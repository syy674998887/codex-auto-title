import json
import multiprocessing
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import support  # 添加被测 hooks 的模块路径。
import codex_rpc
import hook_management as manager
import title_runtime as runtime


def increment(path, times):
    path = Path(path)
    for _ in range(times):
        with runtime.file_lock(path.with_suffix(".lock"), timeout=5):
            count = json.loads(path.read_text())["count"]
            runtime.atomic_json(path, {"count": count + 1})


class RuntimeTests(unittest.TestCase):
    def test_invalid_ids_do_not_alias(self):
        for value in ("a/b", "../abc", "a.b", "", None, 17, "a" * 129):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.session_key(value)
        self.assertNotEqual(runtime.session_key("a-b"), runtime.session_key("ab"))

    def test_replace_failure_preserves_old_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text('{"old": true}')
            with patch.object(runtime.os, "replace", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    runtime.atomic_json(path, {"new": True})
            self.assertEqual(json.loads(path.read_text()), {"old": True})
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_lock_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / "state.lock"
            with self.assertRaises(ValueError):
                with runtime.file_lock(lock):
                    raise ValueError()
            with runtime.file_lock(lock, timeout=0):
                with self.assertRaises(runtime.BusyError):
                    with runtime.file_lock(lock, timeout=0):
                        pass

    @unittest.skipIf(os.name == "nt", "POSIX process regression; Windows remains unverified")
    def test_multiprocess_updates_do_not_lose_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            runtime.atomic_json(path, {"count": 0})
            ctx = multiprocessing.get_context("fork")
            workers = [ctx.Process(target=increment, args=(str(path), 20)) for _ in range(4)]
            try:
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join(timeout=15)
                    self.assertEqual(worker.exitcode, 0)
                self.assertEqual(json.loads(path.read_text())["count"], 80)
            finally:
                for worker in workers:
                    if worker.is_alive():
                        worker.terminate()
                        worker.join()


class RpcTests(unittest.TestCase):
    def server(self, mode="normal", timeout=3):
        return codex_rpc.AppServer(timeout, [sys.executable, "-B",
            str(Path(__file__).with_name("fake_codex.py")), mode])

    def test_unicode_round_trip_and_cleanup(self):
        connection = self.server()
        with connection as server:
            self.assertIsNone(server.read_name("session"))
            server.set_name("session", "修改中文菜单")
            self.assertEqual(server.read_name("session"), "修改中文菜单")
        self.assertIsNotNone(connection.proc.poll())
        self.assertFalse(connection.reader.is_alive())

    def test_handshake_error_is_not_success(self):
        connection = self.server("handshake_error")
        with self.assertRaisesRegex(codex_rpc.RpcError, "denied"):
            with connection:
                self.fail("must not enter")
        self.assertIsNotNone(connection.proc.poll())

    def test_eof_does_not_wait_for_full_timeout(self):
        connection = self.server("eof", timeout=10)
        start = time.monotonic()
        with self.assertRaisesRegex(codex_rpc.RpcError, "退出"):
            with connection:
                pass
        self.assertLess(time.monotonic() - start, 3)

    def test_timeout_reaps_process(self):
        connection = self.server("timeout", timeout=0.15)
        with self.assertRaisesRegex(codex_rpc.RpcError, "超时"):
            with connection:
                pass
        self.assertIsNotNone(connection.proc.poll())
        self.assertFalse(connection.reader.is_alive())

    def test_missing_result_is_not_success(self):
        with self.assertRaisesRegex(codex_rpc.RpcError, "缺少 result"):
            with self.server("missing_result"):
                pass

    def test_bad_thread_response_is_rejected(self):
        server = codex_rpc.AppServer()
        for result in ({}, {"thread": {"id": "other"}},
                       {"thread": {"id": "sid", "name": []}}):
            with patch.object(server, "call", return_value=result):
                with self.assertRaises(codex_rpc.RpcError):
                    server.read_thread("sid")


    def test_optional_name_can_be_absent(self):
        server = codex_rpc.AppServer()
        with patch.object(server, "call", return_value={"thread": {"id": "sid"}}):
            self.assertIsNone(server.read_name("sid"))


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.ours = {"type": "command", "command": "python3 '/renamed/hooks/on-stop.py'",
                     "statusMessage": "codex-auto-title"}
        self.other = {"type": "command", "command": "unrelated --keep"}

    def test_only_own_handler_changes(self):
        doc = {"description": "keep", "hooks": {"Stop": [
            {"matcher": ".*", "extra": 8, "hooks": [self.ours, self.other]},
            {"hooks": [self.other]}], "SessionStart": [{"hooks": [self.other]}]}}
        before = json.dumps(doc)
        new = dict(self.ours, command="new command")
        result = manager.update_document(doc, "codex-auto-title", new)
        self.assertEqual(result["hooks"]["Stop"][0]["hooks"], [new, self.other])
        self.assertEqual(result["hooks"]["Stop"][0]["extra"], 8)
        self.assertEqual(result["hooks"]["Stop"][1], doc["hooks"]["Stop"][1])
        removed = manager.update_document(doc, "codex-auto-title")
        self.assertEqual(removed["hooks"]["Stop"][0]["hooks"], [self.other])
        self.assertEqual(removed["hooks"]["SessionStart"], doc["hooks"]["SessionStart"])
        self.assertEqual(json.dumps(doc), before)

    def test_reinstall_is_idempotent_and_retains_group_positions(self):
        doc = {"hooks": {"Stop": [{"hooks": [self.ours]}, {"hooks": [self.other]},
                                    {"hooks": [self.ours]}]}}
        result = manager.update_document(doc, "codex-auto-title", self.ours)
        self.assertEqual(result["hooks"]["Stop"][1], doc["hooks"]["Stop"][1])
        self.assertEqual(result["hooks"]["Stop"][2]["hooks"], [])
        self.assertEqual(manager.update_document(result, "codex-auto-title", self.ours), result)

    def test_upgrade_and_uninstall_both_events_preserve_other_handlers(self):
        old = {"hooks": {"Stop": [{"hooks": [self.ours, self.other]}],
                         "UserPromptSubmit": [{"hooks": [self.other], "extra": 3}]}}
        new = dict(self.ours, command="updated")
        installed = manager.update_document(old, "codex-auto-title", new)
        self.assertEqual(installed["hooks"]["UserPromptSubmit"],
                         [{"hooks": [self.other], "extra": 3}, {"hooks": [dict(new, timeout=1)]}])
        self.assertEqual(manager.update_document(installed, "codex-auto-title", new), installed)
        removed = manager.update_document(installed, "codex-auto-title")
        self.assertEqual(removed["hooks"]["Stop"], [{"hooks": [self.other]}])
        self.assertEqual(removed["hooks"]["UserPromptSubmit"],
                         [{"hooks": [self.other], "extra": 3}, {"hooks": []}])
        self.assertEqual(manager.update_document(removed, "codex-auto-title"), removed)
        mac = manager.update_document(old, "codex-auto-title-MAC", {"statusMessage": "codex-auto-title-MAC"})
        self.assertEqual(mac["hooks"]["UserPromptSubmit"], old["hooks"]["UserPromptSubmit"])

    def test_twin_and_renamed_clone_identification(self):
        self.assertEqual(manager.identify(self.ours), "codex-auto-title")
        self.assertEqual(manager.identify({"command": "/x/codex-auto-title-MAC/hooks/on-stop.py"}),
                         "codex-auto-title-MAC")
        self.assertEqual(manager.identify({"statusMessage": "codex-auto-title-MAC",
                                          "command": "/renamed/hooks/on-stop.py"}), "codex-auto-title-MAC")

    def test_install_refuses_twin_registration(self):
        other = dict(self.ours, statusMessage="codex-auto-title-MAC")
        with self.assertRaisesRegex(ValueError, "双生"):
            manager.install_preflight({"hooks": {"Stop": [{"hooks": [other]}]}},
                                      "codex-auto-title")

    def test_install_is_offline(self):
        with patch.object(codex_rpc, "codex_bin", side_effect=AssertionError("no CLI query")):
            manager.install_preflight({}, "codex-auto-title")

    def test_invalid_document_is_not_rewritten(self):
        for doc in ([], {"hooks": []}, {"hooks": {"Stop": {}}},
                    {"hooks": {"UserPromptSubmit": [{"hooks": {}}]}},
                    {"hooks": {"Stop": [{"hooks": [None]}]}}):
            with self.assertRaises(ValueError):
                manager.update_document(doc, "codex-auto-title", self.ours)

    def test_custom_home_and_shell_special_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "clone ' $literal `literal`"
            (base / "hooks").mkdir(parents=True)
            (base / "hooks/on-stop.py").write_text('print("safe execution")\n')
            home = Path(folder) / "custom home"
            with patch.object(manager, "codex_home", return_value=home), patch.object(
                    manager, "install_preflight"):
                manager.manage("install", "codex-auto-title", base)
            doc = json.loads((home / "hooks.json").read_text())
            bundled = json.loads((support.HOOKS / "hooks.json").read_text())["hooks"]
            for event in ("UserPromptSubmit", "Stop"):
                plugin_hook = bundled[event][0]["hooks"][0]
                self.assertEqual(doc["hooks"][event][0]["hooks"][0]["timeout"], plugin_hook["timeout"])
                self.assertIn("${PLUGIN_ROOT}", plugin_hook["commandWindows"])
            command = doc["hooks"]["Stop"][0]["hooks"][0]["command"]
            self.assertEqual(shlex.split(command), [sys.executable, "-B", str(base.resolve() / "hooks/on-stop.py")])
            if os.name != "nt":
                result = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=folder)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "safe execution")

    def test_external_edit_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            base, home = Path(folder) / "base", Path(folder) / "home"
            (base / "hooks").mkdir(parents=True)
            (base / "hooks/on-stop.py").write_text("")
            home.mkdir()
            path = home / "hooks.json"
            path.write_text("{}")
            def external_edit(*args):
                path.write_text('{"description": "another agent"}')
            with patch.object(manager, "codex_home", return_value=home), patch.object(
                    manager, "install_preflight", side_effect=external_edit):
                with self.assertRaisesRegex(ValueError, "其他进程"):
                    manager.manage("install", "codex-auto-title", base)
            self.assertEqual(json.loads(path.read_text()), {"description": "another agent"})

    @unittest.skipIf(os.name == "nt", "symlink setup requires Windows privileges")
    def test_hooks_symlink_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home, base = root / "home", root / "base"
            home.mkdir()
            (base / "hooks").mkdir(parents=True)
            (base / "hooks/on-stop.py").write_text("")
            target = root / "dotfiles.json"
            target.write_text("{}")
            (home / "hooks.json").symlink_to(target)
            with patch.object(manager, "codex_home", return_value=home), patch.object(
                    manager, "install_preflight"):
                manager.manage("install", "codex-auto-title", base)
            self.assertTrue((home / "hooks.json").is_symlink())
            self.assertEqual(json.loads(target.read_text())["hooks"]["Stop"][0]["hooks"][0]["statusMessage"],
                             "codex-auto-title")

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import Backend, load_hook
import codex_rpc
import title_runtime


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.hook = load_hook()
        values = {"VAR": root, "STATE": root / "state", "LOG": root / "log.jsonl",
                  "FLAG": root / "ENABLED", "DISABLED_FLAG": root / "DISABLED",
                  "CONFIG": root / "config.json", "USER_CONFIG": None, "IS_PLUGIN": False}
        for key, value in values.items():
            self.hook.__dict__[key] = value
        self.hook.FLAG.touch()
        self.hook.CONFIG.write_text("{}")
        self.backend = Backend()
        for patcher in (patch.object(self.hook, "AppServer", self.backend.server),
                        patch.object(self.hook, "local_name", side_effect=lambda sid: self.backend.name),
                        patch.object(title_runtime, "codex_home", return_value=root / "codex")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.spawn = patch.object(self.hook, "spawn_writer").start()
        self.addCleanup(patch.stopall)
        self.cfg = {"first_turn": True, "every": 10}

    def payload(self, turn="turn1", active=False, message="TITLE: 修改菜单"):
        return {"session_id": "sid", "turn_id": turn, "stop_hook_active": active,
                "hook_event_name": "Stop", "last_assistant_message": message, "cwd": self.temp.name}

    def submit(self, prompt="查看项目", turn="turn1"):
        return self.hook.handle({"hook_event_name": "UserPromptSubmit", "session_id": "sid",
                                 "turn_id": turn, "prompt": prompt}, self.cfg)

    def complete(self, turn):
        self.hook.handle(self.payload(turn, True), self.cfg)
        self.run_latest_writer()

    def run_latest_writer(self):
        sid, job_id = self.spawn.call_args.args
        self.hook.run_writer({"session_id": sid, "job_id": job_id})

    def test_first_submit_reserves_only_a_neutral_title_without_advancing_turn(self):
        self.assertEqual(self.submit("尚未获准的草稿"), "")
        marker = self.hook.RESERVED_NAME
        self.assertEqual(self.backend.name, marker)
        st = self.hook.load_state("sid")
        self.assertEqual((st["turns"], st["last_written"], st["write_job"]), (0, marker, None))
        for path in (self.hook.state_path("sid"), self.hook.LOG):
            self.assertNotIn("尚未获准的草稿", path.read_text())
        with patch.object(self.hook, "AppServer", side_effect=AssertionError("duplicate preview RPC")):
            self.submit("重复提交")
        self.assertTrue(self.hook.handle(self.payload(), self.cfg))
        self.complete("turn1")
        self.assertEqual(self.backend.writes, [marker, "修改菜单"])

    def test_deferred_first_title_updates_preview_at_turn_eleven(self):
        self.cfg["first_turn"] = False
        self.submit("被拒绝的草稿")
        self.submit("查看项目", "1")
        self.assertEqual(self.backend.writes, [self.hook.RESERVED_NAME])
        self.backend.preview = "  查看\n\t项目"
        self.assertEqual(self.hook.handle(self.payload("1"), self.cfg), "")
        self.run_latest_writer()
        for n in range(2, 11):
            self.assertEqual(self.hook.handle(self.payload(str(n)), self.cfg), "")
        self.assertEqual(self.backend.name, "查看 项目")
        self.assertTrue(self.hook.handle(self.payload("11"), self.cfg))
        self.complete("11")
        self.assertEqual(self.backend.name, "修改菜单")

    def test_accepted_preview_stops_at_display_limit(self):
        def prefix():
            yield from "   核对\n\x00资料  "
            yield from "甲" * 40
            self.fail("consumed beyond the preview")
        self.assertEqual(self.hook.accepted_preview(prefix()), ("核对 资料 " + "甲" * 40)[:40])

    def test_invalid_first_title_falls_back_to_the_accepted_prompt(self):
        self.submit("提交阶段的文本")
        self.backend.preview = "已经接受的内容"
        self.assertTrue(self.hook.handle(self.payload(), self.cfg))
        self.hook.handle(self.payload(active=True, message="invalid"), self.cfg)
        self.run_latest_writer()
        self.assertEqual(self.backend.writes, [self.hook.RESERVED_NAME, "已经接受的内容"])

    def test_missed_first_continuation_gets_preview_at_next_normal_stop(self):
        self.submit()
        self.backend.preview = "第一条已接受消息"
        self.assertTrue(self.hook.handle(self.payload(), self.cfg))
        self.assertEqual(self.hook.handle(self.payload("turn2"), self.cfg), "")
        self.run_latest_writer()
        self.assertEqual(self.backend.name, self.backend.preview)

    def test_deferred_preview_cannot_adopt_matching_manual_title(self):
        self.cfg["first_turn"] = False
        self.submit()
        self.backend.preview = "用户设置的标题"
        self.hook.handle(self.payload(), self.cfg)
        self.backend.name = self.backend.preview
        self.run_latest_writer()
        self.assertTrue(self.hook.load_state("sid")["disabled"])
        self.assertEqual(self.backend.writes, [self.hook.RESERVED_NAME])

    def test_timed_out_reservation_is_attempted_once_and_respects_unknown_names(self):
        with patch.object(self.hook, "AppServer", side_effect=codex_rpc.RpcError("timeout")) as server:
            with self.assertRaises(codex_rpc.RpcError):
                self.submit()
            self.submit("再次提交", "turn2")
            server.assert_called_once_with(timeout=self.hook.RESERVATION_TIMEOUT)
        self.backend.name = "外部先保存的名字"
        for n in range(1, 12):
            self.assertEqual(self.hook.handle(self.payload(str(n)), self.cfg), "")
        self.assertFalse(self.hook.load_state("sid")["disabled"])
        self.assertEqual(self.backend.writes, [])

    def test_failed_reservation_allows_normal_naming_if_still_unnamed(self):
        with patch.object(self.hook, "AppServer", side_effect=codex_rpc.RpcError("offline")):
            with self.assertRaises(codex_rpc.RpcError):
                self.submit()
        self.assertTrue(self.hook.handle(self.payload(), self.cfg))
        self.complete("turn1")
        self.assertEqual(self.backend.writes, ["修改菜单"])

    def test_deferred_preview_reconciles_a_lost_reservation_ack(self):
        self.cfg["first_turn"] = False
        self.backend.on_set = lambda: (_ for _ in ()).throw(codex_rpc.RpcError("lost reply"))
        with self.assertRaises(codex_rpc.RpcError):
            self.submit("未确认的输入文本")
        self.backend.on_set = None
        self.backend.preview = "实际获准的文本"
        self.hook.handle(self.payload(), self.cfg)
        self.run_latest_writer()
        self.assertEqual(self.backend.writes, [self.hook.RESERVED_NAME, self.backend.preview])

    def test_inactive_schedule_or_missing_prompt_never_reserves_title(self):
        for prompt in (None, "", " \n ", "\x00"):
            self.assertEqual(self.submit(prompt), "")
        for interval in (0, -1):
            self.cfg.update(first_turn=False, every=interval)
            self.submit()
        self.assertEqual(self.backend.reads, 0)
        self.assertFalse(self.hook.state_path("sid").exists())

    def test_existing_name_identical_to_reservation_is_not_adopted(self):
        self.backend.name = self.hook.RESERVED_NAME
        self.submit()
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(self.hook.handle(self.payload(), self.cfg), "")
        self.assertTrue(self.hook.load_state("sid")["disabled"])

    def test_rename_to_exact_neutral_title_before_first_write_wins(self):
        def rename_after_preflight():
            if self.backend.reads == 2:
                self.backend.name = self.hook.RESERVED_NAME
        self.backend.on_read = rename_after_preflight
        self.submit()
        self.assertEqual(self.backend.writes, [])
        st = self.hook.load_state("sid")
        self.assertEqual((st["disabled"], st["last_written"], st["write_job"]), (True, "", None))

    def test_failed_prewrite_read_discards_unsent_preview_intent(self):
        def fail_recheck():
            if self.backend.reads == 2:
                raise codex_rpc.RpcError("read failed")
        self.backend.on_read = fail_recheck
        with self.assertRaises(codex_rpc.RpcError):
            self.submit()
        self.assertEqual(self.backend.writes, [])
        self.assertIsNone(self.hook.load_state("sid")["write_job"])

    def test_old_unnamed_thread_or_legacy_state_does_not_get_a_prompt_preview(self):
        self.backend.preview = "历史上的第一条请求"
        self.submit("今天继续")
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(self.hook.load_state("sid")["last_written"], "")
        self.hook.save_state("sid", {"turns": 3, "disabled": True, "last_written": "手动命名之前"})
        with patch.object(self.hook, "AppServer", side_effect=AssertionError("legacy state")):
            self.submit()
        self.assertTrue(self.hook.load_state("sid")["disabled"])

    def test_manual_rename_between_preview_and_first_stop_wins(self):
        self.submit()
        self.backend.name = "人工标题"
        self.assertEqual(self.hook.handle(self.payload(), self.cfg), "")
        self.assertTrue(self.hook.load_state("sid")["disabled"])
        self.assertEqual(self.backend.writes, [self.hook.RESERVED_NAME])

    def test_preview_save_failure_or_disable_during_read_prevents_write(self):
        save = self.hook.save_state
        def fail_intent(sid, st):
            if st["write_job"]:
                raise PermissionError()
            save(sid, st)
        with patch.object(self.hook, "save_state", side_effect=fail_intent), self.assertRaises(PermissionError):
            self.submit()
        self.assertEqual(self.backend.writes, [])
        self.hook.state_path("sid").unlink(missing_ok=True)
        self.backend.on_read = self.hook.FLAG.unlink
        self.submit()
        self.assertEqual(self.backend.writes, [])

    def test_lost_preview_response_can_be_reconciled_at_stop(self):
        self.backend.on_set = lambda: (_ for _ in ()).throw(codex_rpc.RpcError("lost reply"))
        with self.assertRaises(codex_rpc.RpcError):
            self.submit()
        self.assertEqual(self.hook.load_state("sid")["last_written"], "")
        self.backend.on_set = None
        self.assertTrue(self.hook.handle(self.payload(), self.cfg))
        self.assertEqual(self.hook.load_state("sid")["last_written"], self.hook.RESERVED_NAME)
        self.complete("turn1")
        self.assertEqual(self.backend.writes, [self.hook.RESERVED_NAME, "修改菜单"])

    def test_schedule_options_and_duplicate_events(self):
        for first, every, expected in ((True, 10, [1, 11, 21]), (False, 10, [11, 21]),
                                        (True, 0, [1]), (False, 0, []), (True, 5, [1, 6, 11, 16, 21])):
            with self.subTest(first=first, every=every):
                state = self.hook.state_path("sid")
                state.unlink(missing_ok=True)
                self.backend.name = None
                self.cfg = {"first_turn": first, "every": every}
                actual = []
                for n in range(1, 22):
                    turn = "turn" + str(n)
                    result = self.hook.handle(self.payload(turn), self.cfg)
                    self.assertEqual(self.hook.handle(self.payload(turn), self.cfg), "")
                    if result:
                        self.assertEqual(json.loads(result)["decision"], "block")
                        actual.append(n)
                        self.complete(turn)
                        self.assertEqual(self.hook.handle(self.payload(turn), self.cfg), "")
                self.assertEqual(actual, expected)
                self.assertEqual(self.hook.load_state("sid")["turns"], 21)

    def test_continuation_consumed_once(self):
        self.hook.handle(self.payload(), self.cfg)
        self.hook.handle(self.payload(active=True), self.cfg)
        self.hook.handle(self.payload(active=True), self.cfg)
        self.spawn.assert_called_once()

    def test_other_hook_continuation_and_stale_pending(self):
        self.assertEqual(self.hook.handle(self.payload(active=True), self.cfg), "")
        self.hook.handle(self.payload(), self.cfg)
        self.hook.handle(self.payload("turn2"), self.cfg)
        self.hook.handle(self.payload(active=True), self.cfg)
        self.spawn.assert_not_called()

    def test_collect_persistence_failure_never_spawns(self):
        self.hook.handle(self.payload(), self.cfg)
        with patch.object(self.hook, "save_state", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.hook.handle(self.payload(active=True), self.cfg)
        self.spawn.assert_not_called()

    def test_initial_persistence_failure_never_queries_model_or_blocks(self):
        with patch.object(self.hook, "save_state", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.hook.handle(self.payload(), self.cfg)
        self.assertEqual(self.backend.reads, 0)
        self.spawn.assert_not_called()

    def test_invalid_config_types_are_rejected(self):
        for data in ({"first_turn": "false"}, {"every": True}, {"every": 1.5}, []):
            self.hook.CONFIG.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                self.hook.config()

    def test_single_line_title_validation(self):
        for text in ("TITLE:\n菜单", "TITLE: 菜单\n补充", "TITLE: !!!", "TITLE: ", {}, "解释 TITLE: 菜单"):
            self.assertEqual(self.hook.extract_title(text), "")
        self.assertEqual(self.hook.extract_title("TITLE： 修改菜单"), "修改菜单")
        self.assertEqual(len(self.hook.extract_title("TITLE: " + "标" * 60)), 40)

    def test_missing_turn_or_invalid_payload_does_not_block(self):
        for extra in ({"turn_id": None}, {"stop_hook_active": "false"}, {"hook_event_name": "SubagentStop"}):
            self.assertEqual(self.hook.handle(dict(self.payload(), **extra), self.cfg), "")

    def test_foreground_does_not_start_app_server(self):
        with patch.object(self.hook, "AppServer", side_effect=AssertionError("foreground RPC")):
            self.assertTrue(self.hook.handle(self.payload(), self.cfg))

    def test_writer_read_error_never_writes(self):
        self.hook.handle(self.payload(), self.cfg)
        self.backend.on_read = lambda: (_ for _ in ()).throw(codex_rpc.RpcError("offline"))
        with self.assertRaises(codex_rpc.RpcError):
            self.complete("turn1")
        self.assertEqual(self.backend.writes, [])
        self.assertFalse(self.hook.load_state("sid")["disabled"])
        self.assertIsNotNone(self.hook.load_state("sid")["write_job"])

    def test_local_miss_still_checks_manual_name_before_write(self):
        self.backend.name = "用户标题"
        with patch.object(self.hook, "local_name", return_value=None):
            self.assertTrue(self.hook.handle(self.payload(), self.cfg))
        self.complete("turn1")
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(self.backend.name, "用户标题")

    def test_existing_manual_name_disables_session(self):
        self.backend.name = "我的标题"
        self.assertEqual(self.hook.handle(self.payload(), self.cfg), "")
        self.assertTrue(self.hook.load_state("sid")["disabled"])

    def test_manual_rename_after_block_is_preserved(self):
        self.hook.handle(self.payload(), self.cfg)
        self.backend.name = "用户刚设置的标题"
        self.complete("turn1")
        self.assertEqual(self.backend.writes, [])
        self.assertTrue(self.hook.load_state("sid")["disabled"])

    def test_new_turn_state_survives_writer_commit(self):
        self.hook.handle(self.payload(), self.cfg)
        def concurrent_turn():
            self.hook.handle(self.payload("turn2"), self.cfg)
        self.backend.on_set = concurrent_turn
        self.complete("turn1")
        state = self.hook.load_state("sid")
        self.assertEqual(state["turns"], 2)
        self.assertEqual(state["seen_turn_id"], "turn2")
        self.assertEqual(state["last_written"], "修改菜单")

    def test_stale_writer_cannot_write(self):
        self.hook.handle(self.payload(), self.cfg)
        self.hook.handle(self.payload(active=True), self.cfg)
        state = self.hook.load_state("sid")
        old_id = state["write_job"]["id"]
        state["write_job"]["id"] = "newer"
        self.hook.save_state("sid", state)
        self.hook.run_writer({"session_id": "sid", "job_id": old_id})
        self.assertEqual(self.backend.writes, [])

    def test_disable_before_writer_prevents_write(self):
        self.hook.handle(self.payload(), self.cfg)
        self.hook.FLAG.unlink()
        self.complete("turn1")
        self.assertEqual(self.backend.writes, [])

    def test_uncertain_rpc_success_is_reconciled(self):
        self.cfg["every"] = 1
        self.hook.handle(self.payload(), self.cfg)
        self.backend.on_set = lambda: (_ for _ in ()).throw(codex_rpc.RpcError("lost reply"))
        with self.assertRaises(codex_rpc.RpcError):
            self.complete("turn1")
        self.backend.on_set = None
        self.assertTrue(self.hook.handle(self.payload("turn2"), self.cfg))
        state = self.hook.load_state("sid")
        self.assertFalse(state["disabled"])
        self.assertEqual(state["last_written"], "修改菜单")

    def test_corrupt_state_is_preserved_and_fails_open(self):
        path = self.hook.state_path("sid")
        path.parent.mkdir()
        path.write_text("broken")
        with self.assertRaises(ValueError):
            self.hook.handle(self.payload(), self.cfg)
        self.assertEqual(path.read_text(), "broken")

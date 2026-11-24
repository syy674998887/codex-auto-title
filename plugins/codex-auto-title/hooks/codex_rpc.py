"""Codex app-server JSON-RPC。标题读取和写入均使用官方接口。"""

import json
import os
import queue
import shutil
import subprocess
import threading
import time

CLIENT_INFO = {"name": "codex-auto-title", "title": "codex-auto-title", "version": "0.1.0"}


class RpcError(RuntimeError):
    pass


def codex_bin():
    explicit = os.environ.get("CODEX_BIN")
    found = shutil.which(explicit or "codex")
    if not found:
        raise RpcError("找不到可执行的 Codex CLI；检查 PATH 或 CODEX_BIN")
    return found


class AppServer:
    """顺序请求、整个连接共享时间预算；argv 仅用于隔离协议测试。"""

    def __init__(self, timeout=25, argv=None):
        self.timeout = timeout
        self.argv = argv
        self.proc = None
        self.reader = None
        self.responses = queue.Queue()
        self.next_id = 0

    def __enter__(self):
        self.deadline = time.monotonic() + self.timeout
        try:
            self.proc = subprocess.Popen(
                self.argv or [codex_bin(), "app-server"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1,
            )
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            self.call("initialize", {"clientInfo": CLIENT_INFO,
                                     "capabilities": {"experimentalApi": True}})
            self._send({"method": "initialized", "params": {}})
            return self
        except Exception as exc:
            self.close()
            if isinstance(exc, RpcError):
                raise
            raise RpcError("app-server 启动失败: %s" % exc) from exc

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        for action, timeout in ((None, 0.2), ("terminate", 0.5), ("kill", 1)):
            try:
                if action and self.proc.poll() is None:
                    getattr(self.proc, action)()
                if action != "kill":
                    timeout = min(timeout, max(0, self.deadline - time.monotonic()))
                self.proc.wait(timeout=timeout)
                break
            except subprocess.TimeoutExpired:
                continue
            except ProcessLookupError:
                self.proc.wait(timeout=1)
                break
        if self.reader:
            self.reader.join(timeout=1)
        if self.proc.stdout and (not self.reader or not self.reader.is_alive()):
            self.proc.stdout.close()

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    message = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(message, dict) and type(message.get("id")) is int:
                    if "method" not in message:
                        self.responses.put(message)
        except (OSError, UnicodeError, ValueError):
            pass
        finally:
            self.responses.put(None)

    def _send(self, message):
        try:
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise RpcError("app-server 连接已关闭") from exc

    def call(self, method, params=None):
        self.next_id += 1
        rid = self.next_id
        if time.monotonic() >= self.deadline:
            raise RpcError("app-server 请求超时: " + method)
        self._send({"method": method, "id": rid, "params": params or {}})
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise RpcError("app-server 请求超时: " + method)
            try:
                response = self.responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise RpcError("app-server 请求超时: " + method) from exc
            if response is None:
                raise RpcError("app-server 在响应前退出: " + method)
            if response["id"] != rid:
                continue
            if "error" in response:
                raise RpcError("%s: %s" % (method, json.dumps(response["error"], ensure_ascii=False)[:300]))
            if "result" not in response:
                raise RpcError("app-server 响应缺少 result: " + method)
            return response["result"]

    def read_thread(self, thread_id):
        result = self.call("thread/read", {"threadId": thread_id, "includeTurns": False})
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise RpcError("thread/read 返回无效的 thread")
        # 本机 0.153.4 生成的 schema：name 是可选的 string/null。
        if thread.get("name") is not None and not isinstance(thread["name"], str):
            raise RpcError("thread/read 返回无效的 name")
        return thread

    def read_name(self, thread_id):
        return self.read_thread(thread_id).get("name") or None

    def set_name(self, thread_id, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("empty title")
        self.call("thread/name/set", {"threadId": thread_id, "name": name})

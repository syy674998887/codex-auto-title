"""本工具拥有的状态与锁；不读写 Codex 内部数据库。"""

import errno
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


class BusyError(RuntimeError):
    pass


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()


def session_key(value):
    # 拒绝非法值，不通过删除字符把两个不同 id 映射到同一个文件。
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid session_id")
    return value


def write_lock_path(session_id):
    # 两个独立项目使用同一个写回锁，避免它们同时 read → name/set。
    return codex_home() / "auto-title" / "locks" / (session_key(session_id) + ".lock")


@contextmanager
def file_lock(path, timeout=0.2):
    """操作系统释放进程锁；崩溃不留下永久占用的锁。解锁后不能删除锁文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                fh.write(b"\0")
                fh.flush()

            def acquire():
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise BusyError("state is busy") from exc
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        yield
    finally:
        try:
            if acquired:
                release()
        finally:
            fh.close()


def atomic_json(path, data):
    """唯一临时文件 + 原子替换；错误传给调用者，不能假装保存成功。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def detach_options():
    if os.name == "nt":
        import subprocess
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}

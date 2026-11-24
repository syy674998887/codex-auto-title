#!/usr/bin/env python3
"""提交时登记中性名称，Stop 后从已接受的对话生成标题。"""

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_rpc import AppServer
from title_runtime import atomic_json, codex_home, detach_options, file_lock, session_key, write_lock_path

BASE = Path(__file__).resolve().parent.parent
plugin_root = os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
IS_PLUGIN = bool(plugin_root) and Path(plugin_root).resolve() == BASE
PLUGIN_DATA = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
VAR = Path(PLUGIN_DATA) if IS_PLUGIN and PLUGIN_DATA else (
    codex_home() / "plugins/data/codex-auto-title" if IS_PLUGIN else BASE / "var")
STATE = VAR / "state"
LOG = VAR / "log.jsonl"
FLAG = VAR / "ENABLED"
DISABLED_FLAG = VAR / "DISABLED"
CONFIG = BASE / "config.json"
USER_CONFIG = VAR / "config.json" if IS_PLUGIN else None
DEFAULTS = {"first_turn": True, "every": 10}
MAX_TITLE_CHARS = 40
RESERVED_NAME = "Codex session"
RESERVATION_TIMEOUT = 0.5
BLOCK_REASON = (
    "请只输出一行，格式严格为 TITLE: <6-12 字概括本次会话正在做的事>。"
    "不要解释、不要补充、不要调用任何工具。"
)


def config():
    cfg = dict(DEFAULTS)
    for path in (CONFIG, USER_CONFIG):
        if path is None:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            continue
        if not isinstance(data, dict):
            raise ValueError("config.json 必须是对象")
        cfg.update({k: data[k] for k in DEFAULTS if k in data})
    if type(cfg["first_turn"]) is not bool or type(cfg["every"]) is not int:
        raise ValueError("first_turn 必须是布尔值，every 必须是整数")
    return cfg


def is_active():
    return not Path(DISABLED_FLAG).exists() if IS_PLUGIN else Path(FLAG).exists()


def log(**kw):
    try:
        Path(VAR).mkdir(parents=True, exist_ok=True)
        kw["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(kw, ensure_ascii=False) + "\n")
    except Exception:
        pass


def state_path(session_id):
    return Path(STATE) / (session_key(session_id) + ".json")


def state_lock(session_id):
    return file_lock(state_path(session_id).with_suffix(".lock"))


def load_state(session_id):
    defaults = {"turns": 0, "seen_turn_id": None, "pending_turn": None,
                "pending_name": None, "last_written": "", "disabled": False, "write_job": None}
    try:
        with state_path(session_id).open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return defaults
    if not isinstance(data, dict):
        raise ValueError("state 必须是对象")
    defaults.update(data)
    if (type(defaults["turns"]) is not int or defaults["turns"] < 0
            or type(defaults["disabled"]) is not bool
            or not isinstance(defaults["last_written"], str)):
        raise ValueError("state 格式无效，请先检查状态文件")
    for key in ("seen_turn_id", "pending_turn", "pending_name"):
        if defaults[key] is not None and not isinstance(defaults[key], str):
            raise ValueError("state %s 格式无效" % key)
    job = defaults["write_job"]
    if job is not None and (not isinstance(job, dict)
            or not all(isinstance(job.get(k), str) and job[k] for k in ("id", "turn_id"))
            or not isinstance(job.get("title"), str)
            or (not job["title"] and job.get("expected_name") != RESERVED_NAME)
            or (job.get("expected_name") is not None and not isinstance(job["expected_name"], str))):
        raise ValueError("state write_job 格式无效")
    # 兼容升级前尚未消费的 pending；旧版没有 pending_name。
    if "pending_name" not in data and defaults["pending_turn"]:
        defaults["pending_name"] = defaults["last_written"] or None
    return defaults


def save_state(session_id, data):
    atomic_json(state_path(session_id), data)


def local_name(session_id):
    # 保留原来的前台快路径：只读本地状态，不启动 app-server。
    from codex_state import explicit_name
    return explicit_name(session_id)


def extract_title(message):
    if not isinstance(message, str):
        return ""
    text = message.strip()
    if len(text.splitlines()) != 1:
        return ""
    match = re.fullmatch(r"TITLE[ \t]*[:：][ \t]*(.+)", text)
    if not match:
        return ""
    title = match.group(1).strip("\"'`“”「」*-—:： ")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
        return ""
    title = re.sub(r" +", " ", title)[:MAX_TITLE_CHARS].rstrip()
    return title if any(ch.isalnum() for ch in title) else ""


def spawn_writer(session_id, job_id):
    child = subprocess.Popen(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--write"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **detach_options(),
    )
    try:
        child.stdin.write(json.dumps({"session_id": session_id, "job_id": job_id}).encode("utf-8"))
    finally:
        child.stdin.close()


def finish_write(sid, job_id, **changes):
    """只结束同一项写入，同时保留其他 Stop 已记下的轮次。"""
    with state_lock(sid):
        st = load_state(sid)
        if st["write_job"] and st["write_job"]["id"] == job_id:
            st.update(write_job=None, **changes)
            save_state(sid, st)


def queue_write(st, turn, title, expected):
    """空标题让后台任务从 Codex 已接受的首条消息生成预览。"""
    job = {"id": uuid.uuid4().hex, "turn_id": turn, "title": title, "expected_name": expected}
    st["write_job"] = job
    return job["id"]


def accepted_preview(text):
    chars = []
    in_space = True
    for ch in text:
        if ch.isspace():
            if not in_space:
                chars.append(" ")
            in_space = True
        else:
            in_space = False
            if ch.isprintable():
                chars.append(ch)
        if len(chars) == MAX_TITLE_CHARS:
            break
    return "".join(chars).strip()


def reserve_name(sid, turn, prompt):
    path = state_path(sid)
    if path.exists():
        return
    if not isinstance(prompt, str) or not any(c.isprintable() and not c.isspace() for c in prompt):
        return
    with file_lock(write_lock_path(sid), timeout=0):
        with state_lock(sid):
            if path.exists() or not is_active():
                return
            # 先记录这一次尝试。握手失败或输入被拒绝后，后续提交直接放行。
            st = load_state(sid)
            st["reservation_attempted"] = True
            save_state(sid, st)
        with AppServer(timeout=RESERVATION_TIMEOUT) as server:
            thread = server.read_thread(sid)
            with state_lock(sid):
                st = load_state(sid)
                if st["turns"] or not is_active():
                    return
                if thread.get("name"):
                    st["disabled"] = True
                    save_state(sid, st)
                    log(event="disabled", why="already named before first input", sid=sid)
                    return
                if thread.get("preview") != "":
                    return
                queue_write(st, turn, RESERVED_NAME, None)
                save_state(sid, st)
            write_title(server, sid, st["write_job"], fresh=True)


def write_title(server, sid, job, *, fresh=False):
    """调用方持有写回锁。新登记的目标不能把外部恰好相同的名字当作自己的。"""
    job_id, target = job["id"], job["title"]
    try:
        current = server.read_name(sid)
    except Exception:
        if fresh:  # 这项写入确定还未发出。
            finish_write(sid, job_id)
        raise
    with state_lock(sid):
        st = load_state(sid)
        pending = st["write_job"]
        if not pending or pending["id"] != job_id:
            return
        if st["disabled"] or not is_active():
            if fresh:
                st["write_job"] = None
                save_state(sid, st)
            return
        if current != job.get("expected_name") and (fresh or current != target):
            st.update(disabled=True, write_job=None)
            save_state(sid, st)
            log(event="disabled", why="renamed before write", sid=sid)
            return
    if current != target:
        server.set_name(sid, target)
    finish_write(sid, job_id, last_written=target)
    if fresh:
        log(event="reserved" if job.get("expected_name") is None else "preview", sid=sid)
    else:
        log(event="named", sid=sid, title=target)


def run_writer(job):
    sid = session_key(job["session_id"])
    job_id = job["job_id"]
    with file_lock(write_lock_path(sid), timeout=2):
        with state_lock(sid):
            st = load_state(sid)
            pending = st["write_job"]
            if not is_active() or st["disabled"] or not pending or pending["id"] != job_id:
                return
        with AppServer() as server:
            preview = not pending["title"]
            if preview:
                # Stop 已发生。只从 Codex 已接受的记录取预览，绝不从提交事件取正文。
                text = server.read_thread(sid).get("preview")
                title = accepted_preview(text) if isinstance(text, str) else ""
                if not title:
                    finish_write(sid, job_id)
                    return
                with state_lock(sid):
                    st = load_state(sid)
                    pending = st["write_job"]
                    if not pending or pending["id"] != job_id:
                        return
                    pending["title"] = title
                    save_state(sid, st)
            write_title(server, sid, pending, fresh=preview)


def handle(payload, cfg):
    sid = session_key(payload.get("session_id"))
    turn = payload.get("turn_id")
    if not isinstance(turn, str) or not turn:
        return ""
    event = payload.get("hook_event_name", "Stop")
    enabled = cfg["first_turn"] or cfg["every"] > 0
    if event == "UserPromptSubmit":
        if enabled:
            reserve_name(sid, turn, payload.get("prompt"))
        return ""
    active = payload.get("stop_hook_active", False)
    if event != "Stop" or type(active) is not bool:
        return ""
    to_spawn = None
    with state_lock(sid):
        st = load_state(sid)
        if st["disabled"]:
            return ""
        if active:
            if st["pending_turn"] != turn:
                return ""
            title = extract_title(payload.get("last_assistant_message"))
            expected = st["pending_name"]
            st.update(pending_turn=None, pending_name=None)
            if title:
                to_spawn = queue_write(st, turn, title, expected)
            elif st["turns"] == 1 and expected == RESERVED_NAME:
                to_spawn = queue_write(st, turn, "", RESERVED_NAME)
            save_state(sid, st)  # 失败直接抛错，不 detach。
            if not title:
                log(event="skip", why="invalid TITLE reply", sid=sid)
        else:
            # 决策和计数都幂等；查询失败后也不在同一轮重复收取模型额度。
            if st["seen_turn_id"] == turn:
                return ""
            missed_first_title = st["turns"] == 1 and st["pending_turn"] is not None
            st.update(turns=st["turns"] + 1, seen_turn_id=turn,
                      pending_turn=None, pending_name=None)
            turns = st["turns"]
            due = ((turns == 1 and cfg["first_turn"]) or
                   (cfg["every"] > 0 and turns > 1 and (turns - 1) % cfg["every"] == 0))
            save_state(sid, st)
            need_preview = enabled and st.get("reservation_attempted") and (turns == 1 or missed_first_title)
            if not (due or need_preview):
                return ""
    if active:
        if to_spawn:
            spawn_writer(sid, to_spawn)
        return ""

    # 写回锁保护查询与状态对账；前台不启动 Codex 子进程。
    with file_lock(write_lock_path(sid), timeout=0):
        current = local_name(sid)
        with state_lock(sid):
            st = load_state(sid)
            if st["seen_turn_id"] != turn or st["disabled"] or not is_active():
                return ""
            previous = st["write_job"]
            if previous and current == previous["title"]:
                st["last_written"] = current
            st["write_job"] = None  # 持有写回锁，旧任务无法越过身份检查。
            # 读不到标题仍继续命名，保留原版取舍；后台写前再经 RPC 复查。
            if current is not None and current != (st["last_written"] or None):
                # 登记未确认时无法判断改名来源，下一周期重新核对。
                unconfirmed = st.get("reservation_attempted") and not st["last_written"]
                st["disabled"] = not unconfirmed
                save_state(sid, st)
                if unconfirmed:
                    log(event="skip", why="name present without confirmed reservation", sid=sid)
                else:
                    log(event="disabled", why="renamed by hand", sid=sid)
                return ""
            if due:
                st.update(pending_turn=turn, pending_name=current or st["last_written"] or None)
            elif st["last_written"] == RESERVED_NAME:
                to_spawn = queue_write(st, turn, "", RESERVED_NAME)
            save_state(sid, st)
    if not due:
        if to_spawn:
            spawn_writer(sid, to_spawn)
        return ""
    log(event="block", sid=sid, turns=st["turns"])
    return json.dumps({"decision": "block", "reason": BLOCK_REASON}, ensure_ascii=False)


def main():
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        if "--write" in sys.argv:
            run_writer(payload)
        elif is_active():
            output = handle(payload, config())
            if output:
                sys.stdout.write(output)
    except Exception as exc:
        # RPC 结果不确定时保留 write_job，下一周期查询服务端结果再恢复。
        log(event="fail", why=str(exc)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())

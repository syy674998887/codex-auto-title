#!/usr/bin/python3
"""读 Codex 自己的状态 —— 只读，绝不写。

写入一律走 codex_rpc.AppServer.set_name（app-server RPC）；前台读取保留本地快路径：
原版开发记录中 spawn 一次约 1 秒（历史样例），而这个判断在每个命名轮次都要做一次，挂在 hook 的关键路径上。
本轮 0.153.4 隔离验收确认下面两种布局均可回读；延迟不作固定承诺。

代价是要自己处理两种存储布局。Codex 的 ThreadHistoryMode 有两种取值，二进制的 SQL
常量池里 `UPDATE threads SET name = ?` 和 `UPDATE threads SET title = ?` 两条并存：

  legacy     覆盖 threads.title，并往 session_index.jsonl 追加一行 thread_name。
             threads.name 在原版 1328 条本机样本中全为 NULL；不是 API name 缺失的证据。
  paginated  以 threads.name 为准。0.171.0 的隔离 TUI 中，title 保留首句，索引也可记录显式名称。

普通 Stop 优先读 threads.name，缺失时再查会话索引。调用方负责与上次写入的名称比对。
"""

import json
import os
import sqlite3

CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
STATE_DB = os.path.join(CODEX_HOME, "state_5.sqlite")
SESSION_INDEX = os.path.join(CODEX_HOME, "session_index.jsonl")


def _name_column(session_id):
    """paginated 模式下标题的落点。legacy 下恒为 None。"""
    if not os.path.exists(STATE_DB):
        return None
    try:
        # 只读打开：Codex 可能正在写（WAL），我们绝不能干扰它。
        conn = sqlite3.connect("file:%s?mode=ro" % STATE_DB, uri=True, timeout=2.0)
        try:
            row = conn.execute("SELECT name FROM threads WHERE id=?", (session_id,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    return row[0] if row and row[0] else None


def _session_index_name(session_id):
    """legacy 模式下标题的落点。追加写，同一 id 可能多行，取最后一行。"""
    if not os.path.exists(SESSION_INDEX):
        return None
    found = None
    try:
        with open(SESSION_INDEX, encoding="utf-8") as fh:
            for line in fh:
                if session_id not in line:
                    continue  # 便宜的预筛，避免对几万行全做 json 解析
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("id") == session_id and entry.get("thread_name"):
                    found = entry["thread_name"]
    except Exception:
        return None
    return found


def explicit_name(session_id):
    """返回 /rename 或 thread/name/set 保存的显式名称，缺失时返回 None。"""
    return _name_column(session_id) or _session_index_name(session_id)

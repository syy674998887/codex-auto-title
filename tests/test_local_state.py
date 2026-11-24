import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support
import codex_state


class LocalStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = root / "state_5.sqlite"
        self.index = root / "session_index.jsonl"
        for patcher in (patch.object(codex_state, "STATE_DB", str(self.db)),
                        patch.object(codex_state, "SESSION_INDEX", str(self.index))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_stores_do_not_create_database(self):
        self.assertIsNone(codex_state.explicit_name("sid"))
        self.assertFalse(self.db.exists())

    def test_database_name_is_used_without_modifying_database(self):
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE threads (id TEXT, name TEXT)")
            db.execute("INSERT INTO threads VALUES (?, ?)", ("sid", "用户标题"))
        before = self.db.read_bytes()
        self.assertEqual(codex_state.explicit_name("sid"), "用户标题")
        self.assertEqual(self.db.read_bytes(), before)

    def test_null_database_name_falls_back_to_latest_index_name(self):
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE threads (id TEXT, name TEXT)")
            db.execute("INSERT INTO threads VALUES (?, NULL)", ("sid",))
        rows = [{"id": "sid", "thread_name": "旧标题"},
                {"id": "other", "thread_name": "其他会话"},
                {"id": "sid", "thread_name": "新标题"}]
        self.index.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        self.assertEqual(codex_state.explicit_name("sid"), "新标题")

    def test_bad_database_still_uses_index(self):
        self.db.write_bytes(b"not sqlite")
        self.index.write_text('broken\n{"id":"sid","thread_name":"保留标题"}\n')
        self.assertEqual(codex_state.explicit_name("sid"), "保留标题")

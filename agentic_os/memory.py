"""Memory layer — persistent SQLite store the agent reads and writes.

Two access paths:
  * the agent calls remember()/recall() as tools mid-conversation
  * the kernel injects a digest of recent memories into every system prompt,
    so the agent wakes up already knowing its recent context
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    topic TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_topic ON memories(topic);
"""


class Memory:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the web server touches memory from handler
        # threads; all access is serialized by the caller (CLI is single-threaded,
        # web.py holds a lock around every kernel/memory operation).
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def remember(self, topic: str, content: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO memories (created_at, topic, content) VALUES (?, ?, ?)",
            (time.time(), topic.strip(), content.strip()),
        )
        self._conn.commit()
        return cur.lastrowid

    def recall(self, query: str, limit: int = 8) -> list[dict]:
        """Keyword recall: a row matches if any query term appears in its
        topic or content. Most recent first."""
        terms = [t.lower() for t in query.split() if len(t) > 2] or [query.lower()]
        clauses = " OR ".join(
            "(lower(topic) LIKE ? OR lower(content) LIKE ?)" for _ in terms
        )
        params: list = []
        for t in terms:
            params += [f"%{t}%", f"%{t}%"]
        rows = self._conn.execute(
            f"SELECT id, created_at, topic, content FROM memories WHERE {clauses} "
            "ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [
            {"id": r[0], "created_at": r[1], "topic": r[2], "content": r[3]}
            for r in rows
        ]

    def recent(self, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, created_at, topic, content FROM memories "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"id": r[0], "created_at": r[1], "topic": r[2], "content": r[3]}
            for r in rows
        ]

    def digest(self, limit: int = 10) -> str:
        """Compact text block for the system prompt."""
        rows = self.recent(limit)
        if not rows:
            return "(no stored memories yet)"
        lines = [f"- [{r['topic']}] {r['content']}" for r in reversed(rows)]
        return "\n".join(lines)

    def close(self) -> None:
        self._conn.close()

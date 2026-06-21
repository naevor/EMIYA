import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from monitor import db


MEMORY_TYPES = {"conversation", "observation", "trigger_event", "user_note", "voice_anchor"}


@dataclass(frozen=True)
class Memory:
    id: int
    timestamp: str
    type: str
    content: str
    mood_snapshot: dict[str, Any]
    importance: float
    tags: list[str]
    role: str | None = None
    turn_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "type": self.type,
            "content": self.content,
            "mood_snapshot": self.mood_snapshot,
            "importance": self.importance,
            "tags": self.tags,
            "role": self.role,
            "turn_id": self.turn_id,
        }


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _zone(value: float | int | None) -> str:
    if value is None:
        return "mid"
    if value < 0.3:
        return "low"
    if value > 0.7:
        return "high"
    return "mid"


def mood_zone(mood: dict[str, Any] | None) -> dict[str, str]:
    mood = mood or {}
    return {
        "energy": _zone(mood.get("energy")),
        "focus": _zone(mood.get("focus")),
        "openness": _zone(mood.get("openness")),
    }


class MemoryStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path

    @property
    def path(self) -> str:
        return self.db_path or db.DB_PATH

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "role" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN role TEXT")
        if "turn_id" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN turn_id TEXT")

    def init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT NOT NULL,
                    type          TEXT NOT NULL,
                    content       TEXT NOT NULL,
                    mood_snapshot TEXT,
                    importance    REAL DEFAULT 0.5,
                    tags          TEXT,
                    role          TEXT,
                    turn_id       TEXT
                )
                """
            )
            self._ensure_columns(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_content ON memories(content)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_role ON memories(role, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_turn ON memories(turn_id)")
            conn.commit()
        finally:
            conn.close()

    def add(
        self,
        memory_type: str,
        content: str,
        mood_snapshot: dict[str, Any] | None = None,
        importance: float = 0.5,
        tags: list[str] | None = None,
        timestamp: str | None = None,
        role: str | None = None,
        turn_id: str | None = None,
    ) -> int:
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"unknown memory type: {memory_type}")
        text = content.strip()
        if not text:
            raise ValueError("memory content cannot be empty")
        clean_role = role.strip().lower() if isinstance(role, str) and role.strip() else None
        clean_turn_id = turn_id.strip() if isinstance(turn_id, str) and turn_id.strip() else None

        self.init_schema()
        importance = max(0.0, min(1.0, float(importance)))
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO memories (
                    timestamp, type, content, mood_snapshot, importance, tags, role, turn_id
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    timestamp or datetime.now().isoformat(timespec="seconds"),
                    memory_type,
                    text,
                    json.dumps(mood_snapshot or {}, ensure_ascii=False),
                    importance,
                    json.dumps(tags or [], ensure_ascii=False),
                    clean_role,
                    clean_turn_id,
                ),
            )
            memory_id = int(cur.lastrowid)
            conn.commit()
            return memory_id
        finally:
            conn.close()

    def get_recent(self, n: int = 20) -> list[Memory]:
        self.init_schema()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?",
                (max(1, int(n)),),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_memory(row) for row in reversed(rows)]

    def get_by_id(self, memory_id: int) -> Memory | None:
        self.init_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?",
                (int(memory_id),),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_memory(row) if row else None

    def get_by_type(self, memory_type: str, limit: int = 20) -> list[Memory]:
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"unknown memory type: {memory_type}")
        self.init_schema()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE type = ?
                ORDER BY importance DESC, id DESC
                LIMIT ?
                """,
                (memory_type, max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_memory(row) for row in rows]

    def search(self, query: str, limit: int = 5) -> list[Memory]:
        text = query.strip()
        if not text:
            return []
        self.init_schema()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE content LIKE ?
                ORDER BY importance DESC, id DESC
                LIMIT ?
                """,
                (f"%{text}%", max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_memory(row) for row in rows]

    def by_mood(self, target_mood: dict[str, Any], limit: int = 5) -> list[Memory]:
        target_zone = mood_zone(target_mood)
        candidates = self.get_recent(500)
        matches = []
        for memory in reversed(candidates):
            memory_zone = mood_zone(memory.mood_snapshot)
            if all(memory_zone[axis] == target_zone[axis] for axis in target_zone):
                matches.append(memory)
            if len(matches) >= limit:
                break
        return matches

    def _row_to_memory(self, row: sqlite3.Row) -> Memory:
        return Memory(
            id=int(row["id"]),
            timestamp=row["timestamp"],
            type=row["type"],
            content=row["content"],
            mood_snapshot=_json_loads(row["mood_snapshot"], {}),
            importance=float(row["importance"] or 0.5),
            tags=_json_loads(row["tags"], []),
            role=row["role"] if "role" in row.keys() else None,
            turn_id=row["turn_id"] if "turn_id" in row.keys() else None,
        )

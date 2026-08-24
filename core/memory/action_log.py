import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ARGS_CAP = 2000
_SUMMARY_CAP = 500
_STATUSES = {
    "ok",
    "error",
    "denied",
    "invalid",
    "unknown_skill",
    "repeat_blocked",
}


@dataclass(frozen=True)
class ActionLogEntry:
    id: int
    run_id: str
    step: int
    ts: float
    skill: str
    args_json: str
    status: str
    result_summary: str | None
    duration_ms: int | None
    undo_hint: str | None

    @property
    def args(self) -> Any:
        return json.loads(self.args_json)


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _capped_args_json(args: dict[str, Any]) -> str:
    encoded = _json_text(args)
    if len(encoded) <= _ARGS_CAP:
        return encoded

    low = 0
    high = min(len(encoded), _ARGS_CAP)
    best = _json_text({"preview": "", "truncated": True})
    while low <= high:
        midpoint = (low + high) // 2
        candidate = _json_text(
            {"preview": encoded[:midpoint], "truncated": True}
        )
        if len(candidate) <= _ARGS_CAP:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _cap_summary(summary: str | None) -> str | None:
    if summary is None:
        return None
    return str(summary)[:_SUMMARY_CAP]


class ActionLogStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS action_log (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id         TEXT NOT NULL,
                    step           INTEGER NOT NULL,
                    ts             REAL NOT NULL,
                    skill          TEXT NOT NULL,
                    args_json      TEXT NOT NULL,
                    status         TEXT NOT NULL,
                    result_summary TEXT,
                    duration_ms    INTEGER,
                    undo_hint      TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_action_log_run
                ON action_log(run_id, step)
                """
            )
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        *,
        run_id: str,
        step: int,
        skill: str,
        args: dict[str, Any],
        status: str,
        result_summary: str | None = None,
        duration_ms: int | None = None,
    ) -> int:
        clean_run_id = str(run_id).strip()
        clean_skill = str(skill).strip()
        if not clean_run_id:
            raise ValueError("run_id cannot be empty")
        if int(step) < 1:
            raise ValueError("step must be positive")
        if not clean_skill:
            raise ValueError("skill cannot be empty")
        if status not in _STATUSES:
            raise ValueError(f"unsupported action status: {status}")

        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO action_log (
                        run_id, step, ts, skill, args_json, status,
                        result_summary, duration_ms, undo_hint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        clean_run_id,
                        int(step),
                        time.time(),
                        clean_skill,
                        _capped_args_json(args),
                        status,
                        _cap_summary(result_summary),
                        None if duration_ms is None else max(0, int(duration_ms)),
                    ),
                )
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def get_run(self, run_id: str) -> list[ActionLogEntry]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM action_log
                WHERE run_id = ?
                ORDER BY step ASC, id ASC
                """,
                (str(run_id),),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> ActionLogEntry:
        return ActionLogEntry(
            id=int(row["id"]),
            run_id=row["run_id"],
            step=int(row["step"]),
            ts=float(row["ts"]),
            skill=row["skill"],
            args_json=row["args_json"],
            status=row["status"],
            result_summary=row["result_summary"],
            duration_ms=row["duration_ms"],
            undo_hint=row["undo_hint"],
        )

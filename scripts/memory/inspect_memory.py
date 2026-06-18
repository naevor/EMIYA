import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "core"
DEFAULT_DB = CORE / "emiya.db"
sys.path.insert(0, str(CORE))

from memory.retriever import DEFAULT_IMPORTANCE_FLOOR, is_prompt_safe_memory  # noqa: E402
from memory.store import MemoryStore  # noqa: E402


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}


def _select_columns(columns: set[str]) -> str:
    role_expr = "role" if "role" in columns else "NULL AS role"
    turn_expr = "turn_id" if "turn_id" in columns else "NULL AS turn_id"
    return (
        "id, timestamp, type, content, mood_snapshot, importance, tags, "
        f"{role_expr}, {turn_expr}"
    )


def _row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _print_counts(conn: sqlite3.Connection, columns: set[str]) -> None:
    if "role" in columns:
        query = """
            SELECT type, COALESCE(role, 'legacy') AS role, COUNT(*) AS count
            FROM memories
            GROUP BY type, role
            ORDER BY type, role
        """
        print("memory counts by type/role:")
        for row in conn.execute(query):
            print(f"  {row['type']}/{row['role']}: {row['count']}")
        return

    print("memory counts by type:")
    for row in conn.execute("SELECT type, COUNT(*) AS count FROM memories GROUP BY type ORDER BY type"):
        print(f"  {row['type']}: {row['count']}")


def _recent(conn: sqlite3.Connection, columns: set[str], limit: int) -> list[dict[str, Any]]:
    selected = _select_columns(columns)
    rows = conn.execute(
        f"SELECT {selected} FROM memories ORDER BY id DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    return [_row_to_memory(row) for row in rows]


def _unsafe(conn: sqlite3.Connection, columns: set[str], importance_floor: float) -> list[dict[str, Any]]:
    selected = _select_columns(columns)
    rows = conn.execute(f"SELECT {selected} FROM memories ORDER BY id DESC").fetchall()
    memories = [_row_to_memory(row) for row in rows]
    return [
        memory
        for memory in memories
        if not is_prompt_safe_memory(memory, importance_floor=importance_floor)
    ]


def _legacy(conn: sqlite3.Connection, columns: set[str]) -> list[dict[str, Any]]:
    selected = _select_columns(columns)
    if "role" not in columns:
        rows = conn.execute(f"SELECT {selected} FROM memories ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT {selected}
            FROM memories
            WHERE role IS NULL OR TRIM(role) = ''
            ORDER BY id DESC
            """
        ).fetchall()
    return [_row_to_memory(row) for row in rows]


def _all_rows(conn: sqlite3.Connection, columns: set[str]) -> list[dict[str, Any]]:
    selected = _select_columns(columns)
    rows = conn.execute(f"SELECT {selected} FROM memories ORDER BY id DESC").fetchall()
    return [_row_to_memory(row) for row in rows]


def _preview(memory: dict[str, Any], width: int = 90) -> str:
    text = " ".join(str(memory.get("content", "")).split())
    if len(text) > width:
        return text[: width - 3] + "..."
    return text


def _downgrade(conn: sqlite3.Connection, ids: list[int], importance: float) -> int:
    if not ids:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        UPDATE memories
        SET importance = CASE
            WHEN importance IS NULL OR importance > ? THEN ?
            ELSE importance
        END
        WHERE id = ?
        """,
        [(importance, importance, memory_id) for memory_id in ids],
    )
    conn.commit()
    return conn.total_changes - before


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect and optionally downgrade unsafe EMIYA memories.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to emiya.db")
    parser.add_argument("--limit", type=int, default=10, help="Number of recent rows to print")
    parser.add_argument("--migrate", action="store_true", help="Run MemoryStore schema migration first")
    parser.add_argument(
        "--importance-floor",
        type=float,
        default=DEFAULT_IMPORTANCE_FLOOR,
        help="Prompt retrieval importance floor used by the safety check",
    )
    parser.add_argument(
        "--downgrade-unsafe",
        action="store_true",
        help="Set unsafe memories to --downgrade-to instead of only reporting them",
    )
    parser.add_argument(
        "--downgrade-legacy",
        action="store_true",
        help="Set legacy rows with no role to --downgrade-to so old persona data stays out of retrieval",
    )
    parser.add_argument(
        "--archive-all",
        action="store_true",
        help="Set every existing memory row to --downgrade-to without deleting it; useful after a persona reset",
    )
    parser.add_argument("--downgrade-to", type=float, default=0.1, help="Importance value for downgraded rows")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 1

    if args.migrate:
        MemoryStore(str(args.db)).init_schema()

    conn = _connect(args.db)
    try:
        columns = _columns(conn)
        if not columns:
            print("memories table not found")
            return 1

        print(f"database: {args.db}")
        print(f"schema: {'role/turn_id' if {'role', 'turn_id'} <= columns else 'legacy'}")
        _print_counts(conn, columns)

        print("\nrecent memories:")
        for memory in _recent(conn, columns, args.limit):
            role = memory.get("role") or "legacy"
            print(
                f"  #{memory['id']} {memory['type']}/{role} "
                f"importance={memory.get('importance')}: {_preview(memory)}"
            )

        unsafe = _unsafe(conn, columns, args.importance_floor)
        print(f"\nunsafe for prompt retrieval: {len(unsafe)}")
        for memory in unsafe[: args.limit]:
            role = memory.get("role") or "legacy"
            print(f"  #{memory['id']} {memory['type']}/{role}: {_preview(memory)}")

        legacy = _legacy(conn, columns)
        print(f"\nlegacy rows: {len(legacy)}")
        for memory in legacy[: args.limit]:
            print(f"  #{memory['id']} {memory['type']}/legacy: {_preview(memory)}")

        if args.downgrade_unsafe:
            changed = _downgrade(conn, [int(memory["id"]) for memory in unsafe], args.downgrade_to)
            print(f"\ndowngraded unsafe rows: {changed}")

        if args.downgrade_legacy:
            changed = _downgrade(conn, [int(memory["id"]) for memory in legacy], args.downgrade_to)
            print(f"downgraded legacy rows: {changed}")

        if args.archive_all:
            all_rows = _all_rows(conn, columns)
            changed = _downgrade(conn, [int(memory["id"]) for memory in all_rows], args.downgrade_to)
            print(f"archived all memory rows: {changed}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

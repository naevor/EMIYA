import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'emiya.db')

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()

    # Sessions.
    c.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            started   TEXT NOT NULL,
            ended     TEXT,
            duration  INTEGER
        )
    ''')

    # Window log.
    c.execute('''
        CREATE TABLE IF NOT EXISTS window_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            app_name   TEXT NOT NULL,
            category   TEXT NOT NULL,
            session_id INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    ''')

    # State log.
    c.execute('''
        CREATE TABLE IF NOT EXISTS state_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            state      TEXT NOT NULL,
            session_id INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    ''')

    # Emiya trigger log.
    c.execute('''
        CREATE TABLE IF NOT EXISTS trigger_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            trigger    TEXT NOT NULL,
            message    TEXT NOT NULL,
            feedback   INTEGER DEFAULT 0,
            session_id INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    ''')

    # Dialogue log and raw model thinking blocks.
    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp      TEXT NOT NULL,
            session_id     INTEGER,
            turn_id        TEXT,
            role           TEXT NOT NULL,
            source         TEXT NOT NULL,
            content        TEXT NOT NULL,
            thought        TEXT,
            raw_response   TEXT,
            model          TEXT,
            trigger        TEXT,
            mood_energy    REAL,
            mood_focus     REAL,
            mood_openness  REAL,
            metadata       TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    ''')

    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_log_session ON chat_log(session_id, id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_log_turn ON chat_log(turn_id)")

    conn.commit()
    conn.close()
    print("[DB] initialized")

def start_session():
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO sessions (started) VALUES (?)", (now,))
    session_id = c.lastrowid
    conn.commit()
    conn.close()
    print(f"[DB] session #{session_id} started")
    return session_id


def close_stale_sessions():
    """Close sessions left open by an interrupted previous server process."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, started FROM sessions WHERE ended IS NULL ORDER BY id"
        ).fetchall()
        changed = 0
        for row in rows:
            session_id = int(row["id"])
            event_timestamps = []
            for table in ("window_log", "state_log", "trigger_log", "chat_log"):
                event = conn.execute(
                    f"SELECT MAX(timestamp) AS timestamp FROM {table} WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if event and event["timestamp"]:
                    event_timestamps.append(event["timestamp"])

            ended = max(event_timestamps, default=row["started"])
            try:
                duration = max(
                    0,
                    int(
                        (
                            datetime.fromisoformat(ended)
                            - datetime.fromisoformat(row["started"])
                        ).total_seconds()
                    ),
                )
            except (TypeError, ValueError):
                ended = row["started"]
                duration = 0

            conn.execute(
                "UPDATE sessions SET ended = ?, duration = ? WHERE id = ? AND ended IS NULL",
                (ended, duration, session_id),
            )
            changed += 1

        conn.commit()
        return changed
    finally:
        conn.close()

def end_session(session_id):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(
        """
        UPDATE sessions
        SET ended=?, duration=(strftime('%s',?) - strftime('%s',started))
        WHERE id=? AND ended IS NULL
        """,
        (now, now, session_id)
    )
    conn.commit()
    conn.close()
    print(f"[DB] session #{session_id} ended")

def log_window(app_name, category, session_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO window_log (timestamp, app_name, category, session_id) VALUES (?,?,?,?)",
        (datetime.now().isoformat(), app_name, category, session_id)
    )
    conn.commit()
    conn.close()

def log_state(state, session_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO state_log (timestamp, state, session_id) VALUES (?,?,?)",
        (datetime.now().isoformat(), state, session_id)
    )
    conn.commit()
    conn.close()

def log_trigger(trigger, message, session_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO trigger_log (timestamp, trigger, message, session_id) VALUES (?,?,?,?)",
        (datetime.now().isoformat(), trigger, message, session_id)
    )
    conn.commit()
    conn.close()

def log_chat_message(
    session_id,
    role,
    content,
    source,
    turn_id=None,
    thought=None,
    raw_response=None,
    model=None,
    trigger=None,
    mood=None,
    metadata=None,
):
    mood = mood or {}
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

    conn = get_connection()
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO chat_log (
            timestamp, session_id, turn_id, role, source, content,
            thought, raw_response, model, trigger,
            mood_energy, mood_focus, mood_openness, metadata
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''',
        (
            datetime.now().isoformat(),
            session_id,
            turn_id,
            role,
            source,
            content,
            thought,
            raw_response,
            model,
            trigger,
            mood.get("energy"),
            mood.get("focus"),
            mood.get("openness"),
            metadata_json,
        )
    )
    conn.commit()
    conn.close()


def get_chat_log(limit=100):
    """Return recent dialogue rows in chronological order for the UI."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM chat_log ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    finally:
        conn.close()

    entries = []
    for row in reversed(rows):
        metadata = None
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except json.JSONDecodeError:
                metadata = None
        entries.append(
            {
                "id": int(row["id"]),
                "timestamp": row["timestamp"],
                "session_id": row["session_id"],
                "turn_id": row["turn_id"],
                "role": row["role"],
                "source": row["source"],
                "content": row["content"],
                "thought": row["thought"],
                "raw_response": row["raw_response"],
                "model": row["model"],
                "trigger": row["trigger"],
                "mood": {
                    "energy": row["mood_energy"],
                    "focus": row["mood_focus"],
                    "openness": row["mood_openness"],
                },
                "metadata": metadata,
            }
        )
    return entries

if __name__ == "__main__":
    init_db()
    sid = start_session()
    log_window("VS Code", "code", sid)
    log_state("deep_work", sid)
    log_trigger("grinding", "three hours. what is holding you here?", sid)
    log_chat_message(sid, "user", "are you here?", "user", turn_id="demo")
    log_chat_message(
        sid,
        "assistant",
        "here.",
        "l1",
        turn_id="demo",
        thought="shorter is better.",
        model="qwen3:14b",
    )
    print("[DB] test passed")

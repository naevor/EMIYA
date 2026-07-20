import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from models.response_utils import split_thinking
from monitor import db


class ChatLogTests(unittest.TestCase):
    def test_split_thinking_returns_visible_text_and_thought(self):
        visible, thought = split_thinking("<think>hidden reasoning</think>visible answer")

        self.assertEqual(visible, "visible answer")
        self.assertEqual(thought, "hidden reasoning")

    def test_log_chat_message_stores_thought_and_mood_snapshot(self):
        original_path = db.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db.DB_PATH = str(Path(tmp) / "emiya-test.db")
                db.init_db()
                session_id = db.start_session()
                db.log_chat_message(
                    session_id=session_id,
                    role="assistant",
                    content="visible answer",
                    source="l1",
                    turn_id="turn-1",
                    thought="hidden reasoning",
                    raw_response="<think>hidden reasoning</think>visible answer",
                    model="qwen3:14b",
                    mood={"energy": 0.2, "focus": 0.8, "openness": 0.4},
                )

                conn = sqlite3.connect(db.DB_PATH)
                try:
                    row = conn.execute(
                        """
                        SELECT role, source, content, thought, model,
                               mood_energy, mood_focus, mood_openness
                        FROM chat_log
                        """
                    ).fetchone()
                finally:
                    conn.close()

                self.assertEqual(row[0], "assistant")
                self.assertEqual(row[1], "l1")
                self.assertEqual(row[2], "visible answer")
                self.assertEqual(row[3], "hidden reasoning")
                self.assertEqual(row[4], "qwen3:14b")
                self.assertEqual(row[5], 0.2)
                self.assertEqual(row[6], 0.8)
                self.assertEqual(row[7], 0.4)

                entries = db.get_chat_log(limit=10)
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["source"], "l1")
                self.assertEqual(entries[0]["thought"], "hidden reasoning")
                self.assertEqual(entries[0]["mood"]["focus"], 0.8)
        finally:
            db.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()

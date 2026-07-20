import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import server
from monitor import db
from monitor.session_tracker import AFK_THRESHOLD, SessionTracker
from monitor.trigger_engine import TriggerEngine
from monitor.window_tracker import get_app_time, get_switch_count


class MonitorCorrectnessTests(unittest.TestCase):
    def test_afk_stops_active_time_and_emits_return_transition(self):
        start = datetime(2026, 1, 1, 12, 0, 0)
        tracker = SessionTracker(session_id=1, idle_seconds_provider=lambda: 0)
        tracker.session_start = start
        tracker.last_active = start

        transition = tracker.poll_activity(
            idle_seconds=AFK_THRESHOLD,
            now=start + timedelta(minutes=10),
        )

        self.assertEqual(transition, "afk")
        self.assertTrue(tracker.is_afk)
        self.assertEqual(tracker.get_active_duration(now=start + timedelta(minutes=10)), 5.0)

        transition = tracker.poll_activity(
            idle_seconds=0,
            now=start + timedelta(minutes=11),
        )

        self.assertEqual(transition, "afk_return")
        self.assertFalse(tracker.is_afk)
        self.assertEqual(tracker.get_active_duration(now=start + timedelta(minutes=11)), 5.0)
        self.assertEqual(tracker.get_active_duration(now=start + timedelta(minutes=12)), 6.0)

    def test_window_queries_respect_requested_time_window(self):
        original_path = db.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db.DB_PATH = str(Path(tmp) / "monitor.db")
                db.init_db()
                session_id = db.start_session()
                now = datetime.now()
                rows = [
                    (now - timedelta(minutes=40), "old.exe"),
                    (now - timedelta(minutes=20), "mid.exe"),
                    (now - timedelta(minutes=8), "code.exe"),
                    (now - timedelta(minutes=4), "browser.exe"),
                    (now - timedelta(minutes=1), "code.exe"),
                ]
                conn = db.get_connection()
                try:
                    conn.executemany(
                        """
                        INSERT INTO window_log (timestamp, app_name, category, session_id)
                        VALUES (?,?,?,?)
                        """,
                        [
                            (timestamp.isoformat(), app, "test", session_id)
                            for timestamp, app in rows
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                apps = get_app_time(session_id, minutes=30)
                switches = get_switch_count(session_id, minutes=10)

                self.assertEqual({app["app"] for app in apps}, {"mid.exe", "code.exe", "browser.exe"})
                self.assertEqual(switches, 2)
        finally:
            db.DB_PATH = original_path

    def test_stale_sessions_are_closed_from_their_last_event(self):
        original_path = db.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db.DB_PATH = str(Path(tmp) / "monitor.db")
                db.init_db()
                session_id = db.start_session()
                started = datetime.now() - timedelta(minutes=10)
                last_event = datetime.now() - timedelta(minutes=2)
                conn = db.get_connection()
                try:
                    conn.execute(
                        "UPDATE sessions SET started = ? WHERE id = ?",
                        (started.isoformat(), session_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO window_log (timestamp, app_name, category, session_id)
                        VALUES (?,?,?,?)
                        """,
                        (last_event.isoformat(), "code.exe", "code", session_id),
                    )
                    conn.commit()
                finally:
                    conn.close()

                self.assertEqual(db.close_stale_sessions(), 1)

                conn = sqlite3.connect(db.DB_PATH)
                try:
                    ended, duration = conn.execute(
                        "SELECT ended, duration FROM sessions WHERE id = ?",
                        (session_id,),
                    ).fetchone()
                finally:
                    conn.close()

                self.assertEqual(ended, last_event.isoformat())
                self.assertGreaterEqual(duration, 470)
                self.assertLessEqual(duration, 490)
        finally:
            db.DB_PATH = original_path

    def test_server_shutdown_is_idempotent_and_closes_runtime(self):
        instance = server.EmiyaServer.__new__(server.EmiyaServer)
        instance._shutdown_complete = False
        instance.session_id = 42
        instance.window_tracker = Mock()
        instance.system_tracker = Mock()
        instance.mood_engine = Mock()

        with patch.object(server, "end_session") as end_session:
            instance.shutdown()
            instance.shutdown()

        instance.window_tracker.stop.assert_called_once_with()
        instance.system_tracker.stop.assert_called_once_with()
        instance.mood_engine.stop.assert_called_once_with()
        end_session.assert_called_once_with(42)

    def test_afk_return_reaches_trigger_engine_during_general_cooldown(self):
        callback = Mock()
        engine = TriggerEngine(session_id=1, on_trigger=callback)
        engine._last_fired_at = datetime.now()

        with (
            patch.object(engine, "_generate_message", return_value={"content": "back."}),
            patch("monitor.trigger_engine.log_trigger"),
            patch("monitor.trigger_engine.log_chat_message"),
        ):
            result = engine.check(
                {"normal", "afk_return"},
                {"active_minutes": 5, "apps": [], "traits": {}},
                mood={"energy": 0.5, "focus": 0.5, "openness": 0.5},
            )

        self.assertEqual(result, ("afk_return", "back."))
        callback.assert_called_once_with(
            "afk_return",
            "back.",
            {"content": "back."},
        )

    def test_trigger_engine_resets_daily_state_after_midnight(self):
        engine = TriggerEngine(session_id=1)
        engine.fired_today.add("grinding")

        engine._reset_if_new_day(datetime.now() + timedelta(days=1))

        self.assertEqual(engine.fired_today, set())


if __name__ == "__main__":
    unittest.main()

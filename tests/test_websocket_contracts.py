import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import server
from monitor import db
from monitor.trigger_engine import TriggerEngine


class WebSocketContractTests(unittest.TestCase):
    def test_chat_log_packet_contains_persisted_entries(self):
        original_path = db.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db.DB_PATH = str(Path(tmp) / "chat.db")
                db.init_db()
                session_id = db.start_session()
                db.log_chat_message(
                    session_id=session_id,
                    role="assistant",
                    content="back.",
                    source="fallback_trigger",
                    trigger="afk_return",
                )

                packet = server.EmiyaServer.build_chat_log_packet()

                self.assertEqual(packet["type"], "chat_log_update")
                self.assertEqual(packet["entries"][0]["source"], "fallback_trigger")
                self.assertEqual(packet["entries"][0]["trigger"], "afk_return")
        finally:
            db.DB_PATH = original_path

    def test_state_packet_reports_runtime_statuses_and_influence(self):
        instance = server.EmiyaServer.__new__(server.EmiyaServer)
        instance.session_tracker = Mock()
        instance.session_tracker.get_stats.return_value = {
            "time_of_day": "day",
            "active_minutes": 12.5,
            "is_afk": False,
        }
        instance._current_states = {"deep_work"}
        instance._current_apps = []
        instance._started_at = "2026-01-01T12:00:00"
        instance._started_monotonic = 100.0
        instance.last_sys = {}
        instance.trigger_engine = SimpleNamespace(model_status="error")
        instance._l1_status = "active"
        instance.memory_writes_enabled = True
        instance.pending_message = {
            "trigger": "afk_return",
            "message": "back.",
            "source": "fallback_trigger",
            "model": None,
            "thought": None,
        }
        instance.traits = Mock()
        instance.traits.to_dict.return_value = {"warmth": 40}
        instance.personality_presets = {"default": {}}
        instance.mood_influence = deque(
            [
                {
                    "source": "deep_work",
                    "axis": "y",
                    "delta": 1.5,
                    "timestamp": "now",
                }
            ]
        )
        instance.mood_engine = Mock()
        instance.mood_engine.get_state.return_value = SimpleNamespace(
            sigma=10.0,
            rho=28.0,
            beta=8.0 / 3.0,
            x=1.0,
            y=2.0,
            z=3.0,
            raw_x=1.0,
            raw_y=2.0,
            raw_z=3.0,
            energy=0.4,
            focus=0.6,
            openness=0.5,
            timestamp="now",
            trail=[],
        )

        with patch("server.time.monotonic", return_value=161.0):
            packet = instance.build_state_packet()

        self.assertEqual(
            packet["models"],
            {"L-meta": "inactive", "L0": "error", "L1": "active", "L2": "inactive"},
        )
        self.assertEqual(packet["emiya"]["source"], "fallback_trigger")
        self.assertEqual(packet["influence"][0]["source"], "deep_work")
        self.assertEqual(packet["sys"]["uptime"], "00:01:01")
        self.assertEqual(packet["sys"]["started_at"], "2026-01-01T12:00:00")

    def test_l0_failure_is_reported_as_fallback_not_as_model_output(self):
        engine = TriggerEngine(session_id=1)
        engine._l0 = Mock(return_value=None)

        with patch("monitor.trigger_engine.get_fallback", return_value="fallback line"):
            payload = engine._generate_message("afk_return", {})

        self.assertEqual(payload["source"], "fallback_trigger")
        self.assertEqual(payload["content"], "fallback line")
        self.assertEqual(engine.model_status, "error")

    def test_l0_success_returns_to_standby(self):
        engine = TriggerEngine(session_id=1)
        engine._l0 = Mock(
            return_value={
                "content": "model line",
                "model": "qwen3:4b",
                "thought": None,
                "raw_response": "model line",
            }
        )

        payload = engine._generate_message("afk_return", {})

        self.assertEqual(payload["source"], "l0_trigger")
        self.assertEqual(payload["model"], "qwen3:4b")
        self.assertEqual(engine.model_status, "standby")

    def test_state_nudge_is_recorded_as_mood_influence(self):
        instance = server.EmiyaServer.__new__(server.EmiyaServer)
        instance._last_states = set()
        instance.mood_engine = Mock()
        instance.memory_writer = Mock()
        instance.mood_influence = deque(maxlen=50)
        instance._mood_context = Mock(
            return_value={"energy": 0.5, "focus": 0.5, "openness": 0.5}
        )

        instance.apply_mood_nudges({"deep_work"})

        instance.mood_engine.nudge.assert_called_once_with("y", 1.5)
        self.assertEqual(instance.mood_influence[0]["source"], "deep_work")
        self.assertEqual(instance.mood_influence[0]["delta"], 1.5)


if __name__ == "__main__":
    unittest.main()

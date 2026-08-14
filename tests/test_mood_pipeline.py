import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from models import l0, l1
from monitor.state_modifiers import states_to_activity_hints
from mood.engine import MoodEngine
from mood.modifiers import mood_from_mapping, mood_seed, mood_to_model_options, mood_to_prompt_fragment


class FakeResponse:
    status_code = 200

    def json(self):
        return {"message": {"content": "quiet."}}


class MoodPipelineTests(unittest.TestCase):
    def test_mood_seed_is_stable_and_changes_with_mood(self):
        low = mood_from_mapping({"energy": 0.2, "focus": 0.8, "openness": 0.1})
        same_low = mood_from_mapping({"energy": 0.2, "focus": 0.8, "openness": 0.1})
        high = mood_from_mapping({"energy": 0.8, "focus": 0.2, "openness": 0.9})

        self.assertEqual(mood_seed(low), mood_seed(same_low))
        self.assertNotEqual(mood_seed(low), mood_seed(high))

    def test_mood_model_options_preserve_base_options_and_add_seed(self):
        mood = mood_from_mapping({"energy": 0.34, "focus": 0.81, "openness": 0.22})
        options = mood_to_model_options(mood, {"temperature": 0.8, "num_predict": 100})

        self.assertEqual(options["temperature"], 0.8)
        self.assertEqual(options["num_predict"], 100)
        self.assertEqual(options["seed"], mood_seed(mood))

    def test_mood_prompt_does_not_echo_forbidden_state_language(self):
        mood = mood_from_mapping({"energy": 0.9, "focus": 0.1, "openness": 0.9})
        fragment = mood_to_prompt_fragment(mood)

        self.assertIn("loose focus", fragment)
        self.assertNotIn("scattered", fragment)
        self.assertNotIn("state:", fragment)

    def test_l0_prompt_and_request_options_are_regenerated_from_current_mood(self):
        low_mood = {"energy": 0.2, "focus": 0.8, "openness": 0.1}
        high_mood = {"energy": 0.8, "focus": 0.2, "openness": 0.9}

        low_system = l0._build_system(low_mood)
        high_system = l0._build_system(high_mood)

        self.assertTrue(low_system.startswith("<mood>"))
        self.assertIn("energy: 0.20", low_system)
        self.assertIn("energy: 0.80", high_system)
        self.assertNotEqual(low_system, high_system)

        payloads = []

        def fake_post(url, json, timeout):
            payloads.append(json)
            return FakeResponse()

        with patch.object(l0.requests, "post", side_effect=fake_post):
            l0.generate("first_start", {"hour": 10, "apps": [], "mood": low_mood})
            l0.generate("first_start", {"hour": 10, "apps": [], "mood": high_mood})

        self.assertEqual(payloads[0]["options"]["seed"], mood_seed(mood_from_mapping(low_mood)))
        self.assertEqual(payloads[1]["options"]["seed"], mood_seed(mood_from_mapping(high_mood)))
        self.assertNotEqual(payloads[0]["options"]["seed"], payloads[1]["options"]["seed"])
        self.assertEqual(payloads[0]["model"], "qwen3:4b-instruct-2507-q4_K_M")
        self.assertEqual(payloads[0]["options"]["num_ctx"], 4096)
        self.assertFalse(payloads[0]["think"])
        self.assertEqual(payloads[0]["keep_alive"], "2m")

    def test_l1_request_options_are_regenerated_from_current_mood(self):
        low_context = {
            "active_min": 10,
            "apps": [],
            "states": ["normal"],
            "mood": {"energy": 0.2, "focus": 0.8, "openness": 0.1},
        }
        high_context = {
            "active_min": 10,
            "apps": [],
            "states": ["normal"],
            "mood": {"energy": 0.8, "focus": 0.2, "openness": 0.9},
        }
        payloads = []

        def fake_post(url, json, timeout):
            payloads.append(json)
            return FakeResponse()

        with patch.object(l1.requests, "post", side_effect=fake_post):
            l1.chat([{"role": "user", "content": "are you here?"}], low_context)
            l1.chat([{"role": "user", "content": "are you here?"}], high_context)

        self.assertIn("energy: 0.20", payloads[0]["messages"][0]["content"])
        self.assertIn("energy: 0.80", payloads[1]["messages"][0]["content"])
        self.assertEqual(
            payloads[0]["options"]["seed"],
            mood_seed(mood_from_mapping(low_context["mood"])),
        )
        self.assertEqual(
            payloads[1]["options"]["seed"],
            mood_seed(mood_from_mapping(high_context["mood"])),
        )
        self.assertNotEqual(payloads[0]["options"]["seed"], payloads[1]["options"]["seed"])

    def test_l1_factual_queries_do_not_pull_history_or_memory_context(self):
        context = {
            "active_min": 10,
            "apps": [{"app": "code.exe"}],
            "activity_hints": states_to_activity_hints(["normal"]),
            "mood": {"energy": 0.5, "focus": 0.5, "openness": 0.5},
            "recent_memory": [
                {
                    "timestamp": "now",
                    "type": "conversation",
                    "content": "user: sqlite?\nemiya: memory layer needs relational stability.",
                    "importance": 0.5,
                }
            ],
            "relevant_memory": [
                {
                    "timestamp": "now",
                    "type": "conversation",
                    "content": "user: postgres?\nemiya: memory layer again.",
                    "importance": 0.5,
                }
            ],
            "voice_anchors": [
                {
                    "timestamp": "now",
                    "type": "voice_anchor",
                    "role": "assistant",
                    "content": "approved voice must not steer a factual answer.",
                    "importance": 0.9,
                }
            ],
        }
        messages = [
            {"role": "user", "content": "sqlite or postgres for emiya's memory layer?"},
            {"role": "assistant", "content": "Postgres. If the memory layer needs to connect."},
            {"role": "user", "content": "what do you know about project Artemis?"},
        ]
        payloads = []

        def fake_post(url, json, timeout):
            payloads.append(json)
            return FakeResponse()

        with patch.object(l1.requests, "post", side_effect=fake_post):
            l1.chat(messages, context)

        system = payloads[0]["messages"][0]["content"]
        prompt_messages = payloads[0]["messages"][1:]

        self.assertIn("<task_mode>", system)
        self.assertIn("factual question", system)
        self.assertNotIn("memory layer needs relational stability", system)
        self.assertNotIn("approved voice must not steer", system)
        self.assertEqual(prompt_messages, [{"role": "user", "content": "what do you know about project Artemis?"}])

    def test_l1_nonfactual_turn_injects_approved_voice_anchors(self):
        context = {
            "active_min": 10,
            "apps": [{"app": "code.exe"}],
            "activity_hints": states_to_activity_hints(["normal"]),
            "mood": {"energy": 0.5, "focus": 0.5, "openness": 0.5},
            "recent_memory": [],
            "relevant_memory": [],
            "voice_anchors": [
                {
                    "timestamp": "now",
                    "type": "voice_anchor",
                    "role": "assistant",
                    "content": "enough to test. not enough to trust.",
                    "importance": 0.9,
                }
            ],
        }
        payloads = []

        def fake_post(url, json, timeout):
            payloads.append(json)
            return FakeResponse()

        with patch.object(l1.requests, "post", side_effect=fake_post):
            l1.chat([{"role": "user", "content": "are you here?"}], context)

        system = payloads[0]["messages"][0]["content"]
        self.assertIn("<voice_anchors>", system)
        self.assertIn("enough to test. not enough to trust.", system)
        self.assertIn("not their factual content", system)

    def test_l1_proper_name_factual_followups_do_not_pull_memory_context(self):
        context = {
            "active_min": 10,
            "apps": [{"app": "code.exe"}],
            "activity_hints": states_to_activity_hints(["normal"]),
            "mood": {"energy": 0.5, "focus": 0.5, "openness": 0.5},
            "recent_memory": [
                {
                    "timestamp": "now",
                    "type": "conversation",
                    "content": "user: sqlite?\nemiya: memory layer needs relational stability.",
                    "importance": 0.5,
                }
            ],
            "relevant_memory": [],
        }
        messages = [
            {"role": "user", "content": "sqlite or postgres for emiya's memory layer?"},
            {"role": "assistant", "content": "Postgres. If the memory layer needs to connect."},
            {"role": "user", "content": "okay what about Artemis?"},
        ]
        payloads = []

        def fake_post(url, json, timeout):
            payloads.append(json)
            return FakeResponse()

        with patch.object(l1.requests, "post", side_effect=fake_post):
            l1.chat(messages, context)

        system = payloads[0]["messages"][0]["content"]
        prompt_messages = payloads[0]["messages"][1:]

        self.assertIn("<task_mode>", system)
        self.assertNotIn("memory layer needs relational stability", system)
        self.assertEqual(prompt_messages, [{"role": "user", "content": "okay what about Artemis?"}])

    def test_l1_runtime_context_does_not_duplicate_raw_mood_values(self):
        context = {
            "active_min": 10,
            "apps": [{"app": "code.exe"}],
            "activity_hints": states_to_activity_hints(["normal"]),
            "mood": {"energy": 0.34, "focus": 0.81, "openness": 0.22},
        }

        runtime = l1._build_runtime_context(context)
        system = l1._build_system(context)

        self.assertNotIn("<mood_values>", runtime)
        self.assertNotIn("<energy>0.34</energy>", runtime)
        self.assertIn("energy: 0.34", system)

    def test_l1_runtime_context_uses_activity_hints_instead_of_state_labels(self):
        context = {
            "active_min": 10,
            "apps": [{"app": "code.exe"}],
            "states": ["scattered", "idle_loop"],
            "activity_hints": states_to_activity_hints(["scattered", "idle_loop"]),
            "mood": {"energy": 0.5, "focus": 0.5, "openness": 0.5},
        }

        runtime = l1._build_runtime_context(context)

        self.assertIn("<activity_hints>", runtime)
        self.assertIn("he keeps switching windows", runtime)
        self.assertIn("he keeps circling the same windows", runtime)
        self.assertNotIn("<states>", runtime)
        self.assertNotIn("scattered", runtime)
        self.assertNotIn("idle_loop", runtime)

    def test_activity_hints_do_not_echo_monitor_state_labels(self):
        hints = states_to_activity_hints(["scattered", "grinding", "idle_loop", "normal"])
        joined = " ".join(hints)

        self.assertIn("switching windows", joined)
        for raw_label in ("scattered", "grinding", "idle_loop", "normal"):
            self.assertNotIn(raw_label, joined)

    def test_l1_clean_strips_chat_template_tokens(self):
        self.assertEqual(l1._clean("neutral. observing. <|im_end|> trailing"), "neutral. observing.")
        self.assertEqual(l1._clean("i'm here.<|eot_id|>"), "i'm here.")
        self.assertEqual(l1._clean("emiya. |&lt;im_end|&gt;\n```python\nbad()"), "emiya.")
        self.assertEqual(l1._clean("emiya.\nThis AI model explains itself."), "emiya.")

    def test_l0_clean_strips_chat_template_tokens(self):
        self.assertEqual(l0._clean("same windows again. stuck? <|im_end|>"), "same windows again. stuck?")
        self.assertEqual(l0._clean("emiya: first. second. third."), "first. second.")

    def test_mood_engine_logs_initial_and_interval_ticks(self):
        engine = MoodEngine(log_interval_ticks=2)

        with patch("builtins.print") as mocked_print:
            engine._tick()
            engine._tick()
            engine._tick()

        lines = [call.args[0] for call in mocked_print.call_args_list]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith("[Mood] t=") for line in lines))
        self.assertIn("e=", lines[0])
        self.assertIn("params=", lines[0])


if __name__ == "__main__":
    unittest.main()

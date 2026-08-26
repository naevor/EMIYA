import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from models import l1


class FakeResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"message": {"content": "grounded. still mine."}}


class VoiceFinalizeTests(unittest.TestCase):
    def test_prompt_requires_preservation_of_material_facts(self):
        prompt = l1._voice_finalize_prompt(
            "where is it?",
            "file: core/server.py; line: 213; error: denied",
            'fs.read({"path":"core/server.py"})',
        )

        self.assertIn("Preserve every material fact", prompt)
        self.assertIn("paths, filenames, error messages", prompt)
        self.assertIn("line references, values", prompt)
        self.assertIn("core/server.py", prompt)
        self.assertIn("line: 213", prompt)

    def test_finalize_uses_only_mood_traits_and_bounded_agent_data(self):
        captured = {}

        def build_system(context):
            captured["context"] = context
            return "voice system"

        def post(_url, json, timeout):
            captured["payload"] = json
            captured["timeout"] = timeout
            return FakeResponse()

        mood = {"energy": 0.2, "focus": 0.8, "openness": 0.4}
        traits = {"warmth": 40}
        facts = "f" * 5000 + "FACTS_TAIL"
        actions = "a" * 700 + "ACTIONS_TAIL"
        with patch.object(l1, "_build_system", side_effect=build_system), patch.object(
            l1, "_build_options", return_value={}
        ), patch.object(l1.requests, "post", side_effect=post):
            reply = l1.voice_finalize("read it", facts, actions, mood, traits)

        self.assertEqual(reply, "grounded. still mine.")
        self.assertEqual(captured["context"], {"mood": mood, "traits": traits})
        self.assertNotIn("recent_memory", captured["context"])
        self.assertNotIn("voice_anchors", captured["context"])
        messages = captured["payload"]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "voice system")
        self.assertNotIn("FACTS_TAIL", messages[1]["content"])
        self.assertNotIn("ACTIONS_TAIL", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()

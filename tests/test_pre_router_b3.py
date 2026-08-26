import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from routing.pre_router import PreRouter, Route


SNAPSHOT = {
    "cpu_percent": 12.5,
    "ram_percent": 40.0,
    "ram_used_gb": 6.4,
    "ram_total_gb": 16.0,
    "top_processes": [{"name": "code.exe", "cpu": 8.0, "ram": 4.0}],
}


class PreRouterTests(unittest.TestCase):
    def test_classification_table(self):
        router = PreRouter(lambda: SNAPSHOT)
        cases = (
            ("/agent read core/server.py", Route.AGENT, "read core/server.py"),
            ("прочитай core/skills/base.py и скажи что там", Route.AGENT, None),
            ("прочитай мне стихотворение", Route.CHAT, None),
            ("йоу как ты", Route.CHAT, None),
            ("сколько RAM занято", Route.CACHED, None),
            ("/agent", Route.CHAT, None),
        )

        for text, expected_route, expected_task in cases:
            with self.subTest(text=text):
                decision = router.classify(text)
                self.assertIs(decision.route, expected_route)
                if expected_task is not None:
                    self.assertEqual(decision.task, expected_task)

        empty = router.classify("/agent")
        self.assertEqual(empty.response, "give me a task after /agent.")

    def test_cached_answers_use_snapshot(self):
        router = PreRouter(lambda: SNAPSHOT)

        self.assertEqual(router.classify("CPU?").response, "cpu: 12.5%.")
        self.assertEqual(
            router.classify("сколько RAM занято?").response,
            "ram: 40% used (6.4/16 gb).",
        )
        self.assertEqual(
            router.classify("which process uses most memory?").response,
            "top process: code.exe (ram 4%, cpu 8%).",
        )

    def test_missing_snapshot_stays_cached(self):
        router = PreRouter(lambda: None)
        for text in ("CPU?", "сколько RAM занято?", "which process uses most memory?"):
            with self.subTest(text=text):
                decision = router.classify(text)
                self.assertIs(decision.route, Route.CACHED)
                self.assertIn("telemetry is unavailable", decision.response)

    def test_path_heuristic_requires_action_and_path(self):
        router = PreRouter(lambda: SNAPSHOT)
        self.assertIs(router.classify("show settings.json").route, Route.AGENT)
        self.assertIs(router.classify("settings.json looks odd").route, Route.CHAT)
        self.assertIs(router.classify("show me something").route, Route.CHAT)


if __name__ == "__main__":
    unittest.main()

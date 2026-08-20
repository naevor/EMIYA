import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from skills.base import SkillContext
from skills.registry import SkillRegistry
from skills.system import SystemProcessesSkill, SystemStatsSkill


class SnapshotProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.snapshot


class SystemSkillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = SkillContext(allowed_roots=[], run_id="system-test")

    async def test_stats_uses_injected_snapshot_provider(self):
        provider = SnapshotProvider(
            {
                "cpu_percent": 17.5,
                "ram_percent": 62.0,
                "ram_used_gb": 19.8,
                "ram_total_gb": 32.0,
                "top_processes": [{"name": "code.exe", "cpu": 8.0, "ram": 2.0}],
            }
        )
        registry = SkillRegistry()
        registry.register(SystemStatsSkill(provider))

        result = await registry.execute("system.stats", {}, self.context)

        self.assertTrue(result.ok)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            result.data,
            {
                "cpu_percent": 17.5,
                "ram_percent": 62.0,
                "ram_used_gb": 19.8,
                "ram_total_gb": 32.0,
            },
        )

    async def test_processes_use_snapshot_order_and_cap(self):
        provider = SnapshotProvider(
            {
                "top_processes": [
                    {"name": "first.exe", "cpu": 20.0, "ram": 4.0},
                    {"name": "second.exe", "cpu": 10.0, "ram": 2.0},
                    {"name": "third.exe", "cpu": 5.0, "ram": 1.0},
                ]
            }
        )
        registry = SkillRegistry()
        registry.register(SystemProcessesSkill(provider, process_cap=2))

        result = await registry.execute("system.processes", {}, self.context)

        self.assertTrue(result.ok)
        self.assertEqual(provider.calls, 1)
        self.assertTrue(result.truncated)
        self.assertEqual(result.data["count"], 2)
        self.assertEqual(
            result.data["processes"],
            [
                {"name": "first.exe", "cpu": 20.0, "ram": 4.0},
                {"name": "second.exe", "cpu": 10.0, "ram": 2.0},
            ],
        )

    async def test_invalid_provider_result_is_normalized_by_registry(self):
        registry = SkillRegistry()
        registry.register(SystemStatsSkill(lambda: None))

        with self.assertLogs("skills.registry", level="ERROR"):
            result = await registry.execute("system.stats", {}, self.context)

        self.assertFalse(result.ok)
        self.assertIn("snapshot provider returned invalid data", result.error)
        self.assertNotIn("traceback", result.error.lower())


if __name__ == "__main__":
    unittest.main()

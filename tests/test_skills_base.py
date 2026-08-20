import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from skills.base import Permission, SkillContext, SkillResult


class SkillBaseTests(unittest.TestCase):
    def test_permission_values_are_stable_strings(self):
        self.assertEqual(Permission.READ.value, "read")
        self.assertEqual(Permission.WRITE.value, "write")
        self.assertEqual(Permission.DANGEROUS.value, "dangerous")
        self.assertIsInstance(Permission.READ, str)

    def test_skill_result_defaults_are_neutral(self):
        result = SkillResult(ok=True)

        self.assertTrue(result.ok)
        self.assertIsNone(result.data)
        self.assertIsNone(result.error)
        self.assertEqual(result.duration_ms, 0)
        self.assertFalse(result.truncated)

    def test_skill_context_contains_only_execution_context(self):
        root = Path("workspace")
        context = SkillContext(allowed_roots=[root], run_id="run-1")

        self.assertEqual(context.allowed_roots, [root])
        self.assertEqual(context.run_id, "run-1")
        self.assertEqual(set(vars(context)), {"allowed_roots", "run_id"})


if __name__ == "__main__":
    unittest.main()

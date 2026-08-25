import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from agent.gate import GateDecision, GatePolicy, GateVerdict
from agent.loop import AgentLoop, RunStatus
from agent.provider import FinalResult, ToolCall
from agent.testing import FakeModelProvider
from skills.base import Permission, SkillContext, SkillResult
from skills.registry import SkillRegistry


NO_ARGS_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class CountingSkill:
    args_schema = NO_ARGS_SCHEMA
    non_reversible = False
    timeout_s = 1.0

    def __init__(self, name: str, permission: Permission):
        self.name = name
        self.description = "Count executions."
        self.permission = permission
        self.calls = 0

    async def run(self, args, ctx):
        self.calls += 1
        return SkillResult(ok=True, data={"calls": self.calls})


def sealed_registry(*skills):
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    registry.seal()
    return registry


class ConfirmationPolicy:
    def evaluate(self, permission):
        return GateDecision(GateVerdict.REQUIRE_CONFIRMATION, "confirmation required")


class AgentGateTests(unittest.IsolatedAsyncioTestCase):
    def test_fixed_permission_policy(self):
        policy = GatePolicy()

        read = policy.evaluate(Permission.READ)
        write = policy.evaluate(Permission.WRITE)
        dangerous = policy.evaluate(Permission.DANGEROUS)

        self.assertEqual(read.verdict, GateVerdict.ALLOW)
        self.assertEqual(write.verdict, GateVerdict.DENY)
        self.assertEqual(write.reason, "write skills are not enabled")
        self.assertEqual(dangerous.verdict, GateVerdict.DENY)
        self.assertEqual(dangerous.reason, "dangerous skills require confirmation flow")

    async def test_write_and_dangerous_skills_are_denied_without_execution(self):
        for permission in (Permission.WRITE, Permission.DANGEROUS):
            with self.subTest(permission=permission):
                skill = CountingSkill(f"test.{permission.value}", permission)
                provider = FakeModelProvider(
                    [ToolCall(skill.name, {}), FinalResult("done")]
                )
                result = await AgentLoop(
                    provider,
                    sealed_registry(skill),
                    GatePolicy(),
                ).run("test gate", SkillContext([], "gate-run"))

                self.assertEqual(result.status, RunStatus.COMPLETED)
                self.assertEqual(result.steps[0].status, "denied")
                self.assertFalse(result.steps[0].observation.ok)
                self.assertEqual(skill.calls, 0)

    async def test_confirmation_verdict_is_defensively_denied(self):
        skill = CountingSkill("test.confirm", Permission.READ)
        provider = FakeModelProvider(
            [ToolCall(skill.name, {}), FinalResult("done")]
        )

        result = await AgentLoop(
            provider,
            sealed_registry(skill),
            ConfirmationPolicy(),
        ).run("test confirmation", SkillContext([], "confirmation-run"))

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.steps[0].status, "denied")
        self.assertEqual(result.steps[0].observation.content, "confirmation required")
        self.assertEqual(skill.calls, 0)


if __name__ == "__main__":
    unittest.main()

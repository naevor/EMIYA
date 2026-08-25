import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from agent.gate import GatePolicy
from agent.loop import AgentLoop, RunStatus
from agent.provider import FinalResult, InvalidDecision, ToolCall
from agent.testing import FakeModelProvider
from skills.base import Permission, SkillContext, SkillResult
from skills.fs import FsListSkill, FsReadSkill
from skills.registry import SkillRegistry


VALUE_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string", "minLength": 1}},
    "required": ["value"],
    "additionalProperties": False,
}


class EchoSkill:
    name = "test.echo"
    description = "Echo a value."
    args_schema = VALUE_SCHEMA
    permission = Permission.READ
    non_reversible = False
    timeout_s = 1.0

    def __init__(self):
        self.calls = []

    async def run(self, args, ctx):
        self.calls.append(args["value"])
        return SkillResult(ok=True, data={"value": args["value"]})


def sealed_registry(*skills):
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    registry.seal()
    return registry


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "note.txt").write_text("agent facts", encoding="utf-8")
        self.context = SkillContext([self.root], "agent-run")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def fs_registry():
        return sealed_registry(FsListSkill(), FsReadSkill())

    async def test_one_real_fs_read_completes(self):
        provider = FakeModelProvider(
            [
                ToolCall("fs.read", {"path": "note.txt"}),
                FinalResult("The file contains agent facts."),
            ]
        )

        result = await AgentLoop(provider, self.fs_registry(), GatePolicy()).run(
            "read the note",
            self.context,
        )

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.final_facts, "The file contains agent facts.")
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[0].status, "ok")
        self.assertIn("agent facts", result.steps[0].observation.content)

    async def test_final_result_stops_provider_immediately(self):
        provider = FakeModelProvider(
            [
                FinalResult("Already complete."),
                ToolCall("fs.read", {"path": "note.txt"}),
            ]
        )

        result = await AgentLoop(provider, self.fs_registry(), GatePolicy()).run(
            "answer directly",
            self.context,
        )

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.final_facts, "Already complete.")
        self.assertEqual(result.steps, ())
        self.assertEqual(len(provider.requests), 1)

    async def test_real_fs_list_then_read_completes(self):
        provider = FakeModelProvider(
            [
                ToolCall("fs.list", {"path": "."}),
                ToolCall("fs.read", {"path": "note.txt"}),
                FinalResult("Found and read note.txt."),
            ]
        )

        result = await AgentLoop(provider, self.fs_registry(), GatePolicy()).run(
            "find and read the note",
            self.context,
        )

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual([step.index for step in result.steps], [1, 2])
        self.assertEqual([len(request.steps) for request in provider.requests], [0, 1, 2])

    async def test_invalid_decision_retries_with_temporary_feedback(self):
        provider = FakeModelProvider(
            [
                InvalidDecision("missing decision type", raw="bad output"),
                ToolCall("fs.read", {"path": "note.txt"}),
                FinalResult("Recovered."),
            ]
        )

        result = await AgentLoop(provider, self.fs_registry(), GatePolicy()).run(
            "read the note",
            self.context,
        )

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.invalid_decisions, 1)
        self.assertIsNone(provider.requests[0].feedback)
        self.assertIn("invalid decision", provider.requests[1].feedback)
        self.assertNotIn("bad output", provider.requests[1].feedback)
        self.assertIsNone(provider.requests[2].feedback)
        self.assertTrue(all(request.task == "read the note" for request in provider.requests))

    async def test_invalid_decision_budget_is_bounded(self):
        provider = FakeModelProvider(
            [
                InvalidDecision("bad one"),
                InvalidDecision("bad two"),
                InvalidDecision("bad three"),
            ]
        )

        result = await AgentLoop(provider, self.fs_registry(), GatePolicy()).run(
            "bounded invalid output",
            self.context,
        )

        self.assertEqual(result.status, RunStatus.FAILED_INVALID_DECISION)
        self.assertEqual(result.invalid_decisions, 3)
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(result.steps, ())

    async def test_unknown_skill_consumes_step_and_exposes_available_names(self):
        provider = FakeModelProvider(
            [ToolCall("missing.skill", {}), FinalResult("Recovered.")]
        )

        result = await AgentLoop(provider, self.fs_registry(), GatePolicy()).run(
            "use a tool",
            self.context,
        )

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.steps[0].status, "unknown_skill")
        self.assertIn("fs.list", result.steps[0].observation.content)
        self.assertIn("fs.read", result.steps[0].observation.content)

    async def test_registry_validation_error_can_be_recovered(self):
        provider = FakeModelProvider(
            [
                ToolCall("fs.read", {}),
                ToolCall("fs.read", {"path": "note.txt"}),
                FinalResult("Recovered."),
            ]
        )

        result = await AgentLoop(provider, self.fs_registry(), GatePolicy()).run(
            "read a file",
            self.context,
        )

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual([step.status for step in result.steps], ["error", "ok"])
        self.assertIn("invalid arguments", result.steps[0].observation.content)

    async def test_skill_error_can_be_recovered(self):
        provider = FakeModelProvider(
            [
                ToolCall("fs.read", {"path": "missing.txt"}),
                ToolCall("fs.read", {"path": "note.txt"}),
                FinalResult("Used the existing file."),
            ]
        )

        result = await AgentLoop(provider, self.fs_registry(), GatePolicy()).run(
            "recover from a missing path",
            self.context,
        )

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual([step.status for step in result.steps], ["error", "ok"])
        self.assertEqual(result.steps[0].observation.content, "path not found")

    async def test_three_identical_actions_fail_after_two_blocks(self):
        skill = EchoSkill()
        action = ToolCall("test.echo", {"value": "same"})
        provider = FakeModelProvider([action, action, action])

        result = await AgentLoop(
            provider,
            sealed_registry(skill),
            GatePolicy(),
        ).run("repeat", self.context)

        self.assertEqual(result.status, RunStatus.FAILED_REPEATED_ACTION)
        self.assertEqual(
            [step.status for step in result.steps],
            ["ok", "repeat_blocked", "repeat_blocked"],
        )
        self.assertEqual(skill.calls, ["same"])

    async def test_repeat_counter_resets_after_different_action(self):
        skill = EchoSkill()
        provider = FakeModelProvider(
            [
                ToolCall("test.echo", {"value": "a"}),
                ToolCall("test.echo", {"value": "a"}),
                ToolCall("test.echo", {"value": "b"}),
                ToolCall("test.echo", {"value": "a"}),
                FinalResult("Done."),
            ]
        )

        result = await AgentLoop(
            provider,
            sealed_registry(skill),
            GatePolicy(),
        ).run("legal repeat", self.context)

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(skill.calls, ["a", "b", "a"])
        self.assertEqual(result.steps[1].status, "repeat_blocked")

    async def test_max_steps_stops_without_an_extra_provider_call(self):
        skill = EchoSkill()
        provider = FakeModelProvider(
            [
                ToolCall("test.echo", {"value": "a"}),
                ToolCall("test.echo", {"value": "b"}),
                ToolCall("test.echo", {"value": "c"}),
                FinalResult("must not be reached"),
            ]
        )

        result = await AgentLoop(
            provider,
            sealed_registry(skill),
            GatePolicy(),
            max_steps=3,
        ).run("bounded steps", self.context)

        self.assertEqual(result.status, RunStatus.FAILED_MAX_STEPS)
        self.assertEqual(len(result.steps), 3)
        self.assertEqual(len(provider.requests), 3)

    async def test_provider_exception_is_compact_and_terminal(self):
        provider = FakeModelProvider([RuntimeError("provider failed " + "x" * 700)])

        result = await AgentLoop(provider, self.fs_registry(), GatePolicy()).run(
            "provider failure",
            self.context,
        )

        self.assertEqual(result.status, RunStatus.FAILED_PROVIDER)
        self.assertLessEqual(len(result.error), 500)
        self.assertEqual(result.steps, ())

    async def test_registry_remains_sealed_and_unchanged(self):
        registry = self.fs_registry()
        before = registry.descriptors()
        provider = FakeModelProvider(
            [ToolCall("fs.read", {"path": "note.txt"}), FinalResult("Done.")]
        )

        await AgentLoop(provider, registry, GatePolicy()).run("read", self.context)

        self.assertEqual(registry.descriptors(), before)
        with self.assertRaisesRegex(RuntimeError, "registry is sealed"):
            registry.register(EchoSkill())


if __name__ == "__main__":
    unittest.main()

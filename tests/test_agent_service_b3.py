import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from agent.gate import GatePolicy
from agent.loop import AgentLoop, RunStatus
from agent.provider import FinalResult, ToolCall
from agent.testing import FakeModelProvider
from routing.agent_service import AgentService, LIVE_OBSERVATION_CAP_CHARS
from skills.base import SkillContext
from skills.fs import FsReadSkill
from skills.registry import SkillRegistry
from telemetry.pipeline_log import PipelineLogger


class ConversationSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def registry():
    result = SkillRegistry()
    result.register(FsReadSkill())
    result.seal()
    return result


class AgentServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "note.txt").write_text("agent facts", encoding="utf-8")
        self.pipeline = PipelineLogger()
        self.conversations = ConversationSpy()

    def tearDown(self):
        self.temp.cleanup()

    def service(self, provider, voice_fn):
        loop = AgentLoop(
            provider,
            registry(),
            GatePolicy(),
            observation_cap_chars=LIVE_OBSERVATION_CAP_CHARS,
        )
        return AgentService(
            loop,
            allowed_roots=[self.root],
            voice_fn=voice_fn,
            conversation_store=self.conversations,
            pipeline_logger=self.pipeline,
            mood_provider=lambda: {"energy": 0.4},
            traits_provider=lambda: {"warmth": 40},
            voice_model="voice-model",
        )

    async def test_completed_run_finalizes_voice_stores_original_and_orders_stages(self):
        provider = FakeModelProvider(
            [
                ToolCall("fs.read", {"path": "note.txt"}),
                FinalResult("note.txt contains agent facts"),
            ]
        )
        voice_calls = []

        def voice(**kwargs):
            voice_calls.append(kwargs)
            return "facts, with a pulse."

        service = self.service(provider, voice)
        result = await service.run(
            original_user_message="/agent read note.txt",
            agent_task="read note.txt",
            run_id="run-complete",
        )

        self.assertEqual(result.run.status, RunStatus.COMPLETED)
        self.assertEqual(result.reply, "facts, with a pulse.")
        self.assertEqual(provider.requests[0].task, "read note.txt")
        self.assertEqual(voice_calls[0]["user_message"], "read note.txt")
        self.assertIn("agent facts", voice_calls[0]["facts"])
        self.assertIn("fs.read", voice_calls[0]["actions_summary"])
        args, kwargs = self.conversations.calls[0]
        self.assertEqual(args[:2], ("/agent read note.txt", "facts, with a pulse."))
        self.assertEqual(kwargs["turn_id"], "run-complete")

        run = self.pipeline.recent()[0]
        self.assertEqual(
            [step["name"] for step in run["steps"]],
            ["INPUT", "ROUTE", "DECIDE", "TOOL", "DECIDE", "VOICE", "OUT"],
        )
        tool = next(step for step in run["steps"] if step["name"] == "TOOL")
        self.assertGreaterEqual(tool["details"]["duration_ms"], 0)

    async def test_voice_failure_falls_back_to_facts_and_marks_stage(self):
        provider = FakeModelProvider([FinalResult("raw grounded facts")])

        def voice(**_kwargs):
            raise RuntimeError("voice offline")

        result = await self.service(provider, voice).run(
            original_user_message="/agent answer",
            agent_task="answer",
            run_id="run-voice-fail",
        )

        self.assertEqual(result.reply, "raw grounded facts")
        run = self.pipeline.recent()[0]
        voice_step = next(step for step in run["steps"] if step["name"] == "VOICE")
        self.assertEqual(voice_step["status"], "error")
        self.assertIn("voice offline", voice_step["details"]["error"])
        self.assertEqual(self.conversations.calls[0][0][1], "raw grounded facts")

    async def test_provider_failure_uses_deterministic_reply_and_is_stored(self):
        provider = FakeModelProvider([RuntimeError("ollama offline")])
        result = await self.service(provider, lambda **_: "unused").run(
            original_user_message="/agent read note.txt",
            agent_task="read note.txt",
            run_id="run-provider-fail",
        )

        self.assertEqual(result.run.status, RunStatus.FAILED_PROVIDER)
        self.assertEqual(
            result.reply,
            "the model is unavailable. apparently observation is all i'm allowed today.",
        )
        self.assertEqual(self.conversations.calls[0][0][1], result.reply)
        run = self.pipeline.recent()[0]
        self.assertEqual(
            [step["name"] for step in run["steps"]],
            ["INPUT", "ROUTE", "DECIDE", "OUT"],
        )
        self.assertEqual(run["steps"][2]["status"], "error")

    async def test_live_composition_caps_observation_before_next_decision(self):
        (self.root / "large.txt").write_text("z" * 10_000, encoding="utf-8")
        provider = FakeModelProvider(
            [
                ToolCall("fs.read", {"path": "large.txt"}),
                FinalResult("large file read"),
            ]
        )
        await self.service(provider, lambda **_: "done").run(
            original_user_message="/agent read large.txt",
            agent_task="read large.txt",
            run_id="run-cap",
        )

        observation = provider.requests[1].steps[0].observation
        self.assertLessEqual(len(observation.content), LIVE_OBSERVATION_CAP_CHARS)
        self.assertTrue(observation.truncated)


class AgentLoopHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_step_runs_before_followup_decision(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "note.txt").write_text("facts", encoding="utf-8")
        callback_steps = []

        def final_after_callback(_request):
            self.assertEqual(len(callback_steps), 1)
            return FinalResult("done")

        provider = FakeModelProvider(
            [ToolCall("fs.read", {"path": "note.txt"}), final_after_callback]
        )
        loop = AgentLoop(provider, registry(), GatePolicy(), on_step=callback_steps.append)
        result = await loop.run(
            "read note.txt",
            SkillContext([root], "hook"),
        )

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(callback_steps[0].skill, "fs.read")

    async def test_on_step_exception_does_not_fail_run(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "note.txt").write_text("facts", encoding="utf-8")
        provider = FakeModelProvider(
            [ToolCall("fs.read", {"path": "note.txt"}), FinalResult("done")]
        )

        def broken(_step):
            raise RuntimeError("telemetry failed")

        loop = AgentLoop(provider, registry(), GatePolicy(), on_step=broken)
        with self.assertLogs("agent.loop", level="WARNING") as captured:
            result = await loop.run(
                "read note.txt",
                SkillContext([root], "hook"),
            )
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertIn("callback failed", captured.output[0].lower())


if __name__ == "__main__":
    unittest.main()

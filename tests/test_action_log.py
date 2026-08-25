import json
import sqlite3
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
from memory.action_log import ActionLogStore
from skills.base import Permission, SkillContext, SkillResult
from skills.registry import SkillRegistry


NO_ARGS_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class FixedSkill:
    description = "Return fixed test data."
    args_schema = NO_ARGS_SCHEMA
    non_reversible = False
    timeout_s = 1.0

    def __init__(self, name, permission=Permission.READ, payload="ok"):
        self.name = name
        self.permission = permission
        self.payload = payload
        self.calls = 0

    async def run(self, args, ctx):
        self.calls += 1
        return SkillResult(ok=True, data=self.payload)


def sealed_registry(*skills):
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    registry.seal()
    return registry


class BrokenActionLog:
    def record(self, **kwargs):
        raise sqlite3.OperationalError("controlled log failure")


class ActionLogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "emiya.db"
        self.context = SkillContext([], "ctx-run")

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_is_additive_and_manual_payloads_are_capped(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("CREATE TABLE existing_data (value TEXT)")
            conn.execute("INSERT INTO existing_data VALUES ('kept')")
            conn.commit()
        finally:
            conn.close()

        store = ActionLogStore(self.db_path)
        store.record(
            run_id="manual-run",
            step=1,
            skill="test.large-args",
            args={"payload": "a" * 5000},
            status="ok",
            result_summary="r" * 900,
            duration_ms=4,
        )
        entry = store.get_run("manual-run")[0]

        self.assertLessEqual(len(entry.args_json), 2000)
        self.assertTrue(json.loads(entry.args_json)["truncated"])
        self.assertEqual(len(entry.result_summary), 500)
        self.assertEqual(entry.duration_ms, 4)
        self.assertIsNone(entry.undo_hint)

        conn = sqlite3.connect(self.db_path)
        try:
            kept = conn.execute("SELECT value FROM existing_data").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(kept, "kept")

    async def test_event_ordinals_are_independent_from_tool_step_indexes(self):
        read = FixedSkill("test.read")
        write = FixedSkill("test.write", Permission.WRITE)
        registry = sealed_registry(read, write)
        store = ActionLogStore(self.db_path)
        provider = FakeModelProvider(
            [
                InvalidDecision("malformed", raw="bad-json"),
                ToolCall("missing.skill", {}),
                ToolCall("test.write", {}),
                ToolCall("test.read", {}),
                FinalResult("Done."),
            ]
        )

        result = await AgentLoop(
            provider,
            registry,
            GatePolicy(),
            action_log=store,
        ).run("log every event", self.context, run_id="ordinal-run")
        entries = store.get_run("ordinal-run")

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual([step.index for step in result.steps], [1, 2, 3])
        self.assertEqual([entry.step for entry in entries], [1, 2, 3, 4])
        self.assertEqual(
            [entry.status for entry in entries],
            ["invalid", "unknown_skill", "denied", "ok"],
        )
        self.assertEqual(
            [entry.duration_ms for entry in entries[:3]],
            [None, None, None],
        )
        self.assertIsNotNone(entries[3].duration_ms)
        self.assertTrue(all(entry.run_id == "ordinal-run" for entry in entries))
        self.assertEqual(write.calls, 0)
        self.assertEqual(read.calls, 1)

    async def test_repeat_blocked_events_are_logged(self):
        skill = FixedSkill("test.repeat")
        store = ActionLogStore(self.db_path)
        action = ToolCall("test.repeat", {})

        result = await AgentLoop(
            FakeModelProvider([action, action, action]),
            sealed_registry(skill),
            GatePolicy(),
            action_log=store,
        ).run("repeat", self.context, run_id="repeat-run")

        self.assertEqual(result.status, RunStatus.FAILED_REPEATED_ACTION)
        self.assertEqual(
            [entry.status for entry in store.get_run("repeat-run")],
            ["ok", "repeat_blocked", "repeat_blocked"],
        )

    async def test_large_tool_payload_is_capped_in_observation_and_database(self):
        payload = "x" * 200_000
        skill = FixedSkill("test.large", payload=payload)
        store = ActionLogStore(self.db_path)
        provider = FakeModelProvider(
            [ToolCall("test.large", {}), FinalResult("Done.")]
        )

        result = await AgentLoop(
            provider,
            sealed_registry(skill),
            GatePolicy(),
            action_log=store,
            observation_cap_chars=8000,
        ).run("large output", self.context, run_id="large-run")
        entry = store.get_run("large-run")[0]

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertTrue(result.steps[0].observation.truncated)
        self.assertEqual(len(result.steps[0].observation.content), 8000)
        self.assertEqual(len(entry.result_summary), 500)
        self.assertNotIn(payload, entry.result_summary)

        raw_db = self.db_path.read_bytes()
        self.assertNotIn(payload.encode(), raw_db)

    async def test_action_log_failure_warns_but_does_not_fail_run(self):
        skill = FixedSkill("test.log-failure")
        provider = FakeModelProvider(
            [ToolCall(skill.name, {}), FinalResult("Done.")]
        )

        with self.assertLogs("agent.loop", level="WARNING"):
            result = await AgentLoop(
                provider,
                sealed_registry(skill),
                GatePolicy(),
                action_log=BrokenActionLog(),
            ).run("survive log failure", self.context)

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(skill.calls, 1)


if __name__ == "__main__":
    unittest.main()

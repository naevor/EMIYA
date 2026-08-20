import asyncio
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from skills import build_core_registry
from skills.base import Permission, SkillContext, SkillResult
from skills.registry import SkillRegistry


VALUE_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string", "minLength": 1}},
    "required": ["value"],
    "additionalProperties": False,
}


class EchoSkill:
    name = "test.echo"
    description = "Echo one test value."
    args_schema = VALUE_SCHEMA
    permission = Permission.READ
    non_reversible = False
    timeout_s = 1.0

    def __init__(self):
        self.calls = 0

    async def run(self, args, ctx):
        self.calls += 1
        return SkillResult(ok=True, data={"value": args["value"]})


class SlowSkill(EchoSkill):
    name = "test.slow"
    timeout_s = 0.01

    async def run(self, args, ctx):
        await asyncio.sleep(0.05)
        return SkillResult(ok=True)


class DelayedSkill(EchoSkill):
    name = "test.delayed"

    async def run(self, args, ctx):
        await asyncio.sleep(0.02)
        return SkillResult(ok=True)


class BrokenSkill(EchoSkill):
    name = "test.broken"

    async def run(self, args, ctx):
        raise RuntimeError("controlled failure")


class SkillRegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = SkillContext(allowed_roots=[], run_id="registry-test")

    def test_core_descriptors_are_neutral_and_complete(self):
        registry = build_core_registry(lambda: {})
        descriptors = registry.descriptors()

        self.assertEqual(
            {descriptor["name"] for descriptor in descriptors},
            {"system.stats", "system.processes", "fs.list", "fs.read"},
        )
        self.assertEqual(len(descriptors), 4)
        for descriptor in descriptors:
            self.assertEqual(descriptor["permission"], "read")
            self.assertFalse(descriptor["non_reversible"])
            self.assertEqual(descriptor["trust"], "core")
            self.assertIsInstance(descriptor["args_schema"], dict)
            encoded = json.dumps(descriptor).lower()
            for forbidden in ("ollama", "qwen", "gemma", "openai", "tool_calls", "chat template"):
                self.assertNotIn(forbidden, encoded)

    def test_get_returns_registered_skill(self):
        registry = SkillRegistry()
        skill = EchoSkill()
        registry.register(skill)

        self.assertIs(registry.get(skill.name), skill)
        self.assertIsNone(registry.get("does.not.exist"))

    def test_core_registry_is_sealed_after_composition(self):
        registry = build_core_registry(lambda: {})

        with self.assertRaisesRegex(RuntimeError, "registry is sealed"):
            registry.register(EchoSkill())

    async def test_unknown_skill_returns_error(self):
        result = await SkillRegistry().execute("does.not.exist", {}, self.context)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "unknown skill: does.not.exist")
        self.assertGreaterEqual(result.duration_ms, 0)

    async def test_invalid_args_do_not_execute_skill(self):
        registry = SkillRegistry()
        skill = EchoSkill()
        registry.register(skill)

        missing = await registry.execute(skill.name, {}, self.context)
        wrong_type = await registry.execute(skill.name, {"value": 7}, self.context)
        extra = await registry.execute(
            skill.name,
            {"value": "ok", "unexpected": True},
            self.context,
        )

        self.assertEqual(skill.calls, 0)
        for result in (missing, wrong_type, extra):
            self.assertFalse(result.ok)
            self.assertTrue(result.error.startswith("invalid arguments:"))
            self.assertGreaterEqual(result.duration_ms, 0)

    async def test_timeout_is_normalized(self):
        registry = SkillRegistry()
        registry.register(SlowSkill())

        result = await registry.execute("test.slow", {"value": "wait"}, self.context)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "timeout")
        self.assertGreaterEqual(result.duration_ms, 0)

    async def test_exception_is_compact_without_traceback(self):
        registry = SkillRegistry()
        registry.register(BrokenSkill())

        with self.assertLogs("skills.registry", level="ERROR"):
            result = await registry.execute("test.broken", {"value": "fail"}, self.context)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "skill error: controlled failure")
        self.assertNotIn("traceback", result.error.lower())

    async def test_duration_is_measured_by_registry(self):
        registry = SkillRegistry()
        registry.register(DelayedSkill())

        result = await registry.execute("test.delayed", {"value": "measure"}, self.context)

        self.assertTrue(result.ok)
        self.assertGreaterEqual(result.duration_ms, 10)


if __name__ == "__main__":
    unittest.main()

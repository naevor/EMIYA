import asyncio
import copy
import logging
import time
from dataclasses import dataclass, replace
from typing import Any

from .base import Permission, Skill, SkillContext, SkillResult


logger = logging.getLogger(__name__)

_TYPE_NAMES = {
    "object": "object",
    "array": "array",
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}


@dataclass(frozen=True)
class _RegisteredSkill:
    skill: Skill
    trust: str


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> str | None:
    expected = schema.get("type")
    if expected:
        expected_types = [expected] if isinstance(expected, str) else list(expected)
        if not any(_matches_type(value, item) for item in expected_types):
            labels = "/".join(_TYPE_NAMES.get(item, str(item)) for item in expected_types)
            return f"{path} must be {labels}"

    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of {schema['enum']}"

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key} is required"
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                return f"{path}.{extra[0]} is not allowed"
        for key, item in value.items():
            item_schema = properties.get(key)
            if item_schema is not None:
                error = _validate_value(item, item_schema, f"{path}.{key}")
                if error:
                    return error

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            error = _validate_value(item, schema["items"], f"{path}[{index}]")
            if error:
                return error

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            return f"{path} is too short"
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            return f"{path} is too long"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path} must be >= {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path} must be <= {schema['maximum']}"

    return None


def _validate_args(args: Any, schema: dict[str, Any]) -> str | None:
    return _validate_value(args, schema, "args")


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _compact_exception(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    if not message:
        message = exc.__class__.__name__
    return f"skill error: {message[:160]}"


class SkillRegistry:
    def __init__(self, default_timeout_s: float = 10.0):
        if default_timeout_s <= 0:
            raise ValueError("default_timeout_s must be positive")
        self.default_timeout_s = float(default_timeout_s)
        self._skills: dict[str, _RegisteredSkill] = {}
        self._sealed = False

    def register(self, skill: Skill, trust: str = "core") -> None:
        if self._sealed:
            raise RuntimeError("skill registry is sealed")
        name = str(getattr(skill, "name", "")).strip()
        if not name:
            raise ValueError("skill name cannot be empty")
        if name in self._skills:
            raise ValueError(f"skill already registered: {name}")
        if trust != "core":
            raise ValueError(f"unsupported skill trust: {trust}")
        if not isinstance(getattr(skill, "permission", None), Permission):
            raise ValueError(f"invalid permission for skill: {name}")
        if not isinstance(getattr(skill, "args_schema", None), dict):
            raise ValueError(f"invalid args schema for skill: {name}")
        self._skills[name] = _RegisteredSkill(skill=skill, trust=trust)

    def seal(self) -> None:
        self._sealed = True

    def get(self, name: str) -> Skill | None:
        registered = self._skills.get(name)
        return registered.skill if registered else None

    def descriptors(self) -> list[dict[str, Any]]:
        descriptors = []
        for name in sorted(self._skills):
            registered = self._skills[name]
            skill = registered.skill
            descriptors.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "args_schema": copy.deepcopy(skill.args_schema),
                    "permission": skill.permission.value,
                    "non_reversible": bool(skill.non_reversible),
                    "trust": registered.trust,
                }
            )
        return descriptors

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        ctx: SkillContext,
    ) -> SkillResult:
        started = time.perf_counter()
        skill = self.get(name)
        if skill is None:
            return SkillResult(
                ok=False,
                error=f"unknown skill: {name}",
                duration_ms=_duration_ms(started),
            )

        validation_error = _validate_args(args, skill.args_schema)
        if validation_error:
            return SkillResult(
                ok=False,
                error=f"invalid arguments: {validation_error}",
                duration_ms=_duration_ms(started),
            )

        timeout_s = float(getattr(skill, "timeout_s", self.default_timeout_s))
        if timeout_s <= 0:
            timeout_s = self.default_timeout_s

        try:
            result = await asyncio.wait_for(skill.run(dict(args), ctx), timeout=timeout_s)
            if not isinstance(result, SkillResult):
                return SkillResult(
                    ok=False,
                    error="invalid skill result",
                    duration_ms=_duration_ms(started),
                )
            return replace(result, duration_ms=_duration_ms(started))
        except asyncio.TimeoutError:
            return SkillResult(ok=False, error="timeout", duration_ms=_duration_ms(started))
        except Exception as exc:
            logger.exception("Skill %s failed", name)
            return SkillResult(
                ok=False,
                error=_compact_exception(exc),
                duration_ms=_duration_ms(started),
            )

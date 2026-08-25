import copy
import json
import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from skills.base import SkillContext, SkillResult
from skills.registry import SkillRegistry

from .gate import GatePolicy, GateVerdict
from .provider import (
    DecisionRequest,
    FinalResult,
    InvalidDecision,
    ModelProvider,
    Observation,
    StepRecord,
    ToolCall,
    is_agent_decision,
)


logger = logging.getLogger(__name__)


class RunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED_INVALID_DECISION = "failed_invalid_decision"
    FAILED_MAX_STEPS = "failed_max_steps"
    FAILED_REPEATED_ACTION = "failed_repeated_action"
    FAILED_PROVIDER = "failed_provider"


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    status: RunStatus
    final_facts: str | None
    steps: tuple[StepRecord, ...]
    invalid_decisions: int
    error: str | None


class ActionLog(Protocol):
    def record(
        self,
        *,
        run_id: str,
        step: int,
        skill: str,
        args: dict[str, Any],
        status: str,
        result_summary: str | None = None,
        duration_ms: int | None = None,
    ) -> int:
        ...


def _compact_text(value: object, cap: int) -> str:
    text = " ".join(str(value).split())
    return text[:cap]


def _provider_error(exc: Exception) -> str:
    return _compact_text(str(exc) or exc.__class__.__name__, 500)


def _feedback(reason: str) -> str:
    reason_text = _compact_text(reason or "unspecified", 120)
    return (
        f"invalid decision: {reason_text}; respond with a valid tool call or final result"
    )[:200]


def _invalid_summary(decision: InvalidDecision) -> str:
    summary = _compact_text(decision.reason or "unspecified", 500)
    if decision.raw:
        summary = f"{summary}; raw: {_compact_text(decision.raw, 500)}"
    return summary[:500]


def _render_result(result: SkillResult) -> str:
    if not result.ok:
        return str(result.error or "skill failed")
    if isinstance(result.data, str):
        return result.data
    return json.dumps(
        result.data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _capped_observation(
    *,
    source: str,
    ok: bool,
    content: str,
    cap: int,
    already_truncated: bool = False,
) -> Observation:
    was_capped = len(content) > cap
    return Observation(
        source=source,
        ok=ok,
        content=content[:cap],
        truncated=already_truncated or was_capped,
    )


def _action_signature(decision: ToolCall) -> str:
    encoded_args = json.dumps(decision.args, sort_keys=True, separators=(",", ":"))
    return f"{decision.skill}:{encoded_args}"


class AgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        registry: SkillRegistry,
        gate: GatePolicy,
        action_log: ActionLog | None = None,
        *,
        max_steps: int = 6,
        invalid_retry_budget: int = 2,
        observation_cap_chars: int = 8000,
    ):
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if invalid_retry_budget < 0:
            raise ValueError("invalid_retry_budget cannot be negative")
        if observation_cap_chars <= 0:
            raise ValueError("observation_cap_chars must be positive")
        self.provider = provider
        self.registry = registry
        self.gate = gate
        self.action_log = action_log
        self.max_steps = int(max_steps)
        self.invalid_retry_budget = int(invalid_retry_budget)
        self.observation_cap_chars = int(observation_cap_chars)

    async def run(
        self,
        task: str,
        ctx: SkillContext,
        run_id: str | None = None,
    ) -> AgentRunResult:
        resolved_run_id = self._resolve_run_id(run_id, ctx.run_id)
        execution_ctx = SkillContext(
            allowed_roots=list(ctx.allowed_roots),
            run_id=resolved_run_id,
        )
        tools = tuple(self.registry.descriptors())
        available_skills = tuple(descriptor["name"] for descriptor in tools)
        steps: list[StepRecord] = []
        invalid_decisions = 0
        feedback: str | None = None
        provider_calls = 0
        action_ordinal = 0
        last_signature: str | None = None
        consecutive_identical = 0
        provider_call_limit = self.max_steps + self.invalid_retry_budget + 1

        def finish(
            status: RunStatus,
            *,
            facts: str | None = None,
            error: str | None = None,
        ) -> AgentRunResult:
            return AgentRunResult(
                run_id=resolved_run_id,
                status=status,
                final_facts=facts,
                steps=tuple(steps),
                invalid_decisions=invalid_decisions,
                error=error,
            )

        def log_event(
            *,
            skill: str,
            args: dict[str, Any],
            status: str,
            summary: str | None,
            duration_ms: int | None = None,
        ) -> None:
            nonlocal action_ordinal
            action_ordinal += 1
            if self.action_log is None:
                return
            try:
                self.action_log.record(
                    run_id=resolved_run_id,
                    step=action_ordinal,
                    skill=skill,
                    args=args,
                    status=status,
                    result_summary=summary,
                    duration_ms=duration_ms,
                )
            except Exception:
                logger.warning(
                    "Failed to write action log event %s for run %s",
                    action_ordinal,
                    resolved_run_id,
                    exc_info=True,
                )

        while True:
            if provider_calls >= provider_call_limit:
                return finish(
                    RunStatus.FAILED_MAX_STEPS,
                    error="provider call limit reached",
                )

            request = DecisionRequest(
                task=task,
                tools=tools,
                steps=tuple(steps),
                feedback=feedback,
            )
            feedback = None
            provider_calls += 1
            try:
                decision = await self.provider.decide(request)
            except Exception as exc:
                return finish(RunStatus.FAILED_PROVIDER, error=_provider_error(exc))

            if not is_agent_decision(decision):
                decision = InvalidDecision(
                    reason="provider returned unsupported decision",
                    raw=repr(decision),
                )

            if isinstance(decision, InvalidDecision):
                invalid_decisions += 1
                log_event(
                    skill="(invalid)",
                    args={},
                    status="invalid",
                    summary=_invalid_summary(decision),
                )
                if invalid_decisions > self.invalid_retry_budget:
                    return finish(
                        RunStatus.FAILED_INVALID_DECISION,
                        error="invalid decision retry budget exhausted",
                    )
                feedback = _feedback(decision.reason)
                continue

            if isinstance(decision, FinalResult):
                return finish(RunStatus.COMPLETED, facts=decision.facts)

            step_index = len(steps) + 1
            step_args = copy.deepcopy(decision.args)
            try:
                signature = _action_signature(decision)
            except (TypeError, ValueError) as exc:
                invalid_decisions += 1
                invalid = InvalidDecision(reason=f"tool arguments are not JSON: {exc}")
                log_event(
                    skill="(invalid)",
                    args={},
                    status="invalid",
                    summary=_invalid_summary(invalid),
                )
                if invalid_decisions > self.invalid_retry_budget:
                    return finish(
                        RunStatus.FAILED_INVALID_DECISION,
                        error="invalid decision retry budget exhausted",
                    )
                feedback = _feedback(invalid.reason)
                continue

            if signature == last_signature:
                consecutive_identical += 1
            else:
                last_signature = signature
                consecutive_identical = 1

            if consecutive_identical >= 2:
                observation = _capped_observation(
                    source="loop",
                    ok=False,
                    content="identical action repeated; previous result already provided",
                    cap=self.observation_cap_chars,
                )
                steps.append(
                    StepRecord(
                        index=step_index,
                        skill=decision.skill,
                        args=step_args,
                        status="repeat_blocked",
                        observation=observation,
                    )
                )
                log_event(
                    skill=decision.skill,
                    args=step_args,
                    status="repeat_blocked",
                    summary=observation.content,
                )
                if consecutive_identical >= 3:
                    return finish(
                        RunStatus.FAILED_REPEATED_ACTION,
                        error="identical action repeated three times",
                    )
                if len(steps) >= self.max_steps:
                    return finish(
                        RunStatus.FAILED_MAX_STEPS,
                        error="maximum steps reached",
                    )
                continue

            skill = self.registry.get(decision.skill)
            if skill is None:
                names = ", ".join(available_skills) or "none"
                observation = _capped_observation(
                    source="loop",
                    ok=False,
                    content=f"unknown skill '{decision.skill}'; available: {names}",
                    cap=self.observation_cap_chars,
                )
                status = "unknown_skill"
                duration_ms = None
            else:
                gate_decision = self.gate.evaluate(skill.permission)
                if gate_decision.verdict is not GateVerdict.ALLOW:
                    reason = gate_decision.reason or "skill execution denied"
                    if gate_decision.verdict is GateVerdict.REQUIRE_CONFIRMATION:
                        reason = reason or "confirmation flow is not available"
                    observation = _capped_observation(
                        source=decision.skill,
                        ok=False,
                        content=reason,
                        cap=self.observation_cap_chars,
                    )
                    status = "denied"
                    duration_ms = None
                else:
                    skill_result = await self.registry.execute(
                        decision.skill,
                        decision.args,
                        execution_ctx,
                    )
                    observation = _capped_observation(
                        source=decision.skill,
                        ok=skill_result.ok,
                        content=_render_result(skill_result),
                        cap=self.observation_cap_chars,
                        already_truncated=skill_result.truncated,
                    )
                    status = "ok" if skill_result.ok else "error"
                    duration_ms = skill_result.duration_ms

            steps.append(
                StepRecord(
                    index=step_index,
                    skill=decision.skill,
                    args=step_args,
                    status=status,
                    observation=observation,
                )
            )
            log_event(
                skill=decision.skill,
                args=step_args,
                status=status,
                summary=observation.content,
                duration_ms=duration_ms,
            )

            if len(steps) >= self.max_steps:
                return finish(
                    RunStatus.FAILED_MAX_STEPS,
                    error="maximum steps reached",
                )

    @staticmethod
    def _resolve_run_id(explicit: str | None, context_run_id: str | None) -> str:
        for candidate in (explicit, context_run_id):
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        return uuid.uuid4().hex

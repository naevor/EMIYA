import asyncio
import json
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.loop import AgentLoop, AgentRunResult, RunStatus
from agent.provider import AgentDecision, FinalResult, InvalidDecision, StepRecord, ToolCall
from models.agent_provider import ProviderCall
from skills.base import SkillContext


logger = logging.getLogger(__name__)

LIVE_OBSERVATION_CAP_CHARS = 3000
_FAILURE_REPLIES = {
    RunStatus.FAILED_PROVIDER: (
        "the model is unavailable. apparently observation is all i'm allowed today."
    ),
    RunStatus.FAILED_INVALID_DECISION: (
        "the model failed to produce a coherent action. try phrasing it differently."
    ),
    RunStatus.FAILED_REPEATED_ACTION: (
        "the model started repeating itself. i stopped it."
    ),
}


@dataclass(frozen=True)
class AgentServiceResult:
    reply: str
    route: str
    run: AgentRunResult


def _decision_kind(decision: AgentDecision) -> str:
    if isinstance(decision, ToolCall):
        return "tool_call"
    if isinstance(decision, FinalResult):
        return "final"
    if isinstance(decision, InvalidDecision):
        return "invalid"
    return "unknown"


def _actions_summary(steps: tuple[StepRecord, ...]) -> str:
    actions = []
    for step in steps:
        args = json.dumps(
            step.args,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        actions.append(f"{step.skill}({args}) [{step.status}]")
    return "; ".join(actions)[:500]


def _failure_reply(result: AgentRunResult) -> str:
    if result.status is RunStatus.FAILED_MAX_STEPS:
        actions = ", ".join(step.skill for step in result.steps) or "nothing useful"
        return f"i ran out of steps before the problem did. this is as far as i got: {actions}."
    return _FAILURE_REPLIES.get(result.status, "the agent path failed. quietly, at least.")


class _ObservedProvider:
    def __init__(self, delegate, callback):
        self.delegate = delegate
        self.callback = callback
        self.name = delegate.name

    async def decide(self, request):
        started = time.perf_counter()
        try:
            decision = await self.delegate.decide(request)
        except Exception as exc:
            self.callback(
                decision=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=str(exc),
            )
            raise
        self.callback(
            decision=decision,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=None,
        )
        return decision


class AgentService:
    def __init__(
        self,
        loop: AgentLoop,
        *,
        allowed_roots: list[Path],
        voice_fn: Callable[..., str | None],
        conversation_store: Callable[..., Any],
        pipeline_logger,
        mood_provider: Callable[[], dict[str, Any]],
        traits_provider: Callable[[], dict[str, Any]],
        voice_model: str,
        provider_call_getter: Callable[[], ProviderCall | None] | None = None,
        pipeline_dump_enabled: bool = False,
    ):
        self.loop = loop
        self.allowed_roots = list(allowed_roots)
        self.voice_fn = voice_fn
        self.conversation_store = conversation_store
        self.pipeline_logger = pipeline_logger
        self.mood_provider = mood_provider
        self.traits_provider = traits_provider
        self.voice_model = voice_model
        self.provider_call_getter = provider_call_getter
        self.pipeline_dump_enabled = bool(pipeline_dump_enabled)
        self._active_run_id: ContextVar[str | None] = ContextVar(
            "agent_service_run_id",
            default=None,
        )
        self._tool_started_at: ContextVar[float | None] = ContextVar(
            "agent_service_tool_started_at",
            default=None,
        )

        original_provider = self.loop.provider
        self.loop.provider = _ObservedProvider(original_provider, self._record_decision)
        previous_on_step = self.loop.on_step

        def combined_on_step(step: StepRecord) -> None:
            if previous_on_step is not None:
                previous_on_step(step)
            self._record_tool(step)

        self.loop.on_step = combined_on_step

    async def run(
        self,
        *,
        original_user_message: str,
        agent_task: str,
        run_id: str,
    ) -> AgentServiceResult:
        token = self._active_run_id.set(run_id)
        mood = self.mood_provider()
        traits = self.traits_provider()
        self.pipeline_logger.start_request(
            run_id,
            original_user_message,
            {"route": "agent"},
        )
        self.pipeline_logger.add_step(
            run_id,
            "INPUT",
            details={"chars": len(original_user_message)},
        )
        self.pipeline_logger.add_step(
            run_id,
            "ROUTE",
            details={"route": "agent", "task": agent_task[:500]},
        )

        try:
            result = await self.loop.run(
                agent_task,
                SkillContext(allowed_roots=self.allowed_roots, run_id=run_id),
                run_id=run_id,
            )
            if result.status is RunStatus.COMPLETED:
                facts = (result.final_facts or "")[:4000]
                actions = _actions_summary(result.steps)
                voice_started = time.perf_counter()
                voice_error = None
                try:
                    reply = await asyncio.to_thread(
                        self.voice_fn,
                        user_message=agent_task,
                        facts=facts,
                        actions_summary=actions,
                        mood=mood,
                        traits=traits,
                    )
                    if not reply:
                        voice_error = "voice finalizer returned no response"
                        reply = facts
                except Exception as exc:
                    voice_error = str(exc)[:500]
                    reply = facts
                self.pipeline_logger.add_step(
                    run_id,
                    "VOICE",
                    status="error" if voice_error else "ok",
                    latency_ms=(time.perf_counter() - voice_started) * 1000,
                    details={"model": self.voice_model, "error": voice_error},
                )
            else:
                reply = _failure_reply(result)

            self.conversation_store(
                original_user_message,
                reply,
                mood_snapshot=mood,
                tags=["agent"],
                turn_id=run_id,
            )
            self.pipeline_logger.add_step(
                run_id,
                "OUT",
                details={"chars": len(reply), "source": "agent"},
            )
            self.pipeline_logger.finish_request(
                run_id,
                result.status.value,
                dump=self.pipeline_dump_enabled,
            )
            return AgentServiceResult(reply=reply, route="agent", run=result)
        finally:
            self._active_run_id.reset(token)

    def _record_decision(
        self,
        *,
        decision: AgentDecision | None,
        latency_ms: float,
        error: str | None,
    ) -> None:
        run_id = self._active_run_id.get()
        if run_id is None:
            return
        provider_call = self.provider_call_getter() if self.provider_call_getter else None
        details = {
            "model": provider_call.model if provider_call else self.loop.provider.name,
            "decision_kind": (
                provider_call.decision_kind
                if provider_call
                else _decision_kind(decision) if decision is not None else "error"
            ),
            "raw": provider_call.raw_capped if provider_call else None,
            "error": error[:500] if error else None,
        }
        self.pipeline_logger.add_step(
            run_id,
            "DECIDE",
            status="error" if error else "ok",
            latency_ms=provider_call.latency_ms if provider_call else latency_ms,
            details=details,
        )
        self._tool_started_at.set(
            time.perf_counter() if isinstance(decision, ToolCall) else None
        )

    def _record_tool(self, step: StepRecord) -> None:
        run_id = self._active_run_id.get()
        if run_id is None:
            return
        tool_started_at = self._tool_started_at.get()
        duration_ms = (
            max(0.0, (time.perf_counter() - tool_started_at) * 1000)
            if tool_started_at is not None
            else None
        )
        self._tool_started_at.set(None)
        self.pipeline_logger.add_step(
            run_id,
            "TOOL",
            latency_ms=duration_ms,
            status=step.status,
            details={
                "skill": step.skill,
                "status": step.status,
                "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
            },
        )

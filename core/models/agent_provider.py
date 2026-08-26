import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import requests

from agent.provider import (
    AgentDecision,
    DecisionRequest,
    FinalResult,
    InvalidDecision,
    ModelProvider,
    ToolCall,
)
from models.agent_prompt import render


logger = logging.getLogger(__name__)

ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["tool_call", "final"]},
        "skill": {"type": "string"},
        "args": {"type": "object"},
        "facts": {"type": "string"},
    },
    "required": ["type"],
    "additionalProperties": False,
}

DEFAULT_OPTIONS = {
    "temperature": 0.1,
    "num_ctx": 8192,
    "num_predict": 1024,
}

Transport = Callable[[str, dict[str, Any], float], Mapping[str, Any]]


@dataclass(frozen=True)
class ProviderCall:
    model: str
    latency_ms: float
    raw_capped: str
    decision_kind: str


def _default_transport(
    url: str,
    payload: dict[str, Any],
    timeout_s: float,
) -> Mapping[str, Any]:
    response = requests.post(url, json=payload, timeout=timeout_s)
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, Mapping):
        raise ValueError("Ollama returned a non-object response")
    return result


def _map_envelope(raw: str) -> AgentDecision:
    try:
        envelope = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return InvalidDecision("response is not valid JSON", raw)

    if not isinstance(envelope, dict):
        return InvalidDecision("decision envelope must be an object", raw)

    decision_type = envelope.get("type")
    if decision_type == "tool_call":
        skill = envelope.get("skill")
        if not isinstance(skill, str) or not skill.strip():
            return InvalidDecision("tool_call missing skill", raw)
        args = envelope.get("args", {})
        if not isinstance(args, dict):
            return InvalidDecision("tool_call args must be an object", raw)
        return ToolCall(skill.strip(), args)

    if decision_type == "final":
        facts = envelope.get("facts")
        if not isinstance(facts, str) or not facts.strip():
            return InvalidDecision("final missing facts", raw)
        return FinalResult(facts.strip())

    return InvalidDecision("unknown decision type", raw)


def _decision_kind(decision: AgentDecision) -> str:
    if isinstance(decision, ToolCall):
        return "tool_call"
    if isinstance(decision, FinalResult):
        return "final"
    return "invalid"


class OllamaAgentProvider(ModelProvider):
    def __init__(
        self,
        host: str,
        model: str,
        timeout_s: float = 120.0,
        options: dict[str, Any] | None = None,
        transport: Transport | None = None,
        observer: Callable[[ProviderCall], None] | None = None,
    ):
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.host = host.rstrip("/")
        self.model = model
        self.name = f"ollama:{model}"
        self.timeout_s = float(timeout_s)
        self.options = {**DEFAULT_OPTIONS, **(options or {})}
        self.transport = transport or _default_transport
        self.observer = observer

    async def decide(self, request: DecisionRequest) -> AgentDecision:
        payload = {
            "model": self.model,
            "messages": render(request),
            "stream": False,
            "format": ENVELOPE_SCHEMA,
            "options": dict(self.options),
        }
        started = time.perf_counter()
        response = await asyncio.wait_for(
            asyncio.to_thread(
                self.transport,
                f"{self.host}/api/chat",
                payload,
                self.timeout_s,
            ),
            timeout=self.timeout_s,
        )
        if not isinstance(response, Mapping):
            raise ValueError("agent transport returned invalid data")

        message = response.get("message")
        raw_content = message.get("content", "") if isinstance(message, Mapping) else ""
        raw = raw_content if isinstance(raw_content, str) else str(raw_content)
        decision = _map_envelope(raw)
        call = ProviderCall(
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            raw_capped=raw[:500],
            decision_kind=_decision_kind(decision),
        )
        if self.observer is not None:
            try:
                self.observer(call)
            except Exception:
                logger.warning("Agent provider observer failed", exc_info=True)
        return decision

from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias


@dataclass(frozen=True)
class ToolCall:
    skill: str
    args: dict[str, Any]


@dataclass(frozen=True)
class FinalResult:
    facts: str


@dataclass(frozen=True)
class InvalidDecision:
    reason: str
    raw: str | None = None

    def __post_init__(self) -> None:
        if self.raw is not None and len(self.raw) > 500:
            object.__setattr__(self, "raw", self.raw[:500])


AgentDecision: TypeAlias = ToolCall | FinalResult | InvalidDecision


@dataclass(frozen=True)
class Observation:
    source: str
    ok: bool
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class StepRecord:
    index: int
    skill: str
    args: dict[str, Any]
    status: str
    observation: Observation


@dataclass(frozen=True)
class DecisionRequest:
    task: str
    tools: tuple[dict[str, Any], ...]
    steps: tuple[StepRecord, ...]
    feedback: str | None = None


class ModelProvider(Protocol):
    """Render observations under an explicit untrusted-data boundary, then decide."""

    name: str

    async def decide(self, request: DecisionRequest) -> AgentDecision:
        ...


def is_agent_decision(value: object) -> bool:
    return isinstance(value, (ToolCall, FinalResult, InvalidDecision))

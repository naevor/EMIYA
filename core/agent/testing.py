import inspect
from collections.abc import Callable, Iterable

from .provider import AgentDecision, DecisionRequest


ScriptCallable = Callable[[DecisionRequest], AgentDecision]
ScriptItem = AgentDecision | Exception | ScriptCallable


class FakeModelProvider:
    def __init__(self, script: Iterable[ScriptItem], name: str = "fake"):
        self.name = name
        self._script = list(script)
        self._position = 0
        self.requests: list[DecisionRequest] = []

    async def decide(self, request: DecisionRequest) -> AgentDecision:
        self.requests.append(request)
        if self._position >= len(self._script):
            raise RuntimeError("fake provider script exhausted")

        item = self._script[self._position]
        self._position += 1
        if isinstance(item, Exception):
            raise item
        if callable(item):
            result = item(request)
            if inspect.isawaitable(result):
                result = await result
            return result
        return item

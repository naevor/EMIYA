import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Route(str, Enum):
    CACHED = "cached"
    CHAT = "chat"
    AGENT = "agent"


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    task: str
    intent: str | None = None
    response: str | None = None


AGENT_COMMAND = re.compile(r"^\s*/agent(?:\s+(.*?))?\s*$", re.IGNORECASE | re.DOTALL)
ACTION_VERBS = frozenset(
    {
        "прочитай",
        "прочти",
        "открой",
        "покажи",
        "посмотри",
        "read",
        "open",
        "show",
        "cat",
        "check",
    }
)
PATH_EXTENSIONS = frozenset(
    {"py", "md", "json", "txt", "js", "jsx", "toml", "yml", "yaml", "cfg", "ini"}
)

CACHED_PATTERNS = (
    (
        "top_process",
        re.compile(
            r"(?:\btop\s+process\b|\bwhich\s+process\b.*\b(?:memory|ram|cpu)\b|"
            r"какой\s+процесс.*(?:памят|ram|cpu)|(?:ест|жр[её]т).*процесс)",
            re.IGNORECASE,
        ),
    ),
    (
        "ram_usage",
        re.compile(
            r"(?:\bram\b|оперативн\w*\s+памят\w*|сколько\s+памят\w*\s+(?:занят|использ))",
            re.IGNORECASE,
        ),
    ),
    (
        "cpu_usage",
        re.compile(
            r"(?:\bcpu\b|загрузк\w*\s+процессор\w*|процессор\w*\s+(?:занят|загруж))",
            re.IGNORECASE,
        ),
    ),
)

_TELEMETRY_UNAVAILABLE = "telemetry is unavailable. apparently even observation has limits."


def _format_number(value: Any) -> str:
    number = float(value)
    return f"{number:.1f}".rstrip("0").rstrip(".")


def _cached_response(intent: str, snapshot: Mapping[str, Any] | None) -> str:
    if not snapshot:
        return _TELEMETRY_UNAVAILABLE

    if intent == "cpu_usage":
        value = snapshot.get("cpu_percent")
        if value is None:
            return _TELEMETRY_UNAVAILABLE
        return f"cpu: {_format_number(value)}%."

    if intent == "ram_usage":
        percent = snapshot.get("ram_percent")
        if percent is None:
            return _TELEMETRY_UNAVAILABLE
        used = snapshot.get("ram_used_gb")
        total = snapshot.get("ram_total_gb")
        if used is not None and total is not None:
            return (
                f"ram: {_format_number(percent)}% used "
                f"({_format_number(used)}/{_format_number(total)} gb)."
            )
        return f"ram: {_format_number(percent)}% used."

    processes = snapshot.get("top_processes")
    if not isinstance(processes, list) or not processes or not isinstance(processes[0], Mapping):
        return _TELEMETRY_UNAVAILABLE
    process = processes[0]
    name = str(process.get("name") or "unknown")
    details = []
    if process.get("ram") is not None:
        details.append(f"ram {_format_number(process['ram'])}%")
    if process.get("cpu") is not None:
        details.append(f"cpu {_format_number(process['cpu'])}%")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"top process: {name}{suffix}."


def _has_action_verb(text: str) -> bool:
    words = re.findall(r"[\w-]+", text.casefold(), flags=re.UNICODE)
    return any(word in ACTION_VERBS for word in words)


def _has_path_token(text: str) -> bool:
    tokens = re.findall(r"[^\s]+", text)
    for token in tokens:
        clean = token.strip("\"'`()[]{}<>,;:!?")
        if "/" in clean or "\\" in clean:
            return True
        match = re.search(r"\.([A-Za-z0-9]+)$", clean)
        if match and match.group(1).casefold() in PATH_EXTENSIONS:
            return True
    return False


class PreRouter:
    def __init__(self, snapshot_provider: Callable[[], Mapping[str, Any] | None]):
        self.snapshot_provider = snapshot_provider

    def classify(self, text: str) -> RouteDecision:
        original = str(text)
        command = AGENT_COMMAND.match(original)
        if command:
            task = (command.group(1) or "").strip()
            if task:
                return RouteDecision(Route.AGENT, task=task)
            return RouteDecision(
                Route.CHAT,
                task=original,
                response="give me a task after /agent.",
            )

        for intent, pattern in CACHED_PATTERNS:
            if pattern.search(original):
                try:
                    snapshot = self.snapshot_provider()
                except Exception:
                    snapshot = None
                return RouteDecision(
                    Route.CACHED,
                    task=original,
                    intent=intent,
                    response=_cached_response(intent, snapshot),
                )

        if _has_action_verb(original) and _has_path_token(original):
            return RouteDecision(Route.AGENT, task=original)
        return RouteDecision(Route.CHAT, task=original)

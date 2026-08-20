from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class Permission(str, Enum):
    READ = "read"
    WRITE = "write"
    DANGEROUS = "dangerous"


@dataclass
class SkillResult:
    ok: bool
    data: Any | None = None
    error: str | None = None
    duration_ms: int = 0
    truncated: bool = False


@dataclass
class SkillContext:
    allowed_roots: list[Path]
    run_id: str


class Skill(Protocol):
    name: str
    description: str
    args_schema: dict[str, Any]
    permission: Permission
    non_reversible: bool
    timeout_s: float

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        ...

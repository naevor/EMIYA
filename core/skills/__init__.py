from collections.abc import Callable, Mapping
from typing import Any

from .base import Permission, Skill, SkillContext, SkillResult
from .fs import FsListSkill, FsReadSkill
from .registry import SkillRegistry
from .system import SystemProcessesSkill, SystemStatsSkill


SnapshotProvider = Callable[[], Mapping[str, Any]]


def build_core_registry(
    snapshot_provider: SnapshotProvider,
    *,
    fs_read_cap_bytes: int = 65536,
    fs_list_cap: int = 500,
    process_cap: int = 20,
    default_timeout_s: float = 10.0,
) -> SkillRegistry:
    registry = SkillRegistry(default_timeout_s=default_timeout_s)
    registry.register(SystemStatsSkill(snapshot_provider, timeout_s=default_timeout_s))
    registry.register(
        SystemProcessesSkill(
            snapshot_provider,
            process_cap=process_cap,
            timeout_s=default_timeout_s,
        )
    )
    registry.register(FsListSkill(max_entries=fs_list_cap, timeout_s=default_timeout_s))
    registry.register(FsReadSkill(max_bytes=fs_read_cap_bytes, timeout_s=default_timeout_s))
    registry.seal()
    return registry


__all__ = [
    "FsListSkill",
    "FsReadSkill",
    "Permission",
    "Skill",
    "SkillContext",
    "SkillRegistry",
    "SkillResult",
    "SystemProcessesSkill",
    "SystemStatsSkill",
    "build_core_registry",
]

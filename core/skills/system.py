from collections.abc import Callable, Mapping
from typing import Any

from .base import Permission, SkillContext, SkillResult


SnapshotProvider = Callable[[], Mapping[str, Any]]
_NO_ARGS_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _snapshot(provider: SnapshotProvider) -> dict[str, Any]:
    value = provider()
    if not isinstance(value, Mapping):
        raise ValueError("snapshot provider returned invalid data")
    return dict(value)


class SystemStatsSkill:
    name = "system.stats"
    description = "Return the current CPU and memory snapshot."
    args_schema = _NO_ARGS_SCHEMA
    permission = Permission.READ
    non_reversible = False

    def __init__(self, snapshot_provider: SnapshotProvider, timeout_s: float = 10.0):
        self._snapshot_provider = snapshot_provider
        self.timeout_s = float(timeout_s)

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        snapshot = _snapshot(self._snapshot_provider)
        return SkillResult(
            ok=True,
            data={
                "cpu_percent": snapshot.get("cpu_percent"),
                "ram_percent": snapshot.get("ram_percent"),
                "ram_used_gb": snapshot.get("ram_used_gb"),
                "ram_total_gb": snapshot.get("ram_total_gb"),
            },
        )


class SystemProcessesSkill:
    name = "system.processes"
    description = "Return the current top processes from the system snapshot."
    args_schema = _NO_ARGS_SCHEMA
    permission = Permission.READ
    non_reversible = False

    def __init__(
        self,
        snapshot_provider: SnapshotProvider,
        process_cap: int = 20,
        timeout_s: float = 10.0,
    ):
        if process_cap <= 0:
            raise ValueError("process_cap must be positive")
        self._snapshot_provider = snapshot_provider
        self.process_cap = int(process_cap)
        self.timeout_s = float(timeout_s)

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        snapshot = _snapshot(self._snapshot_provider)
        raw_processes = snapshot.get("top_processes") or []
        if not isinstance(raw_processes, list):
            raise ValueError("snapshot processes are invalid")

        processes = []
        for process in raw_processes[: self.process_cap]:
            if not isinstance(process, Mapping):
                continue
            processes.append(
                {
                    "name": process.get("name"),
                    "cpu": process.get("cpu"),
                    "ram": process.get("ram"),
                }
            )

        return SkillResult(
            ok=True,
            data={"processes": processes, "count": len(processes)},
            truncated=len(raw_processes) > self.process_cap,
        )

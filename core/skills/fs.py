import heapq
import os
from pathlib import Path
from typing import Any

from .base import Permission, SkillContext, SkillResult


_PATH_ARGS_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1},
    },
    "required": ["path"],
    "additionalProperties": False,
}


class PathSandboxError(ValueError):
    pass


def _canonical_path(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    canonical = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(resolved))))
    return Path(canonical)


def _contains(root: Path, target: Path) -> bool:
    try:
        root_text = os.path.normcase(os.fspath(root))
        target_text = os.path.normcase(os.fspath(target))
        return os.path.commonpath([root_text, target_text]) == root_text
    except (OSError, ValueError):
        return False


def resolve_sandboxed_path(raw_path: str, allowed_roots: list[Path]) -> Path:
    roots = []
    for root in allowed_roots:
        try:
            resolved_root = _canonical_path(Path(root))
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved_root.is_dir():
            roots.append(resolved_root)

    if not roots:
        raise PathSandboxError("no allowed roots")

    requested = Path(raw_path).expanduser()
    candidates = [requested] if requested.is_absolute() else [root / requested for root in roots]

    for candidate in candidates:
        try:
            resolved_target = _canonical_path(candidate)
        except (OSError, RuntimeError, ValueError):
            continue
        if any(_contains(root, resolved_target) for root in roots):
            return resolved_target

    raise PathSandboxError("path is outside allowed roots")


def _path_error(exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return "path not found"
    if isinstance(exc, PermissionError):
        return "path is not readable"
    return "filesystem read failed"


class FsReadSkill:
    name = "fs.read"
    description = "Read text content from a file inside an allowed root."
    args_schema = _PATH_ARGS_SCHEMA
    permission = Permission.READ
    non_reversible = False

    def __init__(self, max_bytes: int = 65536, timeout_s: float = 10.0):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = int(max_bytes)
        self.timeout_s = float(timeout_s)

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        try:
            path = resolve_sandboxed_path(args["path"], ctx.allowed_roots)
        except PathSandboxError as exc:
            return SkillResult(ok=False, error=str(exc))

        if not path.exists():
            return SkillResult(ok=False, error="path not found")
        if not path.is_file():
            return SkillResult(ok=False, error="path is not a file")

        try:
            with path.open("rb") as handle:
                payload = handle.read(self.max_bytes + 1)
        except OSError as exc:
            return SkillResult(ok=False, error=_path_error(exc))

        truncated = len(payload) > self.max_bytes
        if truncated:
            payload = payload[: self.max_bytes]

        return SkillResult(
            ok=True,
            data={
                "path": str(path),
                "content": payload.decode("utf-8", errors="replace"),
                "bytes_read": len(payload),
            },
            truncated=truncated,
        )


class FsListSkill:
    name = "fs.list"
    description = "List entries in one directory inside an allowed root."
    args_schema = _PATH_ARGS_SCHEMA
    permission = Permission.READ
    non_reversible = False

    def __init__(self, max_entries: int = 500, timeout_s: float = 10.0):
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.max_entries = int(max_entries)
        self.timeout_s = float(timeout_s)

    @staticmethod
    def _entry_data(entry: os.DirEntry[str]) -> dict[str, Any]:
        if entry.is_symlink():
            entry_type = "symlink"
            size = None
        elif entry.is_dir(follow_symlinks=False):
            entry_type = "directory"
            size = None
        elif entry.is_file(follow_symlinks=False):
            entry_type = "file"
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = None
        else:
            entry_type = "other"
            size = None
        return {"name": entry.name, "type": entry_type, "size": size}

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        try:
            path = resolve_sandboxed_path(args["path"], ctx.allowed_roots)
        except PathSandboxError as exc:
            return SkillResult(ok=False, error=str(exc))

        if not path.exists():
            return SkillResult(ok=False, error="path not found")
        if not path.is_dir():
            return SkillResult(ok=False, error="path is not a directory")

        try:
            with os.scandir(path) as iterator:
                selected = heapq.nsmallest(
                    self.max_entries + 1,
                    iterator,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
            truncated = len(selected) > self.max_entries
            entries = [self._entry_data(entry) for entry in selected[: self.max_entries]]
        except OSError as exc:
            return SkillResult(ok=False, error=_path_error(exc))

        return SkillResult(
            ok=True,
            data={"path": str(path), "entries": entries, "count": len(entries)},
            truncated=truncated,
        )

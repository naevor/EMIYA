import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)

# Provisional agent default from B3 live acceptance: Gemma scored 10/10;
# qwen3:4b-instruct-2507-q4_K_M scored 0/10 on structured-output compatibility.
# Final model selection belongs to the benchmark stage.
DEFAULT_AGENT_MODEL = "gemma4:e4b"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class AgentRuntimeConfig:
    model: str
    ollama_host: str
    allowed_roots: tuple[Path, ...]


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _canonical_directory(raw_path: str | Path) -> Path | None:
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    return path if path.is_dir() else None


def _parse_roots(raw: str, fallback: Path) -> tuple[Path, ...]:
    roots = []
    seen = set()
    invalid_entries = 0
    for item in raw.split(os.pathsep):
        if not item.strip():
            continue
        root = _canonical_directory(item.strip())
        if root is None:
            invalid_entries += 1
            continue
        key = os.path.normcase(os.path.realpath(os.fspath(root)))
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)

    if invalid_entries:
        logger.warning("[agent] ignored %s invalid allowed root(s)", invalid_entries)
    if roots:
        return tuple(roots)

    logger.warning("[agent] invalid or empty roots override; using repository root")
    return (fallback,)


def load_agent_runtime_config(
    environ: Mapping[str, str] | None = None,
    *,
    repo_root: Path | None = None,
) -> AgentRuntimeConfig:
    env = os.environ if environ is None else environ
    fallback_root = _canonical_directory(repo_root or repository_root())
    if fallback_root is None:
        raise RuntimeError("repository root is unavailable")

    model = env["EMIYA_AGENT_MODEL"] if "EMIYA_AGENT_MODEL" in env else DEFAULT_AGENT_MODEL
    raw_host = env.get("EMIYA_OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
    ollama_host = raw_host.strip().rstrip("/") or DEFAULT_OLLAMA_HOST
    raw_roots = env.get("EMIYA_AGENT_ROOTS")
    roots = (fallback_root,) if raw_roots is None else _parse_roots(raw_roots, fallback_root)
    return AgentRuntimeConfig(
        model=model,
        ollama_host=ollama_host,
        allowed_roots=roots,
    )


def log_agent_runtime_config(config: AgentRuntimeConfig) -> None:
    roots = os.pathsep.join(str(root) for root in config.allowed_roots)
    logger.warning(
        "[agent] model=%s ollama=%s roots=%s",
        config.model,
        config.ollama_host,
        roots,
    )

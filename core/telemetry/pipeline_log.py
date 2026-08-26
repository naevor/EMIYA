import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "logs" / "pipeline.jsonl"
AGENT_PIPELINE_STAGES = ("ROUTE", "CACHED", "DECIDE", "TOOL", "VOICE")


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _compact_value(value: Any, limit: int = 600) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "...[truncated]"
    if isinstance(value, dict):
        return {key: _compact_value(item, limit=limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact_value(item, limit=limit) for item in value[:20]]
    return value


class PipelineLogger:
    def __init__(self, maxlen: int = 100):
        self._runs: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._active: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start_request(self, request_id: str, text: str, context: dict[str, Any] | None = None) -> None:
        run = {
            "request_id": request_id,
            "status": "active",
            "started_at": _now(),
            "finished_at": None,
            "latency_ms": None,
            "input": text,
            "context": _compact_value(context or {}, limit=300),
            "steps": [],
            "_t0": time.perf_counter(),
        }
        with self._lock:
            self._active[request_id] = run
            self._runs.append(run)

    def add_step(
        self,
        request_id: str,
        name: str,
        status: str = "ok",
        latency_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        step = {
            "name": name,
            "status": status,
            "timestamp": _now(),
            "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
            "details": details or {},
        }
        with self._lock:
            run = self._active.get(request_id)
            if run:
                run["steps"].append(step)

    def finish_request(
        self,
        request_id: str,
        status: str = "ok",
        details: dict[str, Any] | None = None,
        dump: bool = False,
    ) -> None:
        with self._lock:
            run = self._active.pop(request_id, None)
            if not run:
                return
            run["status"] = status
            run["finished_at"] = _now()
            run["latency_ms"] = round((time.perf_counter() - run.pop("_t0")) * 1000, 1)
            if details:
                run["details"] = details
            snapshot = json.loads(json.dumps(run, ensure_ascii=False, default=str))

        if dump:
            self.dump_run(snapshot)

    def recent(self, limit: int = 100, compact: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            runs = []
            for run in list(self._runs)[-max(1, int(limit)):]:
                item = dict(run)
                item.pop("_t0", None)
                runs.append(item)
            serializable = json.loads(json.dumps(runs, ensure_ascii=False, default=str))
        if compact:
            return [_compact_value(run, limit=400) for run in serializable]
        return serializable

    def dump_run(self, run: dict[str, Any], path: Path = LOG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(run, ensure_ascii=False) + "\n")


pipeline_logger = PipelineLogger()

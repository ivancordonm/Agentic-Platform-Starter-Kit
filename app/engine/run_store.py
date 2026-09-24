"""Bounded in-memory run and event storage behind a replaceable protocol."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunRepository(Protocol):
    def create(self, run_id: str, kind: str, revision: int, target: str) -> None: ...
    def event(self, run_id: str, event: dict[str, Any]) -> None: ...
    def finish(
        self, run_id: str, status: str, duration_ms: int, result: dict[str, Any] | None,
        error: str | None = None,
    ) -> None: ...
    def get(self, run_id: str) -> dict[str, Any] | None: ...
    def list(self, limit: int = 20) -> list[dict[str, Any]]: ...


class InMemoryRunStore:
    def __init__(self, max_runs: int = 100, max_events: int = 5000) -> None:
        if max_runs < 1 or max_events < 1:
            raise ValueError("Run store bounds must be positive")
        self.max_runs = max_runs
        self.max_events = max_events
        self._lock = Lock()
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def create(self, run_id: str, kind: str, revision: int, target: str) -> None:
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id, "kind": kind, "target": target,
                "revision": revision, "status": "running", "started_at": _now(),
                "finished_at": None, "duration_ms": None, "result": None,
                "error": None, "events": [],
            }
            while len(self._runs) > self.max_runs:
                self._runs.popitem(last=False)

    def event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return  # A very long-running run may have been evicted.
            events: list[dict[str, Any]] = record["events"]
            if len(events) < self.max_events:
                events.append({"time": _now(), **deepcopy(event)})

    def finish(
        self, run_id: str, status: str, duration_ms: int, result: dict[str, Any] | None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return
            record.update(
                status=status, finished_at=_now(), duration_ms=duration_ms,
                result=deepcopy(result), error=error,
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._runs.get(run_id)
            return deepcopy(record) if record is not None else None

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._runs.values())[-limit:]
            return [
                {key: deepcopy(value) for key, value in record.items()
                 if key not in {"events", "result"}}
                for record in reversed(records)
            ]

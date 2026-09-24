"""Optional durable SQLite implementation of the run repository protocol."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteRunStore:
    def __init__(self, path: Path, max_runs: int = 1000, max_events: int = 5000) -> None:
        if max_runs < 1 or max_events < 1:
            raise ValueError("Run store bounds must be positive")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.max_runs = max_runs
        self.max_events = max_events
        self._lock = Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS runs ("
            "run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, record TEXT NOT NULL)"
        )
        self._conn.commit()

    def _get_unlocked(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT record FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _save_unlocked(self, record: dict[str, Any]) -> None:
        self._conn.execute(
            "UPDATE runs SET record = ? WHERE run_id = ?",
            (json.dumps(record), record["run_id"]),
        )
        self._conn.commit()

    def create(self, run_id: str, kind: str, revision: int, target: str) -> None:
        started_at = _now()
        record = {
            "run_id": run_id, "kind": kind, "target": target,
            "revision": revision, "status": "running", "started_at": started_at,
            "finished_at": None, "duration_ms": None, "result": None,
            "error": None, "events": [],
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (run_id, created_at, record) VALUES (?, ?, ?)",
                (run_id, started_at, json.dumps(record)),
            )
            self._conn.execute(
                "DELETE FROM runs WHERE run_id IN ("
                "SELECT run_id FROM runs ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?) ",
                (self.max_runs,),
            )
            self._conn.commit()

    def event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            record = self._get_unlocked(run_id)
            if record is None:
                return
            if len(record["events"]) < self.max_events:
                record["events"].append({"time": _now(), **deepcopy(event)})
                self._save_unlocked(record)

    def finish(
        self, run_id: str, status: str, duration_ms: int, result: dict[str, Any] | None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = self._get_unlocked(run_id)
            if record is None:
                return
            record.update(
                status=status, finished_at=_now(), duration_ms=duration_ms,
                result=deepcopy(result), error=error,
            )
            self._save_unlocked(record)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._get_unlocked(run_id)

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT record FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {key: value for key, value in json.loads(row[0]).items()
             if key not in {"events", "result"}}
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

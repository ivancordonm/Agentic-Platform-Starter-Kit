"""Opt-in API authentication and durable run history."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from agents import Agent

from app.api.routes import create_app
from app.engine.sqlite_run_store import SQLiteRunStore

PROJECT_DIR = Path(__file__).resolve().parents[2] / "project"


@dataclass
class FakeResult:
    final_output: Any


class FakeRunner:
    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> FakeResult:
        return FakeResult({"summary": "Done", "findings": []})


@pytest.mark.asyncio
async def test_api_token_protects_all_except_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_API_TOKEN", "top-secret")
    app = create_app(PROJECT_DIR, FakeRunner())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        unauthorized = await client.get("/project")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["WWW-Authenticate"] == "Bearer"
        assert (await client.get("/runs")).status_code == 401
        assert (await client.post("/reload")).status_code == 401
        wrong = await client.get("/project", headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401
        authorized = await client.get(
            "/project", headers={"Authorization": "Bearer top-secret"}
        )
        assert authorized.status_code == 200
        assert "top-secret" not in authorized.text


@pytest.mark.asyncio
async def test_sqlite_run_history_survives_app_recreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PLATFORM_API_TOKEN", raising=False)
    monkeypatch.setenv("RUN_DB_PATH", str(tmp_path / "traces.sqlite3"))
    first = httpx.ASGITransport(app=create_app(PROJECT_DIR, FakeRunner()))
    async with httpx.AsyncClient(transport=first, base_url="http://test") as client:
        response = await client.post("/agents/analyzer/run", json={"input": "Hi"})
        assert response.status_code == 200
        run_id = response.json()["run_id"]
    second = httpx.ASGITransport(app=create_app(PROJECT_DIR, FakeRunner()))
    async with httpx.AsyncClient(transport=second, base_url="http://test") as client:
        detail = (await client.get(f"/runs/{run_id}")).json()
        assert detail["status"] == "completed"
        assert detail["result"]["result"]["summary"] == "Done"
        assert (await client.get("/runs")).json()[0]["run_id"] == run_id


def test_sqlite_store_bounds_and_event_cap(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "data" / "runs.sqlite3", max_runs=2, max_events=1)
    store.create("one", "agent", 1, "a")
    store.event("one", {"type": "start"})
    store.event("one", {"type": "ignored"})
    first = store.get("one")
    assert first is not None and len(first["events"]) == 1
    store.create("two", "agent", 1, "b")
    store.create("three", "agent", 1, "c")
    assert store.get("one") is None
    assert [item["run_id"] for item in store.list()] == ["three", "two"]
    store.close()

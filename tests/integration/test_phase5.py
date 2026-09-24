"""Versioned reload and server-side run traces without external model calls."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from agents import Agent

from app.api.routes import create_app
from app.engine.exceptions import ConfigurationError
from app.engine.runtime_manager import RuntimeManager

PROJECT_DIR = Path(__file__).resolve().parents[2] / "project"


@dataclass
class FakeResult:
    final_output: Any


class FakeRunner:
    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[Agent[Any]] = []

    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> FakeResult:
        self.calls.append(agent)
        if agent.name == self.fail:
            raise RuntimeError("fake failure")
        outputs = {
            "analyzer": {"summary": "A", "findings": []},
            "reviewer": {"approved": True, "concerns": []},
            "finalizer": {"answer": "Done", "confidence": 1.0},
        }
        return FakeResult(outputs[agent.name])


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(PROJECT_DIR, root)
    return root


def test_failed_reload_keeps_revision_and_prompt_snapshot(project_copy: Path) -> None:
    manager = RuntimeManager(project_copy, FakeRunner())
    original = manager.snapshot()
    prompt_path = project_copy / "prompts" / "analyzer.md"
    original_prompt = original.runtime.prompts.load("prompts/analyzer.md")
    prompt_path.write_text("Changed prompt: {{ input }}")
    assert original.runtime.prompts.load("prompts/analyzer.md") == original_prompt
    config_path = project_copy / "agents.yaml"
    old_config = config_path.read_text()
    config_path.write_text(old_config.replace("model: default", "model: missing", 1))
    with pytest.raises(ConfigurationError, match="Unknown model"):
        manager.reload()
    assert manager.snapshot() is original
    config_path.write_text(old_config)
    updated = manager.reload()
    assert updated.revision == 2
    assert updated.runtime.prompts.load("prompts/analyzer.md") == "Changed prompt: {{ input }}"
    assert original.runtime.prompts.load("prompts/analyzer.md") == original_prompt


@pytest.mark.asyncio
async def test_api_records_workflow_events_and_run_detail() -> None:
    transport = httpx.ASGITransport(app=create_app(PROJECT_DIR, FakeRunner()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/run", json={"input": "Hi"})
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        listing = (await client.get("/runs")).json()
        assert listing[0]["run_id"] == run_id
        assert listing[0]["revision"] == 1
        detail = (await client.get(f"/runs/{run_id}")).json()
        assert detail["status"] == "completed"
        assert detail["result"]["result"]["answer"] == "Done"
        events = (await client.get(f"/runs/{run_id}/events")).json()
        assert [event["type"] for event in events] == [
            "run_started", "node_started", "node_completed",
            "node_started", "node_completed", "node_started", "node_completed",
            "run_completed",
        ]
        assert events[2]["output"]["summary"] == "A"
        assert (await client.get("/runs/unknown")).status_code == 404


@pytest.mark.asyncio
async def test_failed_run_has_trace_id_and_partial_events() -> None:
    transport = httpx.ASGITransport(app=create_app(PROJECT_DIR, FakeRunner(fail="reviewer")))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/run", json={"input": "Hi"})
        assert response.status_code == 502
        run_id = response.headers["X-Run-ID"]
        detail = (await client.get(f"/runs/{run_id}")).json()
        assert detail["status"] == "failed"
        assert detail["error"] == "Agent 'reviewer' execution failed"
        assert [event["type"] for event in detail["events"]] == [
            "run_started", "node_started", "node_completed",
            "node_started", "node_failed", "run_failed",
        ]


@pytest.mark.asyncio
async def test_reload_endpoint_preserves_old_revision_on_error(project_copy: Path) -> None:
    transport = httpx.ASGITransport(app=create_app(project_copy, FakeRunner()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/project")).json()["revision"] == 1
        config_path = project_copy / "agents.yaml"
        old_config = config_path.read_text()
        config_path.write_text(old_config.replace("model: default", "model: missing", 1))
        assert (await client.post("/reload")).status_code == 422
        assert (await client.get("/project")).json()["revision"] == 1
        config_path.write_text(old_config)
        response = await client.post("/reload")
        assert response.json() == {"status": "reloaded", "revision": 2}
        assert (await client.get("/project")).json()["revision"] == 2


class BlockingRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> FakeResult:
        if agent.name == "analyzer" and not self.started.is_set():
            self.started.set()
            await self.release.wait()
        return await super().run(agent, input_text, max_turns)


@pytest.mark.asyncio
async def test_in_flight_run_keeps_original_revision_and_prompt(project_copy: Path) -> None:
    runner = BlockingRunner()
    transport = httpx.ASGITransport(app=create_app(project_copy, runner))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task = asyncio.create_task(client.post("/run", json={"input": "Hi"}))
        await asyncio.wait_for(runner.started.wait(), timeout=2)
        prompt = project_copy / "prompts" / "reviewer.md"
        prompt.write_text("New reviewer prompt")
        assert (await client.post("/reload")).json()["revision"] == 2
        runner.release.set()
        response = await task
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        assert (await client.get(f"/runs/{run_id}")).json()["revision"] == 1
        reviewer = next(agent for agent in runner.calls if agent.name == "reviewer")
        assert "Inspect the analysis" in str(reviewer.instructions)
        assert "New reviewer prompt" not in str(reviewer.instructions)

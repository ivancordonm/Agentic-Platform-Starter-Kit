"""Sequential and conditional workflows with a fake SDK runner."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from agents import Agent

from app.api.routes import create_app
from app.engine.agent_executor import AgentExecutor
from app.engine.exceptions import ConfigurationError, WorkflowExecutionError
from app.engine.project_runtime import ProjectRuntime
from app.engine.workflow_executor import WorkflowExecutor

PROJECT_DIR = Path(__file__).resolve().parents[2] / "project"


@dataclass
class FakeResult:
    final_output: Any


class FakeRunner:
    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.calls: list[Agent[Any]] = []

    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> FakeResult:
        self.calls.append(agent)
        outputs: dict[str, Any] = {
            "analyzer": {"summary": "A", "findings": ["fact"]},
            "reviewer": {"approved": self.approved, "concerns": []},
            "finalizer": {"answer": "Done", "confidence": 0.9},
        }
        return FakeResult(outputs[agent.name])


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(PROJECT_DIR, root)
    return root


def conditional_workflow(root: Path) -> None:
    path = root / "workflow.yaml"
    path.write_text(
        path.read_text().replace(
            "    - from: review\n      to: finalize",
            "    - from: review\n"
            "      select: results.review.output.approved\n"
            "      cases:\n        'true': finalize\n"
            "      default: fail",
        )
    )


@pytest.mark.asyncio
async def test_sequential_workflow_returns_selected_output_and_prior_results() -> None:
    runtime = ProjectRuntime.from_directory(PROJECT_DIR)
    fake = FakeRunner()
    result = await WorkflowExecutor(runtime, AgentExecutor(runtime, fake)).run("Input", {"id": 1})
    assert result.status == "completed"
    assert result.steps == 3
    assert result.result == {"answer": "Done", "confidence": 0.9}
    assert list(result.results) == ["analyze", "review", "finalize"]
    assert [agent.name for agent in fake.calls] == ["analyzer", "reviewer", "finalizer"]
    assert '"summary": "A"' in str(fake.calls[1].instructions)
    assert '"approved": true' in str(fake.calls[2].instructions)


@pytest.mark.asyncio
async def test_conditional_routing_and_failed_route(project_copy: Path) -> None:
    conditional_workflow(project_copy)
    runtime = ProjectRuntime.from_directory(project_copy)
    approved = FakeRunner()
    result = await WorkflowExecutor(runtime, AgentExecutor(runtime, approved)).run("Hi", {})
    assert result.steps == 3
    rejected = FakeRunner(approved=False)
    with pytest.raises(WorkflowExecutionError, match="No successful route"):
        await WorkflowExecutor(runtime, AgentExecutor(runtime, rejected)).run("Hi", {})
    assert [agent.name for agent in rejected.calls] == ["analyzer", "reviewer"]


@pytest.mark.asyncio
async def test_api_run_returns_workflow_result_and_missing_key_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner()
    transport = httpx.ASGITransport(app=create_app(PROJECT_DIR, fake))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/run", json={"input": "Hi", "context": {}})
    assert response.status_code == 200
    assert response.json()["result"]["answer"] == "Done"
    assert response.json()["steps"] == 3
    assert [entry["node"] for entry in response.json()["history"]] == [
        "analyze", "review", "finalize"
    ]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    transport = httpx.ASGITransport(app=create_app(PROJECT_DIR))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/run", json={"input": "Hi"})
    assert response.status_code == 503


def test_ambiguous_transitions_rejected(project_copy: Path) -> None:
    path = project_copy / "workflow.yaml"
    path.write_text(
        path.read_text().replace(
            "    - from: analyze\n",
            "    - from: analyze\n      to: review\n    - from: analyze\n",
            1,
        )
    )
    runtime = ProjectRuntime.from_directory(project_copy)
    with pytest.raises(ConfigurationError, match="ambiguous transitions"):
        WorkflowExecutor(runtime, AgentExecutor(runtime, FakeRunner()))


def test_cycle_without_path_to_end_is_rejected(project_copy: Path) -> None:
    path = project_copy / "workflow.yaml"
    path.write_text(
        path.read_text().replace(
            "    - from: finalize\n      to: END", "    - from: finalize\n      to: analyze"
        )
    )
    runtime = ProjectRuntime.from_directory(project_copy)
    with pytest.raises(ConfigurationError, match="without a path to END"):
        WorkflowExecutor(runtime, AgentExecutor(runtime, FakeRunner()))


@pytest.mark.asyncio
async def test_max_steps_is_enforced(project_copy: Path) -> None:
    path = project_copy / "workflow.yaml"
    path.write_text(path.read_text().replace("max_steps: 10", "max_steps: 2"))
    runtime = ProjectRuntime.from_directory(project_copy)
    fake = FakeRunner()
    with pytest.raises(WorkflowExecutionError, match="max_steps"):
        await WorkflowExecutor(runtime, AgentExecutor(runtime, fake)).run("Hi", {})
    assert [agent.name for agent in fake.calls] == ["analyzer", "reviewer"]


@pytest.mark.asyncio
async def test_missing_output_selector_is_error(project_copy: Path) -> None:
    path = project_copy / "workflow.yaml"
    path.write_text(
        path.read_text().replace(
            "select: results.finalize.output", "select: results.finalize.output.missing"
        )
    )
    runtime = ProjectRuntime.from_directory(project_copy)
    with pytest.raises(WorkflowExecutionError, match="did not resolve"):
        await WorkflowExecutor(runtime, AgentExecutor(runtime, FakeRunner())).run("Hi", {})

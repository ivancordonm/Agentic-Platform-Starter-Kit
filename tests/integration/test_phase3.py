"""Parallel fan-in, bounded loops and complete graph validation."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from agents import Agent

from app.engine.agent_executor import AgentExecutor
from app.engine.exceptions import ConfigurationError, WorkflowExecutionError
from app.engine.project_runtime import ProjectRuntime
from app.engine.workflow_executor import WorkflowExecutor

PROJECT_DIR = Path(__file__).resolve().parents[2] / "project"


@dataclass
class FakeResult:
    final_output: Any


class TrackingRunner:
    def __init__(self, *, fail: str | None = None, approvals: list[bool] | None = None) -> None:
        self.fail = fail
        self.approvals = approvals or [True]
        self.calls: list[str] = []
        self.active = 0
        self.peak = 0

    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> FakeResult:
        self.calls.append(agent.name)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.01)
            if agent.name == self.fail:
                raise RuntimeError("deliberate fake failure")
            if agent.name == "analyzer":
                return FakeResult({"summary": "A", "findings": []})
            if agent.name == "reviewer":
                return FakeResult({"approved": self.approvals.pop(0), "concerns": []})
            return FakeResult({"answer": "Done", "confidence": 1.0})
        finally:
            self.active -= 1


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(PROJECT_DIR, root)
    return root


def configure_parallel(root: Path, *, policy: str = "fail_fast", concurrency: int = 2) -> None:
    path = root / "workflow.yaml"
    config = yaml.safe_load(path.read_text())
    workflow = config["workflow"]
    workflow["start"] = "fan"
    workflow["limits"]["max_concurrency"] = concurrency
    workflow["nodes"] = {
        "fan": {
            "type": "parallel",
            "branches": {
                "analysis": {"agent": "analyzer"},
                "review": {"agent": "reviewer"},
                "final": {"agent": "finalizer"},
            },
            "failure_policy": policy,
        }
    }
    workflow["transitions"] = [{"from": "fan", "to": "END"}]
    workflow["output"]["select"] = "results.fan.output"
    path.write_text(yaml.safe_dump(config, sort_keys=False))


def configure_loop(root: Path, *, visits: int = 2, max_steps: int = 10) -> None:
    path = root / "workflow.yaml"
    config = yaml.safe_load(path.read_text())
    workflow = config["workflow"]
    workflow["limits"]["max_steps"] = max_steps
    workflow["nodes"] = {
        "analyze": {"type": "agent", "agent": "analyzer", "max_visits": visits},
        "review": {"type": "agent", "agent": "reviewer", "max_visits": visits},
    }
    workflow["transitions"] = [
        {"from": "analyze", "to": "review"},
        {
            "from": "review",
            "select": "results.review.output.approved",
            "cases": {"true": "END", "false": "analyze"},
            "default": "fail",
        },
    ]
    workflow["output"]["select"] = "results.review.output"
    path.write_text(yaml.safe_dump(config, sort_keys=False))


@pytest.mark.asyncio
async def test_parallel_fan_in_honors_concurrency_and_order(project_copy: Path) -> None:
    configure_parallel(project_copy)
    runtime = ProjectRuntime.from_directory(project_copy)
    fake = TrackingRunner()
    result = await WorkflowExecutor(runtime, AgentExecutor(runtime, fake)).run("Hi", {})
    assert result.steps == 1
    assert result.result["analysis"]["summary"] == "A"
    assert result.result["final"]["answer"] == "Done"
    assert list(result.result) == ["analysis", "review", "final"]
    assert fake.peak == 2
    assert result.history[0]["node"] == "fan"
    assert result.results["fan"]["branches"]["review"]["status"] == "completed"


@pytest.mark.asyncio
async def test_parallel_collect_errors_returns_partial_results(project_copy: Path) -> None:
    configure_parallel(project_copy, policy="collect_errors")
    runtime = ProjectRuntime.from_directory(project_copy)
    fake = TrackingRunner(fail="reviewer")
    result = await WorkflowExecutor(runtime, AgentExecutor(runtime, fake)).run("Hi", {})
    assert result.result["review"] is None
    assert result.result["analysis"]["summary"] == "A"
    assert result.results["fan"]["branches"]["review"]["status"] == "failed"


@pytest.mark.asyncio
async def test_parallel_fail_fast_raises(project_copy: Path) -> None:
    configure_parallel(project_copy)
    runtime = ProjectRuntime.from_directory(project_copy)
    with pytest.raises(Exception, match="execution failed"):
        fake = TrackingRunner(fail="reviewer")
        executor = WorkflowExecutor(runtime, AgentExecutor(runtime, fake))
        await executor.run("Hi", {})


@pytest.mark.asyncio
async def test_bounded_loop_retains_history_and_latest_results(project_copy: Path) -> None:
    configure_loop(project_copy)
    runtime = ProjectRuntime.from_directory(project_copy)
    fake = TrackingRunner(approvals=[False, True])
    result = await WorkflowExecutor(runtime, AgentExecutor(runtime, fake)).run("Hi", {})
    assert result.steps == 4
    assert [item["node"] for item in result.history] == [
        "analyze", "review", "analyze", "review"
    ]
    assert [item["visit"] for item in result.history] == [1, 1, 2, 2]
    assert result.result["approved"] is True
    assert result.results["review"]["output"]["approved"] is True


@pytest.mark.asyncio
async def test_loop_visit_limit_stops_run(project_copy: Path) -> None:
    configure_loop(project_copy)
    runtime = ProjectRuntime.from_directory(project_copy)
    fake = TrackingRunner(approvals=[False, False])
    with pytest.raises(WorkflowExecutionError, match="max_visits"):
        await WorkflowExecutor(runtime, AgentExecutor(runtime, fake)).run("Hi", {})
    assert fake.calls == ["analyzer", "reviewer", "analyzer", "reviewer"]


def test_cycle_requires_visit_bounds(project_copy: Path) -> None:
    configure_loop(project_copy, visits=1)
    runtime = ProjectRuntime.from_directory(project_copy)
    with pytest.raises(ConfigurationError, match="max_visits"):
        WorkflowExecutor(runtime, AgentExecutor(runtime, TrackingRunner()))


def test_unreachable_node_is_rejected(project_copy: Path) -> None:
    path = project_copy / "workflow.yaml"
    config = yaml.safe_load(path.read_text())
    workflow = config["workflow"]
    workflow["transitions"][1]["to"] = "END"
    workflow["transitions"] = workflow["transitions"][:2] + [
        {"from": "finalize", "to": "END"}
    ]
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    runtime = ProjectRuntime.from_directory(project_copy)
    with pytest.raises(ConfigurationError, match="Unreachable"):
        WorkflowExecutor(runtime, AgentExecutor(runtime, TrackingRunner()))


def test_unknown_result_node_selector_is_rejected(project_copy: Path) -> None:
    path = project_copy / "workflow.yaml"
    config = yaml.safe_load(path.read_text())
    config["workflow"]["output"]["select"] = "results.missing.output"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    runtime = ProjectRuntime.from_directory(project_copy)
    with pytest.raises(ConfigurationError, match="references unknown node"):
        WorkflowExecutor(runtime, AgentExecutor(runtime, TrackingRunner()))

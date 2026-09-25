"""Offline contract and route tests for the isolated support-triage project."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents import Agent

from app.engine.agent_executor import AgentExecutor
from app.engine.project_runtime import ProjectRuntime
from app.engine.workflow_executor import WorkflowExecutor
from examples.support_triage.mcp_server import search_articles

ROOT = Path(__file__).resolve().parents[1] / "examples" / "support_triage" / "project"


@dataclass
class FakeResult:
    final_output: Any


class FakeRunner:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.calls: list[str] = []

    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> FakeResult:
        self.calls.append(agent.name)
        priority = "P1" if self.scenario == "critical" else "P3"
        route = "escalate" if self.scenario == "critical" else "knowledge"
        article_ids = ["KB-202"] if self.scenario == "documented" else []
        outputs: dict[str, Any] = {
            "classifier": {
                "priority": priority,
                "category": "access" if self.scenario == "critical" else "billing",
                "route": route,
                "reason": "Fixture classification",
            },
            "knowledge_lookup": {
                "article_ids": article_ids,
                "facts": ["Download in Settings > Billing > Invoices"] if article_ids else [],
                "no_match": not article_ids,
            },
            "escalation": {"message": "Human investigation needed", "article_ids": [],
                           "needs_human": True},
            "composer": {"message": "Documented step" if article_ids else "Ask support",
                         "article_ids": article_ids, "needs_human": not article_ids},
            "reviewer": {
                "priority": priority,
                "category": "access" if self.scenario == "critical" else "billing",
                "response": "Final response",
                "article_ids": article_ids,
                "needs_human": self.scenario != "documented",
            },
        }
        return FakeResult(outputs[agent.name])


def test_project_loads_and_kb_does_not_guess() -> None:
    runtime = ProjectRuntime.from_directory(ROOT)
    WorkflowExecutor(runtime, AgentExecutor(runtime))
    assert len(runtime.agents.all()) == 5
    assert list(runtime.tools.all()) == ["support_kb"]
    assert [item["id"] for item in search_articles("Necesito factura")] == ["KB-202"]
    assert search_articles("exportar auditoría XML") == []
    assert "KB-202" not in (ROOT / "prompts" / "knowledge_lookup.md").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_path", "expected_human", "expected_articles"),
    [
        ("critical", ["classify", "escalate", "review"], True, []),
        ("documented", ["classify", "lookup", "compose", "review"], False, ["KB-202"]),
        ("unknown", ["classify", "lookup", "compose", "review"], True, []),
    ],
)
async def test_workflow_routes(
    scenario: str, expected_path: list[str], expected_human: bool,
    expected_articles: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ProjectRuntime.from_directory(ROOT)
    async def no_network_mcp(tool_names: list[str], stack: Any) -> list[Any]:
        return []

    monkeypatch.setattr(runtime.tools, "open_mcp", no_network_mcp)
    fake = FakeRunner(scenario)
    run = await WorkflowExecutor(runtime, AgentExecutor(runtime, fake)).run("Ticket", {})
    assert run.status == "completed"
    assert [entry["node"] for entry in run.history] == expected_path
    assert run.steps == len(expected_path)
    assert run.result["needs_human"] is expected_human
    assert run.result["article_ids"] == expected_articles
    assert fake.calls == [
        {
            "classify": "classifier", "lookup": "knowledge_lookup",
            "escalate": "escalation", "compose": "composer", "review": "reviewer",
        }[node] for node in expected_path
    ]

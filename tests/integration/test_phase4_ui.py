"""Streamlit UI smoke tests with a fake public API client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import app.ui.client as client_module
from app.ui.streamlit_app import workflow_dot


class FakeApiClient:
    calls: list[tuple[str, Any, dict[str, Any]]] = []
    records: list[dict[str, Any]] = []
    last_token: str | None = None

    def __init__(self, base_url: str, *, token: str | None = None) -> None:
        self.base_url = base_url
        self.token = token
        FakeApiClient.last_token = token

    def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    def project(self) -> dict[str, Any]:
        return {
            "name": "Demo", "description": "Test project", "workflow": "flow",
            "agents": 1, "models": 1, "tools": 0,
        }

    def agents(self) -> dict[str, Any]:
        return {"analyzer": {"model": "default", "prompt": "prompts/analyzer.md"}}

    def workflow(self) -> dict[str, Any]:
        return {
            "name": "flow", "start": "analyze",
            "nodes": {"analyze": {"type": "agent", "agent": "analyzer"}},
            "transitions": [{"from": "analyze", "to": "END"}],
        }

    def runs(self) -> list[dict[str, Any]]:
        return self.records

    def run_detail(self, run_id: str) -> dict[str, Any]:
        return {"run_id": run_id, "kind": "workflow", "status": "completed", "revision": 1}

    def run_events(self, run_id: str) -> list[dict[str, Any]]:
        return [{"time": "now", "type": "node_completed", "node": "analyze", "visit": 1}]

    def reload(self) -> dict[str, Any]:
        return {"status": "reloaded", "revision": 2}

    def run_workflow(
        self, input_value: str | dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("workflow", input_value, context))
        return {
            "run_id": "run-1", "status": "completed", "result": {"answer": "Done"},
            "history": [{"node": "analyze", "visit": 1, "output": {"answer": "Done"}}],
            "results": {"analyze": {"output": {"answer": "Done"}}},
            "duration_ms": 5, "steps": 1,
        }

    def run_agent(
        self, name: str, input_value: str | dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((name, input_value, context))
        return {
            "run_id": "run-2", "status": "completed", "result": "Agent answer",
            "duration_ms": 3, "metadata": {"model": "demo"},
        }


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    FakeApiClient.calls = []
    FakeApiClient.records = []
    FakeApiClient.last_token = None
    monkeypatch.setattr(client_module, "ApiClient", FakeApiClient)
    script = Path(__file__).resolve().parents[2] / "app/ui/streamlit_app.py"
    return AppTest.from_file(script, default_timeout=10).run()


def test_ui_loads_project_and_debugs_workflow(app: AppTest) -> None:
    assert not app.exception
    assert "Demo" in [item.value for item in app.subheader]
    app.text_area[0].set_value("Hello")
    app.button[0].click().run()
    assert not app.exception
    assert FakeApiClient.calls == [("workflow", "Hello", {})]
    assert app.session_state["last_run"]["run_id"] == "run-1"
    assert "Last run" in [item.value for item in app.subheader]


def test_ui_validates_json_before_calling_api(app: AppTest) -> None:
    app.radio[1].set_value("JSON object")
    app.text_area[0].set_value("[1]")
    app.button[0].click().run()
    assert not app.exception
    assert not FakeApiClient.calls
    assert "JSON input must be an object" in str(app.session_state["last_error"])


def test_ui_runs_individual_agent_through_api(app: AppTest) -> None:
    app.radio[0].set_value("Agent").run()
    assert not app.exception
    assert app.selectbox[0].value == "analyzer"
    app.text_area[0].set_value("Check")
    app.button[0].click().run()
    assert not app.exception
    assert FakeApiClient.calls == [("analyzer", "Check", {})]
    assert app.session_state["last_run"]["result"] == "Agent answer"


def test_workflow_dot_shows_branches_and_conditional_edges() -> None:
    workflow = {
        "start": "fan",
        "nodes": {
            "fan": {"type": "parallel", "branches": {"a": {}, "b": {}}},
            "done": {"type": "agent", "agent": "writer"},
        },
        "transitions": [
            {"from": "fan", "select": "results.fan.output.a", "cases": {"ok": "done"},
             "default": "fail"},
            {"from": "done", "to": "END"},
        ],
    }
    dot = workflow_dot(workflow, {"fan"})
    assert '"START" -> "fan"' in dot
    assert '"fan" -> "done" [label="ok"]' in dot
    assert '"fan" -> "fail" [label="default"]' in dot
    assert 'fillcolor="#D9F2E6"' in dot


def test_ui_renders_server_side_trace(app: AppTest) -> None:
    FakeApiClient.records = [
        {"run_id": "trace-1", "kind": "workflow", "status": "completed", "revision": 1}
    ]
    app.run()
    assert not app.exception
    assert app.selectbox[0].label == "Inspect run"
    assert "Recent runs and event traces" in [item.value for item in app.subheader]


def test_workflow_dot_shows_manager_delegation() -> None:
    dot = workflow_dot(
        {"type": "orchestrator", "manager": "lead", "specialists": ["research", "review"]}
    )
    assert '"lead" [shape=doubleoctagon]' in dot
    assert '"lead" -> "research"' in dot
    assert '"lead" -> "review"' in dot


def test_ui_accepts_api_token_without_displaying_it(app: AppTest) -> None:
    token_box = next(item for item in app.text_input if item.label == "API token")
    token_box.set_value("secret").run()
    assert not app.exception
    assert FakeApiClient.last_token == "secret"

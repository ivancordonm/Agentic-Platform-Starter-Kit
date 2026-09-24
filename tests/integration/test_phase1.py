"""Configuration-to-API tests using a fake agent runner."""

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
from app.engine.exceptions import AgentExecutionError, ConfigurationError
from app.engine.project_runtime import ProjectRuntime

PROJECT_DIR = Path(__file__).resolve().parents[2] / "project"


@dataclass
class FakeResult:
    final_output: Any


class FakeRunner:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.calls: list[tuple[Agent[Any], str, int]] = []

    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> FakeResult:
        self.calls.append((agent, input_text, max_turns))
        return FakeResult(self.output)


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(PROJECT_DIR, root)
    return root


def test_runtime_loads_demo_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = ProjectRuntime.from_directory(PROJECT_DIR)
    assert runtime.agents.exists("analyzer")
    assert runtime.schemas.get("AnalysisResult").__name__ == "AnalysisResult"
    assert runtime.models.get("default").model == "gpt-5.6"


@pytest.mark.asyncio
async def test_agent_execution_renders_prompt_and_validates_output() -> None:
    runtime = ProjectRuntime.from_directory(PROJECT_DIR)
    fake = FakeRunner({"summary": "Concise", "findings": ["Fact"]})
    result = await AgentExecutor(runtime, fake).run("analyzer", "Example input", {"id": 42})
    assert result.output == {"summary": "Concise", "findings": ["Fact"]}
    assert result.run_id
    agent, text, turns = fake.calls[0]
    assert text == "Example input"
    assert turns == 10
    assert isinstance(agent.instructions, str)
    assert "Example input" in agent.instructions
    assert '"id": 42' in agent.instructions
    assert agent.model == "gpt-5.6"
    assert agent.output_type is runtime.schemas.get("AnalysisResult")


@pytest.mark.asyncio
async def test_invalid_structured_output_is_rejected() -> None:
    runtime = ProjectRuntime.from_directory(PROJECT_DIR)
    with pytest.raises(AgentExecutionError, match="invalid structured output"):
        await AgentExecutor(runtime, FakeRunner({"summary": "No findings"})).run(
            "analyzer", "Input", {}
        )


@pytest.mark.asyncio
async def test_api_exposes_inspection_and_agent_execution() -> None:
    fake = FakeRunner({"summary": "Done", "findings": []})
    transport = httpx.ASGITransport(app=create_app(PROJECT_DIR, fake))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        project = (await client.get("/project")).json()
        assert project["agents"] == 3
        assert "analyzer" in (await client.get("/agents")).json()
        assert (await client.get("/workflow")).json()["start"] == "analyze"
        response = await client.post(
            "/agents/analyzer/run", json={"input": "Hello", "context": {"mode": "test"}}
        )
        assert response.status_code == 200
        assert response.json()["result"] == {"summary": "Done", "findings": []}
        assert response.json()["status"] == "completed"
        assert (await client.post("/run")).status_code == 422  # Request body is required.


@pytest.mark.asyncio
async def test_unknown_agent_is_404() -> None:
    transport = httpx.ASGITransport(app=create_app(PROJECT_DIR, FakeRunner("unused")))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/agents/missing/run", json={"input": "Hello"})
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_missing_api_key_returns_503_without_real_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    transport = httpx.ASGITransport(app=create_app(PROJECT_DIR))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/agents/analyzer/run", json={"input": "Hello"})
        assert response.status_code == 503
        assert "OPENAI_API_KEY" in response.json()["detail"]


@pytest.mark.asyncio
async def test_input_schema_is_validated_as_422(project_copy: Path) -> None:
    (project_copy / "schemas" / "input.py").write_text(
        "from pydantic import BaseModel\n"
        "class DemoInput(BaseModel):\n    question: str\n"
    )
    path = project_copy / "agents.yaml"
    path.write_text(
        path.read_text().replace(
            "output_schema: AnalysisResult",
            "input_schema: DemoInput\n    output_schema: AnalysisResult",
            1,
        )
    )
    fake = FakeRunner({"summary": "Done", "findings": []})
    transport = httpx.ASGITransport(app=create_app(project_copy, fake))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bad = await client.post("/agents/analyzer/run", json={"input": {"wrong": "x"}})
        assert bad.status_code == 422
        assert not fake.calls
        good = await client.post("/agents/analyzer/run", json={"input": {"question": "Hi"}})
        assert good.status_code == 200


def test_unknown_model_is_clear_error(project_copy: Path) -> None:
    path = project_copy / "agents.yaml"
    path.write_text(path.read_text().replace("model: default", "model: missing", 1))
    with pytest.raises(ConfigurationError, match="Agent 'analyzer'.*Unknown model"):
        ProjectRuntime.from_directory(project_copy)


def test_unknown_schema_is_clear_error(project_copy: Path) -> None:
    path = project_copy / "agents.yaml"
    path.write_text(path.read_text().replace("AnalysisResult", "MissingResult"))
    with pytest.raises(ConfigurationError, match="Unknown schema"):
        ProjectRuntime.from_directory(project_copy)


def test_unknown_prompt_is_clear_error(project_copy: Path) -> None:
    (project_copy / "prompts" / "analyzer.md").unlink()
    with pytest.raises(ConfigurationError, match="Prompt not readable"):
        ProjectRuntime.from_directory(project_copy)


def test_unknown_prompt_variable_is_clear_error(project_copy: Path) -> None:
    (project_copy / "prompts" / "analyzer.md").write_text("{{ secret_key }}")
    with pytest.raises(ConfigurationError, match="unknown variables: secret_key"):
        ProjectRuntime.from_directory(project_copy)


def test_duplicate_yaml_keys_are_rejected(project_copy: Path) -> None:
    (project_copy / "models.yaml").write_text(
        "schema_version: 1\nmodels:\n  default:\n"
        "    provider: openai\n    provider: openai\n    model: demo\n"
    )
    with pytest.raises(ConfigurationError, match="Duplicate YAML key 'provider'"):
        ProjectRuntime.from_directory(project_copy)


def test_prompt_symlink_escape_is_rejected(project_copy: Path, tmp_path: Path) -> None:
    prompt = project_copy / "prompts" / "analyzer.md"
    prompt.unlink()
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    prompt.symlink_to(outside)
    with pytest.raises(ConfigurationError, match="inside prompts"):
        ProjectRuntime.from_directory(project_copy)


def test_unknown_tool_is_clear_error(project_copy: Path) -> None:
    path = project_copy / "agents.yaml"
    path.write_text(
        path.read_text().replace(
            "output_schema: AnalysisResult", "tools: [missing]\n    output_schema: AnalysisResult"
        )
    )
    with pytest.raises(ConfigurationError, match="Unknown tool"):
        ProjectRuntime.from_directory(project_copy)

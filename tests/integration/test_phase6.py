"""HTTP/MCP tool wiring and manager-owned orchestration without live model calls."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from agents import Agent
from agents.tool_context import ToolContext

from app.api.routes import create_app
from app.engine.agent_executor import AgentExecutor
from app.engine.exceptions import ConfigurationError
from app.engine.project_runtime import ProjectRuntime
from app.engine.workflow_executor import WorkflowExecutor

PROJECT_DIR = Path(__file__).resolve().parents[2] / "project"


@dataclass
class FakeResult:
    final_output: Any


class FakeRunner:
    def __init__(self) -> None:
        self.agents: list[Agent[Any]] = []

    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> FakeResult:
        self.agents.append(agent)
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


def set_tool(root: Path, tool_name: str, definition: dict[str, Any]) -> None:
    tools_path = root / "tools.yaml"
    tools = yaml.safe_load(tools_path.read_text())
    tools["tools"][tool_name] = definition
    tools_path.write_text(yaml.safe_dump(tools, sort_keys=False))
    agents_path = root / "agents.yaml"
    agents = yaml.safe_load(agents_path.read_text())
    agents["agents"]["analyzer"]["tools"] = [tool_name]
    agents_path.write_text(yaml.safe_dump(agents, sort_keys=False))


@pytest.mark.asyncio
async def test_http_tool_is_allowlisted_and_does_not_follow_redirects(
    project_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_tool(
        project_copy, "api", {
            "type": "http", "base_url": "https://example.test",
            "allowed_paths": ["/status"], "allowed_methods": ["GET"],
        },
    )
    runtime = ProjectRuntime.from_directory(project_copy)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.test/"}, text="moved")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.engine.tool_registry.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    tool = runtime.tools.resolve("api")
    context: ToolContext[Any] = ToolContext(
        context=None, tool_name=tool.name, tool_call_id="call-1", tool_arguments="{}"
    )

    async def invoke(args: dict[str, Any]) -> str:
        result = await tool.on_invoke_tool(context, json.dumps(args))
        return str(result)

    assert await invoke({"method": "GET", "path": "/other"}) == "HTTP method or path is not allowed"
    assert not requests
    assert await invoke({"method": "GET", "path": "/status"}) == "HTTP 302: moved"
    assert len(requests) == 1
    assert requests[0].url.host == "example.test"


@pytest.mark.parametrize(
    "base_url", ["http://evil.test", "https://user:pass@example.test", "https://example.test/base"]
)
def test_http_tool_rejects_unsafe_service_origin(project_copy: Path, base_url: str) -> None:
    set_tool(
        project_copy, "api", {
            "type": "http", "base_url": base_url,
            "allowed_paths": ["/status"],
        },
    )
    with pytest.raises(ConfigurationError, match="Tool service URL"):
        ProjectRuntime.from_directory(project_copy)


def test_http_tool_rejects_path_traversal(project_copy: Path) -> None:
    set_tool(
        project_copy, "api", {
            "type": "http", "base_url": "https://example.test",
            "allowed_paths": ["/%2e%2e/secret"],
        },
    )
    with pytest.raises(ConfigurationError, match="Invalid HTTP tool path"):
        ProjectRuntime.from_directory(project_copy)


@pytest.mark.asyncio
async def test_mcp_server_is_scoped_to_one_agent_run(
    project_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_tool(
        project_copy, "remote", {
            "type": "mcp", "server": "docs", "allowed_tools": ["search"],
        },
    )
    tools_path = project_copy / "tools.yaml"
    config = yaml.safe_load(tools_path.read_text())
    config["mcp_servers"]["docs"] = {"url": "https://mcp.example.test/mcp"}
    tools_path.write_text(yaml.safe_dump(config, sort_keys=False))
    runtime = ProjectRuntime.from_directory(project_copy)
    lifecycle: list[str] = []

    class FakeMcpServer:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["name"] == "docs"
            assert kwargs["tool_filter"]["allowed_tool_names"] == ["search"]
            assert kwargs["params"]["url"] == "https://mcp.example.test/mcp"

        async def __aenter__(self) -> FakeMcpServer:
            lifecycle.append("open")
            return self

        async def __aexit__(self, *args: Any) -> None:
            lifecycle.append("close")

    monkeypatch.setattr("app.engine.tool_registry.MCPServerStreamableHttp", FakeMcpServer)
    fake = FakeRunner()
    await AgentExecutor(runtime, fake).run("analyzer", "Hi", {})
    assert lifecycle == ["open", "close"]
    assert len(fake.agents[0].mcp_servers) == 1


@pytest.mark.asyncio
async def test_orchestrator_exposes_specialists_as_tools(project_copy: Path) -> None:
    path = project_copy / "workflow.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "workflow": {
                    "name": "managed", "type": "orchestrator",
                    "manager": "finalizer", "specialists": ["analyzer", "reviewer"],
                    "limits": {"max_steps": 5, "timeout_seconds": 30},
                },
            }, sort_keys=False,
        )
    )
    runtime = ProjectRuntime.from_directory(project_copy)
    fake = FakeRunner()
    events: list[dict[str, Any]] = []
    result = await WorkflowExecutor(runtime, AgentExecutor(runtime, fake)).run(
        "Hi", {}, event_sink=events.append
    )
    assert result.result == {"answer": "Done", "confidence": 1.0}
    assert result.steps == 1
    assert [tool.name for tool in fake.agents[0].tools] == [
        "delegate_analyzer", "delegate_reviewer"
    ]
    assert [event["type"] for event in events] == ["node_started", "node_completed"]

    transport = httpx.ASGITransport(app=create_app(project_copy, FakeRunner()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/workflow")).json()["type"] == "orchestrator"
        response = await client.post("/run", json={"input": "Hi"})
        assert response.status_code == 200
        assert response.json()["result"]["answer"] == "Done"


def test_orchestrator_rejects_unknown_specialist(project_copy: Path) -> None:
    path = project_copy / "workflow.yaml"
    path.write_text(
        "schema_version: 1\nworkflow:\n  name: managed\n  type: orchestrator\n"
        "  manager: finalizer\n  specialists: [missing]\n"
    )
    with pytest.raises(ConfigurationError, match="unknown agent"):
        ProjectRuntime.from_directory(project_copy)

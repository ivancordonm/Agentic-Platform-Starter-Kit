"""Keep the isolated pilot loadable without a model or MCP connection."""

from pathlib import Path

from app.engine.agent_executor import AgentExecutor
from app.engine.project_runtime import ProjectRuntime
from app.engine.workflow_executor import WorkflowExecutor
from pilot.mcp_server import PILOT_VALUE


def test_pilot_project_valid_and_answer_absent_from_prompt() -> None:
    root = Path(__file__).resolve().parents[1] / "pilot" / "project"
    runtime = ProjectRuntime.from_directory(root)
    WorkflowExecutor(runtime, AgentExecutor(runtime))

    assert list(runtime.agents.all()) == ["probe"]
    assert list(runtime.tools.all()) == ["pilot_lookup"]
    assert PILOT_VALUE not in (root / "prompts" / "probe.md").read_text()

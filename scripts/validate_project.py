"""Validate project configuration, references and workflow topology."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.engine.agent_executor import AgentExecutor
from app.engine.definitions import OrchestratorDefinition
from app.engine.exceptions import ConfigurationError
from app.engine.project_runtime import ProjectRuntime
from app.engine.workflow_executor import WorkflowExecutor


def main() -> int:
    load_dotenv()
    root = Path(os.getenv("PROJECT_DIR", "project"))
    try:
        runtime = ProjectRuntime.from_directory(root)
        WorkflowExecutor(runtime, AgentExecutor(runtime))
    except ConfigurationError as exc:
        print(f"Project configuration is invalid: {exc}", file=sys.stderr)
        return 1
    print("Project validation")
    print(f"Agents: {len(runtime.agents.all())}")
    print(f"Models: {len(runtime.models.all())}")
    print(f"Tools: {len(runtime.tools.all())}")
    workflow = runtime.configs.workflow.workflow
    if isinstance(workflow, OrchestratorDefinition):
        print(f"Orchestrator specialists: {len(workflow.specialists)}")
    else:
        print(f"Workflow nodes: {len(workflow.nodes)}")
    print("Configuration, references and workflow are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

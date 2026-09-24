"""Construct an SDK agent for one invocation, with a rendered Markdown prompt."""

from __future__ import annotations

from typing import Any

from agents import Agent
from agents.mcp import MCPServer

from app.engine.project_runtime import ProjectRuntime


class AgentFactory:
    def __init__(self, runtime: ProjectRuntime) -> None:
        self.runtime = runtime

    def build(
        self, name: str, variables: dict[str, Any], mcp_servers: list[MCPServer] | None = None
    ) -> Agent[Any]:
        definition = self.runtime.agents.get(name)
        model, settings = self.runtime.models.resolve_openai(definition.model)
        instructions = self.runtime.prompts.render(definition.prompt, variables)
        output_type = (
            self.runtime.schemas.get(definition.output_schema) if definition.output_schema else None
        )
        return Agent(
            name=name,
            instructions=instructions,
            model=model,
            model_settings=settings,
            tools=[
                self.runtime.tools.resolve(tool)
                for tool in definition.tools if not self.runtime.tools.is_mcp(tool)
            ],
            mcp_servers=mcp_servers or [],
            output_type=output_type,
        )

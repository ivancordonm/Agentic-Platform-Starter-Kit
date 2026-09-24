"""Validated, coherent project runtime built without making model calls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.engine.agent_registry import AgentRegistry
from app.engine.config import ProjectConfigs, load_project_configs
from app.engine.definitions import AgentNode, OrchestratorDefinition
from app.engine.exceptions import ConfigurationError
from app.engine.model_registry import ModelRegistry
from app.engine.prompt_loader import PromptLoader
from app.engine.schema_loader import SchemaLoader
from app.engine.tool_registry import ToolRegistry


@dataclass(frozen=True)
class ProjectRuntime:
    configs: ProjectConfigs
    agents: AgentRegistry
    models: ModelRegistry
    tools: ToolRegistry
    prompts: PromptLoader
    schemas: SchemaLoader

    @classmethod
    def from_directory(cls, root: Path) -> ProjectRuntime:
        configs = load_project_configs(root)
        runtime = cls(
            configs=configs,
            agents=AgentRegistry(configs.agents.agents),
            models=ModelRegistry(configs.models.models),
            tools=ToolRegistry(configs.root, configs.tools.tools, configs.tools.mcp_servers),
            prompts=PromptLoader(configs.root),
            schemas=SchemaLoader(configs.root),
        )
        runtime.schemas.discover()
        runtime.validate_references()
        return runtime

    def validate_references(self) -> None:
        for name, definition in self.agents.all().items():
            try:
                self.models.resolve_openai(definition.model)
                self.prompts.validate(definition.prompt)
                if definition.input_schema:
                    self.schemas.get(definition.input_schema)
                if definition.output_schema:
                    self.schemas.get(definition.output_schema)
                for tool_name in definition.tools:
                    self.tools.validate(tool_name)
            except ConfigurationError as exc:
                raise ConfigurationError(f"Agent {name!r}: {exc}") from exc
        workflow = self.configs.workflow.workflow
        if isinstance(workflow, OrchestratorDefinition):
            for agent_name in [workflow.manager, *workflow.specialists]:
                if not self.agents.exists(agent_name):
                    raise ConfigurationError(
                        f"Orchestrator references unknown agent {agent_name!r}"
                    )
            for agent_name in workflow.specialists:
                if self.agents.get(agent_name).input_schema:
                    raise ConfigurationError(
                        f"Orchestrator specialist {agent_name!r} cannot use input_schema"
                    )
            return
        for node_name, node in workflow.nodes.items():
            referenced = (
                [node.agent]
                if isinstance(node, AgentNode)
                else [branch.agent for branch in node.branches.values()]
            )
            for agent_name in referenced:
                if not self.agents.exists(agent_name):
                    raise ConfigurationError(
                        f"Node {node_name!r} references unknown agent {agent_name!r}"
                    )

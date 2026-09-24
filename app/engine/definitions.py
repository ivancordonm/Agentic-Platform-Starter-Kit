"""Versioned, provider-neutral configuration contracts.

These models intentionally perform structural validation only. Cross-file references,
graph reachability and runtime support are validated in later phases.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


def validate_relative_path(value: str) -> str:
    if value.startswith("/") or "\\" in value or "\x00" in value:
        raise ValueError("Path must be a project-relative POSIX path")
    if any(part in {".", "..", ""} for part in value.split("/")):
        raise ValueError("Path cannot contain empty or traversal segments")
    return value


type Identifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", min_length=1)
]
type RelativePath = Annotated[
    str, StringConstraints(min_length=1), AfterValidator(validate_relative_path)
]
type StateSelector = Annotated[
    str,
    StringConstraints(pattern=r"^(?:input|context|results|metadata)(?:\.[A-Za-z][A-Za-z0-9_-]*)*$"),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Versioned(StrictModel):
    schema_version: Literal[1]


class ExecutionLimits(StrictModel):
    max_steps: int = Field(default=20, ge=1, le=1000)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_concurrency: int = Field(default=4, ge=1, le=64)


class ProjectInfo(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)


class ProjectConfig(Versioned):
    project: ProjectInfo


class ReasoningConfig(StrictModel):
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class ModelDefinition(StrictModel):
    provider: Identifier
    model: str = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning: ReasoningConfig | None = None


class ModelsConfig(Versioned):
    models: dict[Identifier, ModelDefinition] = Field(min_length=1)


class AgentDefinition(StrictModel):
    description: str = Field(min_length=1)
    model: Identifier
    prompt: RelativePath
    tools: list[Identifier] = Field(default_factory=list)
    input_schema: Identifier | None = None
    output_schema: Identifier | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    max_turns: int = Field(default=10, ge=1, le=100)


class AgentsConfig(Versioned):
    agents: dict[Identifier, AgentDefinition] = Field(min_length=1)


class BuiltinToolDefinition(StrictModel):
    type: Literal["builtin"]
    implementation: Literal["filesystem"]
    root: RelativePath | None = None
    read_only: Literal[True] = True


def default_http_methods() -> list[Literal["GET", "POST", "PUT", "PATCH", "DELETE"]]:
    return ["GET"]


class HttpToolDefinition(StrictModel):
    type: Literal["http"]
    base_url: str = Field(min_length=1)
    allowed_paths: list[str] = Field(min_length=1)
    allowed_methods: list[Literal["GET", "POST", "PUT", "PATCH", "DELETE"]] = Field(
        default_factory=default_http_methods
    )
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    auth_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")


class McpToolDefinition(StrictModel):
    type: Literal["mcp"]
    server: Identifier
    allowed_tools: list[Identifier] = Field(min_length=1)


type ToolDefinition = Annotated[
    BuiltinToolDefinition | HttpToolDefinition | McpToolDefinition,
    Field(discriminator="type"),
]


class McpServerDefinition(StrictModel):
    url: str = Field(min_length=1)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    auth_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")


class ToolsConfig(Versioned):
    tools: dict[Identifier, ToolDefinition] = Field(default_factory=dict)
    mcp_servers: dict[Identifier, McpServerDefinition] = Field(default_factory=dict)


class AgentNode(StrictModel):
    type: Literal["agent"]
    agent: Identifier
    max_visits: int = Field(default=1, ge=1, le=100)


class ParallelBranch(StrictModel):
    agent: Identifier


class ParallelNode(StrictModel):
    type: Literal["parallel"]
    branches: dict[Identifier, ParallelBranch] = Field(min_length=2)
    failure_policy: Literal["fail_fast", "collect_errors"] = "fail_fast"
    max_visits: int = Field(default=1, ge=1, le=100)


class ReservedNode(StrictModel):
    """Documented extension points; compilation will reject them until implemented."""

    type: Literal["router", "human_approval"]


type NodeDefinition = Annotated[
    AgentNode | ParallelNode | ReservedNode, Field(discriminator="type")
]


class DirectTransition(StrictModel):
    from_node: Identifier = Field(alias="from")
    to: Identifier | Literal["END"]


class ConditionalTransition(StrictModel):
    from_node: Identifier = Field(alias="from")
    select: StateSelector
    cases: dict[str, str] = Field(min_length=1)
    default: str = "fail"

    @model_validator(mode="after")
    def validate_targets(self) -> ConditionalTransition:
        targets = [*self.cases.values(), self.default]
        for target in targets:
            if target not in {"END", "fail"} and not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_-]*", target
            ):
                raise ValueError(f"Invalid transition target: {target!r}")
        return self


type Transition = DirectTransition | ConditionalTransition


class WorkflowOutput(StrictModel):
    select: StateSelector


class WorkflowDefinition(StrictModel):
    name: Identifier
    type: Literal["graph"] = "graph"
    start: Identifier
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    nodes: dict[Identifier, NodeDefinition] = Field(min_length=1)
    transitions: list[Transition] = Field(min_length=1)
    output: WorkflowOutput

    @model_validator(mode="after")
    def validate_local_shape(self) -> WorkflowDefinition:
        if self.start not in self.nodes:
            raise ValueError(f"Start node {self.start!r} is not defined")
        for transition in self.transitions:
            if transition.from_node not in self.nodes:
                raise ValueError(f"Unknown transition source: {transition.from_node!r}")
            destinations = (
                [transition.to]
                if isinstance(transition, DirectTransition)
                else [*transition.cases.values(), transition.default]
            )
            for destination in destinations:
                if destination not in {"END", "fail"} and destination not in self.nodes:
                    raise ValueError(f"Unknown transition destination: {destination!r}")
        return self


class OrchestratorDefinition(StrictModel):
    name: Identifier
    type: Literal["orchestrator"]
    manager: Identifier
    specialists: list[Identifier] = Field(min_length=1)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @model_validator(mode="after")
    def validate_specialists(self) -> OrchestratorDefinition:
        if self.manager in self.specialists:
            raise ValueError("Manager cannot also be a specialist")
        if len(set(self.specialists)) != len(self.specialists):
            raise ValueError("Specialists must be unique")
        tool_names = [name.replace("-", "_") for name in self.specialists]
        if len(set(tool_names)) != len(tool_names):
            raise ValueError("Specialist tool names must be unique after normalization")
        return self


class WorkflowConfig(Versioned):
    workflow: Annotated[WorkflowDefinition | OrchestratorDefinition, Field(discriminator="type")]

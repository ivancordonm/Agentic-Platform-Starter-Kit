"""Stable HTTP request and response contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str | dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)


class AgentRunResponse(BaseModel):
    run_id: str
    status: Literal["completed"] = "completed"
    agent: str
    result: str | dict[str, Any]
    duration_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResponse(BaseModel):
    run_id: str
    status: Literal["completed"] = "completed"
    result: Any
    results: dict[str, Any]
    history: list[dict[str, Any]]
    steps: int
    duration_ms: int


class ErrorResponse(BaseModel):
    error: str
    message: str

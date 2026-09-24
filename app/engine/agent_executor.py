"""Async, mockable agent execution and output normalization."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from agents import Agent, FunctionTool, RunConfig, Runner
from pydantic import ValidationError

from app.engine.agent_factory import AgentFactory
from app.engine.exceptions import AgentExecutionError, AgentInputError
from app.engine.project_runtime import ProjectRuntime


class RunResultLike(Protocol):
    final_output: Any


class AgentRunner(Protocol):
    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> RunResultLike: ...


class OpenAIAgentRunner:
    async def run(self, agent: Agent[Any], input_text: str, max_turns: int) -> RunResultLike:
        return await Runner.run(
            agent,
            input_text,
            max_turns=max_turns,
            run_config=RunConfig(tracing_disabled=True),
        )


@dataclass(frozen=True)
class AgentRun:
    run_id: str
    agent: str
    output: str | dict[str, Any]
    duration_ms: int
    model: str


class AgentExecutor:
    def __init__(self, runtime: ProjectRuntime, runner: AgentRunner | None = None) -> None:
        self.runtime = runtime
        self.runner = runner or OpenAIAgentRunner()
        self.factory = AgentFactory(runtime)

    async def run(
        self,
        name: str,
        input_value: str | dict[str, Any],
        context: dict[str, Any],
        results: dict[str, Any] | None = None,
        run_id: str | None = None,
        extra_tools: list[FunctionTool] | None = None,
        max_turns_override: int | None = None,
    ) -> AgentRun:
        definition = self.runtime.agents.get(name)
        if definition.input_schema:
            schema = self.runtime.schemas.get(definition.input_schema)
            try:
                validated_input = schema.model_validate(input_value)
            except ValidationError as exc:
                raise AgentInputError(f"Input does not match {definition.input_schema}") from exc
            input_value = validated_input.model_dump(mode="json")
        input_text = input_value if isinstance(input_value, str) else json.dumps(input_value)
        variables = {
            "project_name": self.runtime.configs.project.project.name,
            "input": input_text,
            "context": json.dumps(context),
            "results": json.dumps(results or {}),
            "messages": "[]",
        }
        start = time.perf_counter()
        try:
            async with AsyncExitStack() as stack:
                mcp_servers = await self.runtime.tools.open_mcp(definition.tools, stack)
                agent = self.factory.build(name, variables, mcp_servers)
                if extra_tools:
                    agent.tools.extend(extra_tools)
                result = await asyncio.wait_for(
                    self.runner.run(
                        agent, input_text,
                        min(definition.max_turns, max_turns_override or definition.max_turns),
                    ),
                    timeout=definition.timeout_seconds,
                )
            output: str | dict[str, Any]
            if definition.output_schema:
                schema = self.runtime.schemas.get(definition.output_schema)
                raw = result.final_output
                validated = raw if isinstance(raw, schema) else schema.model_validate(raw)
                output = validated.model_dump(mode="json")
            else:
                raw = result.final_output
                if not isinstance(raw, str):
                    raise AgentExecutionError("Agent returned a non-text output")
                output = raw
        except TimeoutError as exc:
            raise AgentExecutionError(f"Agent {name!r} timed out") from exc
        except ValidationError as exc:
            raise AgentExecutionError(
                f"Agent {name!r} returned an invalid structured output"
            ) from exc
        except AgentExecutionError:
            raise
        except Exception as exc:
            raise AgentExecutionError(f"Agent {name!r} execution failed") from exc
        return AgentRun(
            run_id=run_id or uuid4().hex,
            agent=name,
            output=output,
            duration_ms=round((time.perf_counter() - start) * 1000),
            model=agent.model if isinstance(agent.model, str) else "custom",
        )

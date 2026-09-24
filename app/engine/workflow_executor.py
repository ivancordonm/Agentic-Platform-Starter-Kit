"""LangGraph execution for validated agent, parallel and bounded-loop workflows."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypedDict, cast
from uuid import uuid4

from agents import RunConfig
from langgraph.graph import END, START, StateGraph

from app.engine.agent_executor import AgentExecutor, AgentRun
from app.engine.definitions import (
    AgentNode,
    ConditionalTransition,
    DirectTransition,
    OrchestratorDefinition,
    ParallelNode,
    Transition,
    WorkflowDefinition,
)
from app.engine.exceptions import ConfigurationError, WorkflowExecutionError
from app.engine.project_runtime import ProjectRuntime

EventSink = Callable[[dict[str, Any]], None]
_event_sink: ContextVar[EventSink | None] = ContextVar("workflow_event_sink", default=None)


def _emit(event: dict[str, Any]) -> None:
    sink = _event_sink.get()
    if sink is not None:
        sink(event)


class WorkflowState(TypedDict):
    input: str | dict[str, Any]
    context: dict[str, Any]
    results: dict[str, Any]
    metadata: dict[str, Any]
    history: list[dict[str, Any]]
    visits: dict[str, int]
    steps: int


@dataclass(frozen=True)
class WorkflowRun:
    run_id: str
    status: str
    result: Any
    results: dict[str, Any]
    history: list[dict[str, Any]]
    steps: int
    duration_ms: int


def select_state(state: WorkflowState, selector: str) -> Any:
    current: Any = state
    for part in selector.split("."):
        if not isinstance(current, dict) or part not in current:
            raise WorkflowExecutionError(f"Selector {selector!r} did not resolve")
        current = current[part]
    return current


def _case_key(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return "null"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise WorkflowExecutionError("Conditional selector must resolve to a scalar value")


def _destinations(transition: Transition) -> list[str]:
    if isinstance(transition, DirectTransition):
        return [transition.to]
    return [*transition.cases.values(), transition.default]


class GraphWorkflowExecutor:
    def __init__(
        self, runtime: ProjectRuntime, agents: AgentExecutor, definition: WorkflowDefinition
    ) -> None:
        self.runtime = runtime
        self.agents = agents
        self.definition = definition
        self._validate_graph()
        graph = StateGraph(WorkflowState)
        for name, node in self.definition.nodes.items():
            graph.add_node(name, self._node(name, node))
        graph.add_edge(START, self.definition.start)
        for transition in self.definition.transitions:
            if isinstance(transition, DirectTransition):
                graph.add_edge(
                    transition.from_node, END if transition.to == "END" else transition.to
                )
            else:
                targets = {target for target in _destinations(transition) if target != "fail"}
                graph.add_conditional_edges(
                    transition.from_node,
                    self._route(transition),
                    {target: END if target == "END" else target for target in targets},
                )
        self.graph = graph.compile()

    def _validate_graph(self) -> None:
        nodes = self.definition.nodes
        selectors = [self.definition.output.select]
        selectors.extend(
            transition.select for transition in self.definition.transitions
            if isinstance(transition, ConditionalTransition)
        )
        for selector in selectors:
            parts = selector.split(".")
            if len(parts) > 1 and parts[0] == "results" and parts[1] not in nodes:
                raise ConfigurationError(
                    f"Selector {selector!r} references unknown node {parts[1]!r}"
                )
        transitions: dict[str, Transition] = {}
        for transition in self.definition.transitions:
            source = transition.from_node
            if source in transitions:
                raise ConfigurationError(f"Node {source!r} has ambiguous transitions")
            transitions[source] = transition
        if set(transitions) != set(nodes):
            missing = sorted(set(nodes) - set(transitions))
            raise ConfigurationError(f"Nodes missing transitions: {', '.join(missing)}")

        adjacency = {
            name: [target for target in _destinations(transitions[name]) if target != "fail"]
            for name in nodes
        }
        reachable: set[str] = set()

        def visit(name: str) -> None:
            if name in reachable or name == "END":
                return
            reachable.add(name)
            for target in adjacency[name]:
                visit(target)

        visit(self.definition.start)
        if set(nodes) != reachable:
            unreachable = ", ".join(sorted(set(nodes) - reachable))
            raise ConfigurationError(f"Unreachable workflow nodes: {unreachable}")

        # Every node must have a potential successful termination path. Runtime
        # conditions may still choose a fail route or exhaust a loop bound.
        can_end = {"END"}
        changed = True
        while changed:
            previous = len(can_end)
            can_end.update(
                name for name, targets in adjacency.items() if any(t in can_end for t in targets)
            )
            changed = len(can_end) != previous
        if set(nodes) - can_end:
            stuck = ", ".join(sorted(set(nodes) - can_end))
            raise ConfigurationError(f"Nodes without a path to END: {stuck}")

        # Tarjan SCCs find all cyclic regions, including overlapping cycles.
        index = 0
        indices: dict[str, int] = {}
        low: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()

        def strongconnect(name: str) -> None:
            nonlocal index
            indices[name] = low[name] = index
            index += 1
            stack.append(name)
            on_stack.add(name)
            for target in adjacency[name]:
                if target == "END":
                    continue
                if target not in indices:
                    strongconnect(target)
                    low[name] = min(low[name], low[target])
                elif target in on_stack:
                    low[name] = min(low[name], indices[target])
            if low[name] != indices[name]:
                return
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == name:
                    break
            cyclic = len(component) > 1 or name in adjacency[name]
            if cyclic:
                unbounded = []
                for member in component:
                    member_node = nodes[member]
                    if member_node.max_visits == 1:
                        unbounded.append(member)
                if unbounded:
                    raise ConfigurationError(
                        "Workflow cycle requires max_visits > 1 on every node: "
                        + ", ".join(sorted(unbounded))
                    )

        for node_name in nodes:
            if node_name not in indices:
                strongconnect(node_name)

    def _check_budget(self, state: WorkflowState, name: str, max_visits: int) -> tuple[int, int]:
        steps = state["steps"] + 1
        if steps > self.definition.limits.max_steps:
            raise WorkflowExecutionError("Workflow exceeded max_steps")
        visit_number = state["visits"].get(name, 0) + 1
        if visit_number > max_visits:
            raise WorkflowExecutionError(f"Node {name!r} exceeded max_visits ({max_visits})")
        return steps, visit_number

    def _update(
        self, state: WorkflowState, name: str, steps: int, visit_number: int,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "steps": steps,
            "visits": {**state["visits"], name: visit_number},
            "results": {**state["results"], name: result},
            "history": [*state["history"], {"node": name, "visit": visit_number, **result}],
        }

    def _node(self, name: str, node: AgentNode | ParallelNode) -> Any:
        if isinstance(node, AgentNode):
            async def invoke_agent(state: WorkflowState) -> dict[str, Any]:
                steps, visit_number = self._check_budget(state, name, node.max_visits)
                _emit({"type": "node_started", "node": name, "visit": visit_number})
                try:
                    run = await self.agents.run(
                        node.agent, state["input"], state["context"], state["results"]
                    )
                except Exception as exc:
                    _emit({"type": "node_failed", "node": name, "visit": visit_number,
                           "error": str(exc)})
                    raise
                result = {
                    "agent": node.agent, "output": run.output, "run_id": run.run_id,
                    "duration_ms": run.duration_ms, "model": run.model,
                }
                _emit({"type": "node_completed", "node": name, "visit": visit_number,
                       "output": run.output, "duration_ms": run.duration_ms})
                return self._update(state, name, steps, visit_number, result)

            return invoke_agent

        async def invoke_parallel(state: WorkflowState) -> dict[str, Any]:
            steps, visit_number = self._check_budget(state, name, node.max_visits)
            _emit({"type": "node_started", "node": name, "visit": visit_number})
            semaphore = asyncio.Semaphore(self.definition.limits.max_concurrency)

            async def branch(branch_name: str, agent_name: str) -> AgentRun:
                async with semaphore:
                    _emit({"type": "branch_started", "node": name, "visit": visit_number,
                           "branch": branch_name})
                    try:
                        run = await self.agents.run(
                            agent_name, state["input"], state["context"], state["results"]
                        )
                    except Exception as exc:
                        _emit({"type": "branch_failed", "node": name, "visit": visit_number,
                               "branch": branch_name, "error": str(exc)})
                        raise
                    _emit({"type": "branch_completed", "node": name, "visit": visit_number,
                           "branch": branch_name, "output": run.output,
                           "duration_ms": run.duration_ms})
                    return run

            names = list(node.branches)
            tasks = [
                asyncio.create_task(branch(key, node.branches[key].agent)) for key in names
            ]
            try:
                if node.failure_policy == "fail_fast":
                    pending = set(tasks)
                    while pending:
                        done, pending = await asyncio.wait(
                            pending, return_when=asyncio.FIRST_EXCEPTION
                        )
                        for task in done:
                            error = task.exception()
                            if error is not None:
                                raise error
                    outcomes: list[AgentRun | BaseException] = [task.result() for task in tasks]
                else:
                    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            outputs: dict[str, Any] = {}
            branches: dict[str, Any] = {}
            for key, outcome in zip(names, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    outputs[key] = None
                    branches[key] = {"status": "failed", "error": str(outcome)}
                else:
                    outputs[key] = outcome.output
                    branches[key] = {
                        "status": "completed", "agent": outcome.agent,
                        "run_id": outcome.run_id, "duration_ms": outcome.duration_ms,
                        "model": outcome.model,
                    }
            _emit({"type": "node_completed", "node": name, "visit": visit_number,
                   "output": outputs})
            return self._update(
                state, name, steps, visit_number,
                {"output": outputs, "branches": branches},
            )

        return invoke_parallel

    def _route(self, transition: ConditionalTransition) -> Any:
        def route(state: WorkflowState) -> str:
            value = _case_key(select_state(state, transition.select))
            target = transition.cases.get(value, transition.default)
            if target == "fail":
                _emit({"type": "route_failed", "node": transition.from_node,
                       "selector": transition.select, "value": value})
                raise WorkflowExecutionError(
                    f"No successful route from {transition.from_node!r} for {value!r}"
                )
            _emit({"type": "route_selected", "node": transition.from_node,
                   "selector": transition.select, "value": value, "target": target})
            return target

        return route

    async def run(
        self, input_value: str | dict[str, Any], context: dict[str, Any],
        run_id: str | None = None, event_sink: EventSink | None = None,
    ) -> WorkflowRun:
        start = time.perf_counter()
        token = _event_sink.set(event_sink)
        initial: WorkflowState = {
            "input": input_value,
            "context": context,
            "results": {},
            "metadata": {},
            "history": [],
            "visits": {},
            "steps": 0,
        }
        try:
            try:
                state = await asyncio.wait_for(
                    self.graph.ainvoke(
                        initial,
                        config={"recursion_limit": max(25, self.definition.limits.max_steps + 2)},
                    ),
                    timeout=self.definition.limits.timeout_seconds,
                )
            except TimeoutError as exc:
                raise WorkflowExecutionError("Workflow timed out") from exc
        finally:
            _event_sink.reset(token)
        result = select_state(cast(WorkflowState, state), self.definition.output.select)
        return WorkflowRun(
            run_id=run_id or uuid4().hex,
            status="completed",
            result=result,
            results=state["results"],
            history=state["history"],
            steps=state["steps"],
            duration_ms=round((time.perf_counter() - start) * 1000),
        )


class OrchestratorWorkflowExecutor:
    def __init__(
        self, runtime: ProjectRuntime, agents: AgentExecutor, definition: OrchestratorDefinition
    ) -> None:
        self.runtime = runtime
        self.agents = agents
        self.definition = definition

    async def run(
        self, input_value: str | dict[str, Any], context: dict[str, Any],
        run_id: str | None = None, event_sink: EventSink | None = None,
    ) -> WorkflowRun:
        start = time.perf_counter()
        input_text = input_value if isinstance(input_value, str) else json.dumps(input_value)
        variables = {
            "project_name": self.runtime.configs.project.project.name,
            "input": input_text, "context": json.dumps(context),
            "results": "{}", "messages": "[]",
        }
        token = _event_sink.set(event_sink)
        try:
            async with AsyncExitStack() as stack:
                specialist_tools = []
                for name in self.definition.specialists:
                    specialist = self.runtime.agents.get(name)
                    servers = await self.runtime.tools.open_mcp(specialist.tools, stack)
                    agent = self.agents.factory.build(name, variables, servers)
                    specialist_tools.append(
                        agent.as_tool(
                            tool_name=f"delegate_{name.replace('-', '_')}",
                            tool_description=specialist.description,
                            run_config=RunConfig(tracing_disabled=True),
                            max_turns=specialist.max_turns,
                        )
                    )
                _emit({"type": "node_started", "node": "manager", "visit": 1})
                try:
                    manager_run = await asyncio.wait_for(
                        self.agents.run(
                            self.definition.manager, input_value, context,
                            run_id=run_id, extra_tools=specialist_tools,
                            max_turns_override=self.definition.limits.max_steps,
                        ),
                        timeout=self.definition.limits.timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise WorkflowExecutionError("Orchestrator timed out") from exc
                except Exception as exc:
                    _emit({"type": "node_failed", "node": "manager", "visit": 1,
                           "error": str(exc)})
                    raise
                _emit({"type": "node_completed", "node": "manager", "visit": 1,
                       "output": manager_run.output,
                       "duration_ms": manager_run.duration_ms})
        finally:
            _event_sink.reset(token)
        result = {
            "agent": self.definition.manager,
            "output": manager_run.output,
            "run_id": manager_run.run_id,
            "duration_ms": manager_run.duration_ms,
            "model": manager_run.model,
        }
        return WorkflowRun(
            run_id=run_id or uuid4().hex,
            status="completed",
            result=manager_run.output,
            results={"manager": result},
            history=[{"node": "manager", "visit": 1, **result}],
            steps=1,
            duration_ms=round((time.perf_counter() - start) * 1000),
        )


class WorkflowExecutor:
    def __init__(self, runtime: ProjectRuntime, agents: AgentExecutor) -> None:
        self.definition = runtime.configs.workflow.workflow
        self._delegate: GraphWorkflowExecutor | OrchestratorWorkflowExecutor
        if isinstance(self.definition, OrchestratorDefinition):
            self._delegate = OrchestratorWorkflowExecutor(runtime, agents, self.definition)
        else:
            self._delegate = GraphWorkflowExecutor(runtime, agents, self.definition)

    async def run(
        self, input_value: str | dict[str, Any], context: dict[str, Any],
        run_id: str | None = None, event_sink: EventSink | None = None,
    ) -> WorkflowRun:
        return await self._delegate.run(input_value, context, run_id, event_sink)

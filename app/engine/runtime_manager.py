"""Atomic, versioned runtime snapshots for project reloads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from app.engine.agent_executor import AgentExecutor, AgentRunner
from app.engine.project_runtime import ProjectRuntime
from app.engine.workflow_executor import WorkflowExecutor


@dataclass(frozen=True)
class RuntimeSnapshot:
    revision: int
    runtime: ProjectRuntime
    agents: AgentExecutor
    workflow: WorkflowExecutor


class RuntimeManager:
    def __init__(self, root: Path, runner: AgentRunner | None = None) -> None:
        self.root = root
        self.runner = runner
        self._lock = Lock()
        self._reload_lock = Lock()
        self._snapshot = self._build(1)

    def _build(self, revision: int) -> RuntimeSnapshot:
        runtime = ProjectRuntime.from_directory(self.root)
        agents = AgentExecutor(runtime, self.runner)
        workflow = WorkflowExecutor(runtime, agents)
        return RuntimeSnapshot(revision, runtime, agents, workflow)

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot

    def reload(self) -> RuntimeSnapshot:
        # Build without blocking readers. A separate lock serializes reloads,
        # and the short publication lock swaps the coherent candidate atomically.
        with self._reload_lock:
            revision = self.snapshot().revision + 1
            candidate = self._build(revision)
            with self._lock:
                self._snapshot = candidate
            return candidate

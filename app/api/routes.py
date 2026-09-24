"""API boundary for versioned runtimes, execution and in-memory traces."""

from __future__ import annotations

import os
import time
from hmac import compare_digest
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.api.models import AgentRunRequest, AgentRunResponse, WorkflowRunResponse
from app.engine.agent_executor import AgentRunner
from app.engine.exceptions import (
    AgentExecutionError,
    AgentInputError,
    AgentNotFoundError,
    ConfigurationError,
    WorkflowExecutionError,
)
from app.engine.run_store import InMemoryRunStore, RunRepository
from app.engine.runtime_manager import RuntimeManager
from app.engine.sqlite_run_store import SQLiteRunStore


def create_app(
    project_dir: Path | None = None,
    runner: AgentRunner | None = None,
    run_store: RunRepository | None = None,
) -> FastAPI:
    root = project_dir or Path(os.getenv("PROJECT_DIR", "project"))
    manager = RuntimeManager(root, runner)
    db_path = os.getenv("RUN_DB_PATH")
    store = run_store if run_store is not None else (
        SQLiteRunStore(Path(db_path)) if db_path else InMemoryRunStore()
    )
    application = FastAPI(title="Agentic Platform API", version="0.1.0")
    api_token = os.getenv("PLATFORM_API_TOKEN")

    @application.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        if api_token and request.url.path != "/health":
            authorization = request.headers.get("Authorization", "")
            provided = authorization[7:] if authorization.startswith("Bearer ") else ""
            if not compare_digest(provided, api_token):
                return JSONResponse(
                    status_code=401, content={"detail": "Unauthorized"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    def failed(run_id: str, started: float, exc: Exception, status_code: int) -> HTTPException:
        message = str(exc) if status_code != 500 else "Internal run error"
        store.event(run_id, {"type": "run_failed", "error": message})
        store.finish(
            run_id, "failed", round((time.perf_counter() - started) * 1000),
            None, error=message,
        )
        return HTTPException(
            status_code=status_code, detail=message, headers={"X-Run-ID": run_id}
        )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/project")
    async def project() -> dict[str, Any]:
        snapshot = manager.snapshot()
        info = snapshot.runtime.configs.project.project
        return {
            "name": info.name,
            "description": info.description,
            "workflow": snapshot.runtime.configs.workflow.workflow.name,
            "agents": len(snapshot.runtime.agents.all()),
            "models": len(snapshot.runtime.models.all()),
            "tools": len(snapshot.runtime.tools.all()),
            "revision": snapshot.revision,
        }

    @application.get("/agents")
    async def agents() -> dict[str, Any]:
        snapshot = manager.snapshot()
        return {
            name: {**definition.model_dump(mode="json"), "model_id": definition.model}
            for name, definition in snapshot.runtime.agents.all().items()
        }

    @application.get("/workflow")
    async def workflow() -> dict[str, Any]:
        snapshot = manager.snapshot()
        return snapshot.runtime.configs.workflow.workflow.model_dump(mode="json", by_alias=True)

    @application.post("/reload")
    async def reload_project() -> dict[str, Any]:
        try:
            snapshot = manager.reload()
        except ConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"status": "reloaded", "revision": snapshot.revision}

    @application.get("/runs")
    async def list_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
        return store.list(limit)

    @application.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        record = store.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return record

    @application.get("/runs/{run_id}/events")
    async def get_run_events(run_id: str) -> list[dict[str, Any]]:
        record = store.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return record["events"]  # type: ignore[no-any-return]

    @application.post("/agents/{agent_name}/run", response_model=AgentRunResponse)
    async def run_agent(agent_name: str, request: AgentRunRequest) -> AgentRunResponse:
        snapshot = manager.snapshot()
        if not snapshot.runtime.agents.exists(agent_name):
            raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_name}")
        if runner is None and not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
        run_id = uuid4().hex
        started = time.perf_counter()
        store.create(run_id, "agent", snapshot.revision, agent_name)
        store.event(run_id, {"type": "run_started", "agent": agent_name})
        try:
            result = await snapshot.agents.run(
                agent_name, request.input, request.context, run_id=run_id
            )
        except AgentNotFoundError as exc:
            raise failed(run_id, started, exc, 404) from exc
        except (ConfigurationError, AgentInputError) as exc:
            raise failed(run_id, started, exc, 422) from exc
        except AgentExecutionError as exc:
            raise failed(run_id, started, exc, 502) from exc
        except Exception as exc:
            raise failed(run_id, started, exc, 500) from exc
        response = AgentRunResponse(
            run_id=run_id, agent=result.agent, result=result.output,
            duration_ms=result.duration_ms, metadata={"model": result.model},
        )
        store.event(run_id, {"type": "agent_completed", "agent": agent_name,
                             "output": result.output, "duration_ms": result.duration_ms})
        store.event(run_id, {"type": "run_completed"})
        store.finish(run_id, "completed", result.duration_ms, response.model_dump(mode="json"))
        return response

    @application.post("/run", response_model=WorkflowRunResponse)
    async def run_workflow(request: AgentRunRequest) -> WorkflowRunResponse:
        snapshot = manager.snapshot()
        if runner is None and not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
        run_id = uuid4().hex
        started = time.perf_counter()
        store.create(run_id, "workflow", snapshot.revision, snapshot.workflow.definition.name)
        store.event(run_id, {"type": "run_started", "workflow": snapshot.workflow.definition.name})
        try:
            result = await snapshot.workflow.run(
                request.input, request.context, run_id=run_id,
                event_sink=lambda event: store.event(run_id, event),
            )
        except (AgentInputError, WorkflowExecutionError, ConfigurationError) as exc:
            raise failed(run_id, started, exc, 422) from exc
        except AgentExecutionError as exc:
            raise failed(run_id, started, exc, 502) from exc
        except Exception as exc:
            raise failed(run_id, started, exc, 500) from exc
        response = WorkflowRunResponse(
            run_id=run_id, result=result.result, results=result.results,
            history=result.history, steps=result.steps, duration_ms=result.duration_ms,
        )
        store.event(run_id, {"type": "run_completed"})
        store.finish(run_id, "completed", result.duration_ms, response.model_dump(mode="json"))
        return response

    return application

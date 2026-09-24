"""Offline MCP preflight and explicit, billable end-to-end pilot assertion."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from dotenv import load_dotenv

from app.engine.agent_executor import AgentExecutor
from app.engine.project_runtime import ProjectRuntime
from app.engine.workflow_executor import WorkflowExecutor
from pilot.mcp_server import PILOT_FACT_ID, PILOT_VALUE

PILOT_PROJECT = Path(__file__).resolve().parents[1] / "pilot" / "project"


async def preflight() -> None:
    runtime = ProjectRuntime.from_directory(PILOT_PROJECT)
    WorkflowExecutor(runtime, AgentExecutor(runtime))
    async with AsyncExitStack() as stack:
        servers = await runtime.tools.open_mcp(["pilot_lookup"], stack)
        server = servers[0]
        names = {tool.name for tool in await server.list_tools()}
        if "get_pilot_fact" not in names:
            raise RuntimeError(f"MCP tool not advertised; found: {sorted(names)}")
        result = await server.call_tool("get_pilot_fact", {"fact_id": PILOT_FACT_ID})
        if result.isError or PILOT_VALUE not in str(result):
            raise RuntimeError("MCP tool did not return the expected pilot value")
    print("OK: pilot project valid; MCP connected; tool returned the expected value.")


async def live(api_url: str) -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY in .env before the billable test")
    headers = {"Authorization": f"Bearer {token}"} if (
        token := os.getenv("PLATFORM_API_TOKEN")
    ) else {}
    async with httpx.AsyncClient(base_url=api_url, headers=headers, timeout=120) as client:
        project = await client.get("/project")
        project.raise_for_status()
        if project.json()["name"] != "MCP Pilot":
            raise RuntimeError("API is not running the pilot project; set PROJECT_DIR")
        response = await client.post(
            "/run", json={"input": PILOT_FACT_ID, "context": {}}
        )
        response.raise_for_status()
        run = response.json()
        if run["result"].strip() != PILOT_VALUE or run["steps"] != 1:
            raise RuntimeError(f"Unexpected workflow result: {run}")
        trace = await client.get(f"/runs/{run['run_id']}")
        trace.raise_for_status()
        record = trace.json()
        event_types = {event["type"] for event in record["events"]}
        if record["status"] != "completed" or not {
            "run_started", "node_completed", "run_completed"
        }.issubset(event_types):
            raise RuntimeError(f"Incomplete run trace: {record}")
    print(f"OK: live workflow used MCP and completed; run_id={run['run_id']}")
    print("This step made a billable OpenAI model request.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the local MCP acceptance pilot")
    parser.add_argument(
        "--live", action="store_true", help="Call the API and OpenAI (billable)"
    )
    parser.add_argument(
        "--api-url", default="http://127.0.0.1:8000", help="Pilot API base URL"
    )
    args = parser.parse_args()
    load_dotenv()
    try:
        if args.live:
            asyncio.run(live(args.api_url))
        else:
            asyncio.run(preflight())
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"Pilot failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

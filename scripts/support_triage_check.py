"""Free preflight for the example's project and real local MCP connection."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from app.engine.agent_executor import AgentExecutor
from app.engine.project_runtime import ProjectRuntime
from app.engine.workflow_executor import WorkflowExecutor

PROJECT_DIR = Path(__file__).resolve().parents[1] / "examples" / "support_triage" / "project"


async def check() -> None:
    runtime = ProjectRuntime.from_directory(PROJECT_DIR)
    WorkflowExecutor(runtime, AgentExecutor(runtime))
    async with AsyncExitStack() as stack:
        servers = await runtime.tools.open_mcp(["support_kb"], stack)
        server = servers[0]
        tool_names = {tool.name for tool in await server.list_tools()}
        if "search_support_articles" not in tool_names:
            raise RuntimeError(f"Knowledge-base tool unavailable: {sorted(tool_names)}")
        known = await server.call_tool(
            "search_support_articles", {"query": "No encuentro mi factura"}
        )
        unknown = await server.call_tool(
            "search_support_articles", {"query": "exportar auditoría XML"}
        )
        known_payload = json.loads(known.content[0].text)
        unknown_payload = json.loads(unknown.content[0].text)
        if known.isError or known_payload["articles"][0]["id"] != "KB-202":
            raise RuntimeError("Expected billing article was not returned")
        if unknown.isError or unknown_payload["articles"] != []:
            raise RuntimeError("Unknown issue did not return an empty article list")
    print("OK: project valid; local MCP connected; known and unknown searches passed.")


if __name__ == "__main__":
    try:
        asyncio.run(check())
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Support-triage preflight failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

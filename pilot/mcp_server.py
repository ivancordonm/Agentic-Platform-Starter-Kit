"""Small, deterministic Streamable HTTP MCP server for the acceptance pilot."""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

PILOT_FACT_ID = "case-7319"
PILOT_VALUE = "PILOT-ORBIT-7319"


def create_server(host: str = "127.0.0.1", port: int = 8765) -> FastMCP:
    server = FastMCP("agentic-pilot", host=host, port=port, stateless_http=True)

    @server.tool()
    def get_pilot_fact(fact_id: str) -> dict[str, str]:
        """Look up a read-only pilot fact by ID; use this instead of guessing its value."""
        print(f"PILOT_MCP_CALL fact_id={fact_id}", flush=True)
        if fact_id != PILOT_FACT_ID:
            return {"fact_id": fact_id, "error": "unknown fact ID"}
        return {"fact_id": fact_id, "value": PILOT_VALUE}

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local pilot MCP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    create_server(args.host, args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()

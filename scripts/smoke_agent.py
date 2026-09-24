"""Opt-in live API smoke test. This makes a billable OpenAI request."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.engine.agent_executor import AgentExecutor
from app.engine.project_runtime import ProjectRuntime


async def main() -> int:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("Set OPENAI_API_KEY before running the live smoke test.", file=sys.stderr)
        return 2
    runtime = ProjectRuntime.from_directory(Path(os.getenv("PROJECT_DIR", "project")))
    result = await AgentExecutor(runtime).run(
        "analyzer", "The sample report contains three sections.", {}
    )
    print(result.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

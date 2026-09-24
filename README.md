# Agentic Platform Starter Kit

Configuration-first starter kit for graph-based agentic applications.

> **Current status: Phase 7.** Individual agents run with the OpenAI Agents SDK;
> sequential, conditional, parallel and bounded-loop workflows run with LangGraph.
> The Streamlit Developer UI talks only to the API. In-memory traces, atomic
> project reload and Docker Compose are available.
> Phase 6 adds allowlisted HTTP tools, MCP Streamable HTTP connections and
> manager-owned orchestrator workflows.
> Phase 7 adds opt-in API bearer authentication and durable SQLite run storage.

## Quick start

Prerequisites: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
uv sync --locked
make validate
make check
```

No OpenAI API key is needed for validation or tests. To execute a real agent,
set `OPENAI_API_KEY` in `.env`, then:

```bash
make dev
```

Open the Developer UI at `http://127.0.0.1:8501` and the API docs at
`http://127.0.0.1:8000/docs`. To start them separately, use `make api` and
`make ui`. Set `API_BASE_URL` to point the UI at a different API.

Open `http://localhost:8000/docs` or call:

```bash
curl -X POST http://localhost:8000/agents/analyzer/run \
  -H 'Content-Type: application/json' \
  -d '{"input":"Summarize this short note","context":{}}'
```

Run the configured workflow:

```bash
curl -X POST http://localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"input":"Summarize this short note","context":{}}'
```

`GET /health`, `/project`, `/agents`, and `/workflow` are available without an API
key when `PLATFORM_API_TOKEN` is unset. Agent and workflow execution return HTTP
503 when no OpenAI API key is configured. When `PLATFORM_API_TOKEN` is set, all
endpoints except `/health` require `Authorization: Bearer <token>`.
The API is **local-development only**; bearer authentication alone does not
make it production-ready.

By default, each run is recorded in memory. Inspect recent runs with `GET /runs`, a full run
with `GET /runs/{run_id}`, and its event timeline with
`GET /runs/{run_id}/events`. Failed execution responses include an `X-Run-ID`
header so their partial trace can be inspected. `POST /reload` validates and
atomically publishes a new project revision; if validation fails, the prior
revision remains active. The UI provides these controls and a graph view.

Set `RUN_DB_PATH` to persist run records in SQLite instead of memory. Set
`PLATFORM_API_TOKEN` to require a bearer token for every endpoint except
`/health`; enter the token in the UI sidebar or provide it to the UI process as
an environment variable. Leave the token unset **only for loopback development**.

For Docker Compose:

```bash
cp .env.example .env
docker compose up --build
```

The API and UI are published on loopback ports 8000 and 8501 by default.
Docker requires a running daemon. The project directory is mounted read-only
inside the API container; edit files locally, then use Reload project in the UI.
Compose stores SQLite traces in a named volume and forwards `PLATFORM_API_TOKEN`
to both services when configured in `.env`.

For an opt-in live SDK smoke test (a billable model request):

```bash
uv run python scripts/smoke_agent.py
```

## End-to-end acceptance pilot (run it yourself)

This pilot checks configuration, a **real local MCP Streamable HTTP connection**,
one billable OpenAI agent/workflow run, the API trace, UI, optional bearer auth,
SQLite persistence and the Docker build. It uses a separate `pilot/project/`;
your normal `project/` is not changed. The MCP has one read-only tool,
`get_pilot_fact(fact_id)`, whose answer is held only by the server, not the
agent prompt. A correct result therefore exercises the tool rather than a
prompt-supplied answer. The pilot is for local development, not an MCP security
or model-quality benchmark.

**Prerequisites:** Python 3.12, `uv`, free ports 8000/8501/8765, and an OpenAI
API key with access to the model in `pilot/project/models.yaml` (currently the
same `gpt-5.6` as the main demo). Change that file to a model available to your
account if needed. Step 4 and the optional UI repeat in step 5 make billable
model requests. Do not commit
`.env`, the SQLite database or a trace containing sensitive inputs/outputs.

1. **Install and check without an API key or charge:**

   ```bash
   cp .env.example .env                 # only if .env does not already exist
   uv sync --locked
   make check
   PROJECT_DIR=pilot/project make validate
   ```

   Expected: all checks pass, and validation reports 1 agent, 1 tool and 1
   workflow node. `make check` tests the engine with fakes; it does **not** make
   a live model request.

2. **Start the pilot MCP** in terminal A, leaving it running:

   ```bash
   uv run python -m pilot.mcp_server
   ```

   It listens only on `127.0.0.1:8765/mcp` and needs no credentials. In terminal
   B, test the actual MCP handshake and tool call, still without a model charge:

   ```bash
   uv run python scripts/pilot_check.py
   ```

   Expected: `OK: pilot project valid; MCP connected; tool returned the expected
   value.` Terminal A logs `PILOT_MCP_CALL fact_id=case-7319`. If this fails,
   check port 8765 and that the MCP server is still running.

3. **Prepare and start the API/UI.** Put your real `OPENAI_API_KEY` in `.env`.
   Optionally set a strong `PLATFORM_API_TOKEN` in `.env`; the check script reads
   it automatically, and you must enter it in the UI sidebar. Start terminal C:

   ```bash
   PROJECT_DIR=pilot/project RUN_DB_PATH=.pilot-runs.sqlite3 make dev
   ```

   Open `http://127.0.0.1:8501` and confirm the Project tab says **MCP Pilot**,
   Workflow shows one `probe` node, and Agents shows `probe`. If port 8000 or
   8501 is occupied, stop the other process first; `make dev` uses those fixed
   local ports. The SQLite file is ignored by Git.

4. **Run the live acceptance request** from terminal B. This makes **one or
   more billable OpenAI model calls** (a tool-using agent run):

   ```bash
   uv run python scripts/pilot_check.py --live
   ```

   Expected: `OK: live workflow used MCP and completed; run_id=...`. The script
   asserts the exact answer `PILOT-ORBIT-7319`, one workflow step and a completed
   trace with `run_started`, `node_completed` and `run_completed`. Terminal A
   must log another `PILOT_MCP_CALL fact_id=case-7319`. The platform trace does
   not record the SDK's internal tool arguments; that server log is the direct
   evidence of the MCP call. If the response is 401, verify the token; 503 means
   the API did not receive the OpenAI key; 502 or 422 may indicate MCP/model
   connectivity, model access, timeout or agent-output issues. A failed run's
   `X-Run-ID` response header identifies its partial trace.

5. **Inspect and repeat through the UI.** In **Run & Debug**, submit text
   `case-7319` with context `{}` and confirm the same answer. In **Runs &
   Traces**, open the run ID from step 4; confirm status, node history and
   events. Use **Reload project** and confirm the revision increases. Changing
   the pilot prompt then reloading is optional; restore it afterwards. Restart
   the API with the same `RUN_DB_PATH` and confirm the previous run is still in
   `/runs` or the UI. Keep the same SQLite path when restarting; do not delete
   the file. With `PLATFORM_API_TOKEN` set, a request to `/project` without a
   bearer header should return 401 while `/health` still returns 200.

6. **Check the container path separately** (Docker Desktop/daemon required):

   ```bash
   docker compose config --quiet
   docker compose build
   docker compose up -d
   curl -fsS http://127.0.0.1:8000/health
   docker compose down
   ```

   Stop the local `make dev` process first to free ports 8000/8501. Open the
   containerized UI at `http://127.0.0.1:8501` before `down`. This Compose check
   mounts the **normal `project/`**, not `pilot/project/`; it verifies packaging,
   API/UI networking and SQLite volume startup, **not** the pilot MCP connection
   from inside a container. Do not interpret a successful Docker build as a live
   model/MCP test. If the Docker daemon is unavailable, record this step as not
   run rather than as passed. `docker compose down` preserves the named trace
   volume; `docker compose down -v` deletes it.

**Pass criteria:** steps 1–4 succeed; the UI and trace checks in step 5 agree
with the script; and step 6 succeeds when Docker is available. Keep the live
run ID and any failure logs (after removing secrets) for troubleshooting.

## Repository principle

Project-specific behavior belongs in `project/`. The engine must not import a
demo agent, prompt or schema by name. The intended acceptance test is to replace
`project/` with a different valid project and run it without changing `app/engine/`.

## Current structure

- `app/engine/`: contracts, safe config loading, registries, prompt/schema resolution,
  SDK agent construction and LangGraph workflow execution.
- `app/api/`: FastAPI routes and HTTP models.
- `app/ui/`: API-only HTTP client and Streamlit Developer UI.
- `app/engine/run_store.py`: bounded in-memory run/event repository.
- `app/engine/runtime_manager.py`: atomic runtime revision publishing.
- `project/`: minimal generic Analyzer → Reviewer → Finalizer example.
- `pilot/`: isolated one-agent project and deterministic local MCP server.
- `scripts/pilot_check.py`: free MCP preflight and opt-in billable acceptance check.
- `tests/`: unit and integration tests with no external model calls.
- `docs/`: architecture, configuration and workflow DSL decisions.

## Roadmap

1. **Phase 1 complete:** loaders, registries, prompts, schemas, individual agent
   execution, FastAPI, fake tests and an opt-in live smoke script.
2. **Phase 2 complete:** LangGraph-backed sequential and conditional workflows,
   workflow API, result selection and topology validation.
3. **Phase 3 complete:** parallel execution, bounded loops, per-visit history and
   full graph validation.
4. **Phase 4 complete:** Streamlit Developer UI, API-backed run debugging and
   two-service `make dev` startup.
5. **Phase 5 complete:** execution traces, visualization, reload and Docker.
6. **Phase 6 complete:** MCP, advanced HTTP tools and orchestrator mode.
7. **Phase 7 complete (proposed scope):** opt-in API authentication and SQLite
   trace persistence. This phase had no prior roadmap definition.

The local startup is `uv sync --locked && make dev`. The UI is also
**local-development only by default**. Traces are bounded and may contain model
outputs; enabling the token and SQLite does not by itself make the starter kit
production-ready.

## Configuration

See [configuration](docs/configuration.md) and [workflow DSL](docs/workflow-dsl.md).
For trace retention and reload behavior, see [observability](docs/observability.md).
For HTTP and MCP examples, see [tool adapters](docs/tools.md).
Configuration does not contain credentials; `.env` is ignored by Git.

## Development checks

```bash
make test
make lint
make typecheck
make check
make validate
```

`make validate` checks YAML, references, prompts, schemas, configured tools and
the full supported graph topology, including reachable termination and loop bounds.

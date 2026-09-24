# Phase 5 execution records

By default, the API keeps the most recent 100 runs in memory, with at most
5,000 events per run. Restarting the API erases them. Set `RUN_DB_PATH` to use
the SQLite `RunRepository` implementation instead; it keeps 1,000 recent runs
by default and survives restarts. Docker Compose mounts its SQLite directory
in a named volume. Both stores expose the same API contract.

Run records include kind, target, runtime revision, status, timestamps,
duration, final response or failure message, and an ordered event timeline.
Workflow events include node and branch start/completion/failure, conditional
route selections, and outputs on completion. Single-agent runs record their
start and completed output. Input and context are not retained server-side;
the Streamlit session keeps its last request for local debugging.

`GET /runs` returns lightweight summaries; `GET /runs/{id}` returns a complete
record and `GET /runs/{id}/events` returns just the event list. Failed requests
carry `X-Run-ID`. These endpoints can reveal model outputs. Set
`PLATFORM_API_TOKEN` to require `Authorization: Bearer <token>` for all endpoints
except `/health`; without it, bind the service to loopback only. The token is
opt-in and is not a substitute for TLS, secret rotation or access policy in a
production deployment.

`POST /reload` builds a new runtime from the project directory. Validation
failure returns 422 and leaves the active revision unchanged. In-flight requests
continue on their captured revision. Changes to project prompts are visible only
after a successful reload; prompt text is frozen per revision.

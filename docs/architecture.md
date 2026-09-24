# Architecture decisions

1. LangGraph owns graph topology and shared state. The OpenAI Agents SDK owns
   each agent's internal model/tool loop. The two systems do not compete to
   route deterministic workflows.
2. `project/` contains trusted, versioned project definitions. The generic engine
   may read these definitions but must not import domain-specific symbols by name.
3. Runtime construction is atomic and versioned. A failed reload retains
   the previous working runtime; in-flight runs keep their original revision.
4. API is the sole interface for the Streamlit Developer UI. No UI-to-engine imports.
5. In-memory runs/events are the zero-setup default. A repository protocol also
   supports opt-in SQLite persistence.
6. YAML cannot execute Python. Python files in `project/schemas/` remain trusted
   local code; they are not a sandbox for untrusted uploads.
7. Configuration models in `app/engine/definitions.py` validate local structure.
   `ProjectRuntime` validates cross-file references. The graph executor checks
   reachability, cycles and execution bounds. Unsupported node types are rejected
   by the configuration model.
8. Each API request constructs a fresh SDK Agent with rendered instructions. The
   registry stores definitions, not mutable per-run Agent instances.
9. SDK tracing is disabled to avoid exporting prompt/input data unexpectedly.
   Platform events record run and node lifecycle for local debugging.
10. Workflow state retains the original input and context, plus per-node results;
    LangGraph owns deterministic transitions.
11. Parallel fan-in is a single LangGraph node, so branches observe
    the same pre-node snapshot. `results` holds the latest visit while `history`
    preserves all visits. Per-node visit limits and the workflow step/time budgets
    bound cycles; strongly connected components are validated before compilation.
12. The Streamlit UI imports only its HTTP client, not `app.engine` or
    `project/`. Debugging uses API responses and server-side run records.
13. Immutable `RuntimeSnapshot` objects are published under a short lock.
    Reload builds and validates a candidate before swapping it; in-flight runs
    retain the old snapshot, including cached prompt text. The run repository is
    protocol-backed; the default in-memory implementation is bounded.
14. The UI builds a DOT graph from the API's workflow JSON, not from engine
    internals. Docker Compose runs API and UI in separate containers and binds
    published ports to loopback only.
15. Deterministic graph routing stays in LangGraph. Orchestrator mode
    is separate: a manager SDK agent calls specialists as tools and owns the
    final response. MCP Streamable HTTP connections are per run and explicitly
    tool-filtered; HTTP tools have fixed origins, paths, methods and size limits.
16. Optional bearer authentication is enforced at the FastAPI boundary. An SQLite-backed
    `RunRepository`. The in-memory store remains the zero-setup default. Compose
    persists SQLite data in a named volume; `/health` stays public for probes.

## Boundaries

```text
project YAML/prompts/schemas → engine → FastAPI → Streamlit
                              ↓
                         events/logging
```

The demo may mention Analyzer, Reviewer and Finalizer. Engine code must not.

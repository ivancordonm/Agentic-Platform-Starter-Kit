# Architecture decisions (Phase 0)

1. LangGraph will own graph topology and shared state. The OpenAI Agents SDK will
   own each agent's internal model/tool loop. The two systems will not compete to
   route deterministic workflows.
2. `project/` contains trusted, versioned project definitions. The generic engine
   may read these definitions but must not import domain-specific symbols by name.
3. Runtime construction will be atomic and versioned. A failed reload must retain
   the previous working runtime; in-flight runs keep their original revision.
4. API is the sole interface for the Streamlit Developer UI. No UI-to-engine imports.
5. In-memory runs/events are the V1 default. Persistence will be behind protocols,
   not a mandatory database.
6. YAML cannot execute Python. Python files in `project/schemas/` remain trusted
   local code; they are not a sandbox for untrusted uploads.
7. Configuration models in `app/engine/definitions.py` validate local structure.
   `ProjectRuntime` validates agent/model/tool/prompt/schema references and rejects
   unsupported configured tools or node types. Graph reachability and cycle checks
   are later layers.
8. Each API request constructs a fresh SDK Agent with rendered instructions. The
   registry stores definitions, not mutable per-run Agent instances.
9. SDK tracing is disabled for Phase 1 runs to avoid exporting prompt/input data
   unexpectedly. Internal events and explicit tracing controls arrive later.
10. Phase 2 compiles only acyclic agent-node graphs. Workflow state retains the
    original input and context, plus per-node results; LangGraph owns transitions.
11. Phase 3 adds parallel fan-in as a single LangGraph node, so branches observe
    the same pre-node snapshot. `results` holds the latest visit while `history`
    preserves all visits. Per-node visit limits and the workflow step/time budgets
    bound cycles; strongly connected components are validated before compilation.
12. Phase 4's Streamlit UI imports only its HTTP client, not `app.engine` or
    `project/`. Debugging uses the run response's selected result, latest results
    and visit history. Run data remains only in the Streamlit session for now;
    server-side persistence and traces belong to Phase 5.
13. Phase 5 publishes immutable `RuntimeSnapshot` objects under a short lock.
    Reload builds and validates a candidate before swapping it; in-flight runs
    retain the old snapshot, including cached prompt text. The run repository is
    protocol-backed and bounded in memory. SDK tracing remains disabled;
    platform events record node/branch lifecycle and outputs for local debugging.
14. The UI builds a DOT graph from the API's workflow JSON, not from engine
    internals. Docker Compose runs API and UI in separate containers and binds
    published ports to loopback only.
15. Phase 6 keeps deterministic graph routing in LangGraph. Orchestrator mode
    is separate: a manager SDK agent calls specialists as tools and owns the
    final response. MCP Streamable HTTP connections are per run and explicitly
    tool-filtered; HTTP tools have fixed origins, paths, methods and size limits.
16. Phase 7 is an explicit extension of the previously six-phase roadmap:
    optional bearer authentication at the FastAPI boundary and an SQLite-backed
    `RunRepository`. The in-memory store remains the zero-setup default. Compose
    persists SQLite data in a named volume; `/health` stays public for probes.

## Boundaries

```text
project YAML/prompts/schemas → engine → FastAPI → Streamlit
                              ↓
                         events/logging
```

The demo may mention Analyzer, Reviewer and Finalizer. Engine code must not.

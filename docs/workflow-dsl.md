# Workflow DSL, schema version 1

The current demo illustrates the graph syntax. Phase 3 compiles agent and parallel
nodes, conditional transitions and bounded loops with LangGraph.

Phase 6 also supports a separate manager-owned orchestrator workflow:

```yaml
workflow:
  name: managed-demo
  type: orchestrator
  manager: finalizer
  specialists: [analyzer, reviewer]
  limits:
    max_steps: 10
    timeout_seconds: 120
```

The manager calls specialists as SDK agent tools and owns the final result.
`max_steps` caps manager SDK turns in this mode; specialist turns retain each
agent's `max_turns`. Specialist agents cannot declare an `input_schema` in this
mode because SDK agent tools receive text input. Graph workflows remain the
deterministic choice when routing and per-node state must be explicit.

## Nodes

- `agent`: invokes one named agent. `max_visits` defaults to 1.
- `parallel`: named branches, each invoking one agent; fan-in waits for all.
  `max_concurrency` limits simultaneous branch calls. `fail_fast` cancels unfinished
  siblings on the first failure; `collect_errors` returns partial outputs and
  per-branch errors.
- `router`, `human_approval`: reserved syntax, not executable in V1.

`start` and `END` are graph markers, not user-defined node types. A loop is an
edge back to an earlier node, controlled by `max_visits` and workflow `max_steps`.
There is no separate `loop` node in V1.

## Transitions

Direct transition:

```yaml
- from: analyze
  to: review
```

Conditional transition:

```yaml
- from: review
  select: results.review.output.status
  cases:
    approved: finalize
    rejected: fail
  default: fail
```

Selectors are deliberately limited to dotted paths under `input`, `context`,
`results` or `metadata`. `fail` is a terminal routing error; `END` terminates
successfully. Neither expressions nor arbitrary Python are allowed in YAML.

The workflow `output.select` explicitly chooses the final result. The latest result
per node is stored at `results.<node>.output`. Parallel outputs are keyed by branch,
for example `results.fan.output.analysis`; branch status and metadata are at
`results.fan.branches.analysis`. A failed branch under `collect_errors` has a null
output and a failed status with an error message. Prior results are passed into
subsequent agent prompts. The API also returns `history`, an ordered record of
every node visit, so loop iterations are not lost when `results` is overwritten.
Conditional case keys compare scalar selector values as strings; booleans use
lowercase `true` and `false`. Missing selectors and `fail` routes stop execution.

Each node visit consumes one workflow step; a parallel fan-out counts as one step
and its branch calls are limited by `max_concurrency`. Cyclic regions require
`max_visits > 1` on every node, and all runs are capped by workflow `max_steps`
and `timeout_seconds`. Validation rejects ambiguous transitions, unreachable
nodes, nodes with no possible path to `END`, unknown result-node selectors and
unbounded cycles. A valid graph can still fail at runtime if its conditions never
choose an exit before the visit or step limit.

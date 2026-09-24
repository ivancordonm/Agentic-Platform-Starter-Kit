# Configuration contract, schema version 1

The `project/` directory contains five YAML files. Each begins with
`schema_version: 1`. Unknown keys and incompatible schema versions are errors.

| File | Root key | Purpose |
| --- | --- | --- |
| `project.yaml` | `project` | Name, description and default execution limits |
| `models.yaml` | `models` | Logical model IDs, provider and model settings |
| `agents.yaml` | `agents` | Agent IDs, prompt paths, tools and schemas |
| `tools.yaml` | `tools`, `mcp_servers` | Tool and MCP server declarations |
| `workflow.yaml` | `workflow` | Graph nodes, transitions and final output |

IDs begin with a letter and may contain letters, digits, underscores and hyphens.
Agent prompts use project-relative paths. The runtime loader must additionally
resolve paths and reject symlink escapes; regex validation alone is not sufficient.

Secrets are supplied through environment variables. The future config loader will
interpolate `${VARIABLE}` only from an explicit allowlist of fields, report missing
variables, and redact values in diagnostics. Interpolation is **not implemented**
in Phase 0.

`openai` is the intended initial provider. A logical model ID such as `default`
keeps agents independent of the provider's constructor. Runtime compatibility of
settings such as `temperature` and `reasoning` must be checked when the adapter is
implemented.

Schemas under `project/schemas/` are trusted Python. YAML refers to a schema by
class name, not an import path. Phase 1 discovers Pydantic `BaseModel`
subclasses, rejects duplicate names and resolves cross-file references.

`make validate` checks references from agents to models, tools, prompts and
schemas, plus node-to-agent references, graph reachability, successful termination
paths, ambiguous transitions and loop bounds. Unsupported features are rejected.

The engine supports the `openai` provider, read-only `filesystem` tools,
allowlisted HTTP tools, and Streamable HTTP MCP servers. HTTP definitions need
an origin-only HTTPS URL (or loopback HTTP), explicit `allowed_paths` and
`allowed_methods`, and may name an `auth_env` variable for a bearer token.
HTTP calls do not follow redirects; query/body size and response size are
bounded. The tool accepts JSON strings for query and body because the Agents
SDK requires a closed tool argument schema.

An MCP tool references a named server and must list `allowed_tools` explicitly.
MCP server URLs follow the same HTTPS/loopback origin rule and can use
`auth_env`. Connections are opened for a run and closed afterward. Project
configuration is trusted local code, but MCP servers and HTTP endpoints still
need independent trust review. Secrets stay in environment variables, never YAML.

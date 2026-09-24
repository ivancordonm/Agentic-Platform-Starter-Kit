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
Agent prompts use project-relative paths. The runtime loader resolves paths and
rejects symlink escapes; regex validation alone is not sufficient.

Secrets are supplied through environment variables. YAML `${VARIABLE}`
interpolation is not supported. Use environment variables such as
`OPENAI_API_KEY` and the `auth_env` field of HTTP or MCP tool definitions;
never put secret values in project files.

`openai` is the supported provider. A logical model ID such as `default` keeps
agents independent of the provider's constructor. The adapter passes settings
such as `temperature` and `reasoning` to the SDK; whether a particular model
accepts them may only be known when a live request is made.

Schemas under `project/schemas/` are trusted Python. YAML refers to a schema by
class name, not an import path. The loader discovers Pydantic `BaseModel`
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

The filesystem tool defaults to `project/knowledge/` unless `root` is set. Files
there are not automatically placed in an agent's context; declare the tool and
attach it to an agent to enable on-demand reads. See the
[knowledge directory guide](../project/knowledge/README.md).

An MCP tool references a named server and must list `allowed_tools` explicitly.
MCP server URLs follow the same HTTPS/loopback origin rule and can use
`auth_env`. Connections are opened for a run and closed afterward. Project
configuration is trusted local code, but MCP servers and HTTP endpoints still
need independent trust review. Secrets stay in environment variables, never YAML.

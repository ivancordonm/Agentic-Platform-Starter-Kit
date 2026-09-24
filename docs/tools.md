# Tool adapters

Tools are declared in `project/tools.yaml` and attached by ID in `agents.yaml`.
Only tools referenced by an agent are constructed. Validation checks URLs,
paths, server references and allowlists without opening network connections.

The built-in read-only `filesystem` tool can expose files under `project/knowledge/`
when explicitly attached to an agent. See its [usage guide](../project/knowledge/README.md).

```yaml
schema_version: 1
tools:
  catalog:
    type: http
    base_url: https://api.example.com
    allowed_paths: [/items]
    allowed_methods: [GET]
    timeout_seconds: 10
    auth_env: CATALOG_TOKEN
  docs:
    type: mcp
    server: docs-server
    allowed_tools: [search]
mcp_servers:
  docs-server:
    url: https://mcp.example.com/mcp
    timeout_seconds: 30
    auth_env: DOCS_MCP_TOKEN
```

The HTTP adapter exposes a function tool named `<id>_request` with `method`,
`path`, `query_json` and `body_json` string arguments. The last two are JSON
object strings (`{}` and empty string by default). Exact method/path allowlists
are enforced, redirects are not followed, query/body sizes are limited, and
responses above 100 KB are rejected. The model cannot supply a different host.
Only HTTPS origins or loopback HTTP are accepted for the HTTP adapter.

MCP uses Streamable HTTP, opens connections per run, and filters the advertised
tools to `allowed_tools`. MCP URLs may include an endpoint path such as `/mcp`.
This starter does not launch stdio servers or implement approval/resume flows;
connect only to trusted servers and expose read-only tools unless you add a
review mechanism. Auth variables provide bearer tokens at runtime. Never put
tokens in YAML or URL query strings.

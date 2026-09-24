# Knowledge

Put project-owned UTF-8 reference documents here if your agents need to look up
facts during a run. Files in this directory are **not** added to prompts or model
context automatically.

To let an agent read them, declare a read-only filesystem tool in `project/tools.yaml`:

```yaml
schema_version: 1
tools:
  knowledge:
    type: builtin
    implementation: filesystem
    root: knowledge
    read_only: true
mcp_servers: {}
```

Then add `knowledge` to that agent's `tools` list in `project/agents.yaml` and
instruct it in its prompt when to call `knowledge_read_file` with a path relative
to this directory. The tool reads one UTF-8 file at a time (up to 100 KB), cannot
follow symlinks outside this directory, and does not index or search documents.
The demo agents do not use it by default; if you do not need local documents,
you can delete this directory.

Do not put secrets or untrusted instructions here. Review the files before
granting an agent access, and reload the project after changing its YAML or
prompts. Document contents are read when the tool is called.

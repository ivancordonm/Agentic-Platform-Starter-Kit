"""API-backed Streamlit Developer UI for inspecting and debugging runs."""

from __future__ import annotations

import json
import os
from typing import Any

import streamlit as st

from app.ui.client import ApiClient, ApiError, ApiUnavailable


def workflow_dot(workflow: dict[str, Any], visited: set[str] | None = None) -> str:
    """Build a DOT view from the API workflow definition, without engine imports."""
    visited = visited or set()
    if workflow.get("type") == "orchestrator":
        manager = str(workflow.get("manager", "manager"))
        lines = [
            "digraph workflow {", "rankdir=LR;", "node [shape=box, style=rounded];",
            f"{json.dumps(manager)} [shape=doubleoctagon];",
        ]
        for specialist in workflow.get("specialists", []):
            if isinstance(specialist, str):
                lines.append(
                    f"{json.dumps(manager)} -> {json.dumps(specialist)} "
                    '[label="delegates", dir=both];'
                )
        return "\n".join([*lines, "}"])
    lines = [
        "digraph workflow {", "rankdir=LR;", "node [shape=box, style=rounded];",
        '"START" [shape=circle];', '"END" [shape=doublecircle];',
    ]
    nodes = workflow.get("nodes", {})
    if isinstance(nodes, dict):
        for name, definition in nodes.items():
            if not isinstance(definition, dict):
                continue
            kind = definition.get("type", "node")
            if kind == "agent":
                detail = str(definition.get("agent", ""))
            elif kind == "parallel":
                detail = f"{len(definition.get('branches', {}))} branches"
            else:
                detail = str(kind)
            label = f"{name}\n{detail}"
            style = ', style="rounded,filled", fillcolor="#D9F2E6"' if name in visited else ""
            lines.append(f"{json.dumps(name)} [label={json.dumps(label)}{style}];")
    start = workflow.get("start")
    if isinstance(start, str):
        lines.append(f'"START" -> {json.dumps(start)};')
    for transition in workflow.get("transitions", []):
        if not isinstance(transition, dict):
            continue
        source = transition.get("from")
        if not isinstance(source, str):
            continue
        if "to" in transition:
            target = transition["to"]
            if isinstance(target, str):
                lines.append(f"{json.dumps(source)} -> {json.dumps(target)};")
        else:
            cases = transition.get("cases", {})
            if isinstance(cases, dict):
                for value, target in cases.items():
                    if isinstance(target, str):
                        lines.append(
                            f"{json.dumps(source)} -> {json.dumps(target)} "
                            f"[label={json.dumps(str(value))}];"
                        )
            default = transition.get("default", "fail")
            if isinstance(default, str):
                lines.append(
                    f"{json.dumps(source)} -> {json.dumps(default)} [label=\"default\"] ;"
                )
    if any('"fail"' in line for line in lines[5:]):
        lines.append('"fail" [shape=octagon, color=red];')
    lines.append("}")
    return "\n".join(lines)


def parse_payload(
    input_text: str, input_format: str, context_text: str
) -> tuple[str | dict[str, Any], dict[str, Any]]:
    try:
        context = json.loads(context_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Context is not valid JSON: {exc.msg}") from exc
    if not isinstance(context, dict):
        raise ValueError("Context must be a JSON object")
    if input_format == "Text":
        return input_text, context
    try:
        input_value = json.loads(input_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Input is not valid JSON: {exc.msg}") from exc
    if not isinstance(input_value, dict):
        raise ValueError("JSON input must be an object")
    return input_value, context


def render_run(run: dict[str, Any], request: dict[str, Any]) -> None:
    st.subheader("Last run")
    st.caption(f"Run ID: {run.get('run_id', 'unknown')}")
    columns = st.columns(3)
    columns[0].metric("Status", str(run.get("status", "unknown")))
    columns[1].metric("Duration", f"{run.get('duration_ms', '?')} ms")
    columns[2].metric("Steps", str(run.get("steps", 1)))
    with st.expander("Request", expanded=False):
        st.json(request)
    st.markdown("**Selected result**")
    st.json(run.get("result"))
    history = run.get("history")
    if isinstance(history, list):
        st.markdown("**Node history**")
        if not history:
            st.info("No node visits were returned.")
        for index, entry in enumerate(history, start=1):
            if not isinstance(entry, dict):
                continue
            label = f"{index}. {entry.get('node', 'node')} · visit {entry.get('visit', '?')}"
            with st.expander(label, expanded=False):
                if "output" in entry:
                    st.markdown("Output")
                    st.json(entry["output"])
                if "branches" in entry:
                    st.markdown("Branches")
                    st.json(entry["branches"])
                st.markdown("Full record")
                st.json(entry)
        with st.expander("Latest results by node", expanded=False):
            st.json(run.get("results", {}))
    else:
        with st.expander("Agent metadata", expanded=False):
            st.json(run.get("metadata", {}))
    st.download_button(
        "Download run JSON",
        data=json.dumps({"request": request, "response": run}, indent=2, ensure_ascii=False),
        file_name=f"run-{run.get('run_id', 'unknown')}.json",
        mime="application/json",
    )


def main() -> None:
    st.set_page_config(page_title="Agentic Developer UI", page_icon="🧭", layout="wide")
    st.title("Agentic Developer UI")
    st.caption("Inspect configuration and debug runs through the FastAPI service.")

    default_url = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
    with st.sidebar:
        st.header("Connection")
        api_url = st.text_input("API URL", value=default_url).strip()
        api_token = st.text_input(
            "API token", value=os.getenv("PLATFORM_API_TOKEN", ""), type="password"
        )
        if st.button("Refresh API data"):
            st.rerun()
        reload_requested = st.button("Reload project")
        st.caption("The UI uses HTTP only; it does not import the engine or read project files.")
    try:
        client = ApiClient(api_url, token=api_token or None)
        if reload_requested:
            try:
                reloaded = client.reload()
                st.session_state["reload_feedback"] = (
                    f"Project reloaded: revision {reloaded.get('revision', '?')}"
                )
            except (ApiUnavailable, ApiError) as exc:
                st.session_state["reload_feedback"] = f"Reload failed: {exc}"
        health = client.health()
        project = client.project()
        agents = client.agents()
        workflow = client.workflow()
    except (ApiUnavailable, ApiError, ValueError) as exc:
        st.error(str(exc))
        st.info("Start the API with `make api`, or use `make dev` for both services.")
        st.stop()
    with st.sidebar:
        st.success(f"API: {health.get('status', 'connected')}")
        st.caption(str(project.get("name", "Project")))
        st.caption(f"Revision: {project.get('revision', '?')}")
        if feedback := st.session_state.get("reload_feedback"):
            st.info(feedback)

    project_tab, workflow_tab, agents_tab, run_tab, traces_tab = st.tabs(
        ["Project", "Workflow", "Agents", "Run & Debug", "Runs & Traces"]
    )
    with project_tab:
        st.subheader(str(project.get("name", "Project")))
        st.write(project.get("description", ""))
        columns = st.columns(3)
        columns[0].metric("Agents", str(project.get("agents", "?")))
        columns[1].metric("Models", str(project.get("models", "?")))
        columns[2].metric("Tools", str(project.get("tools", "?")))
        st.caption(f"Workflow: {project.get('workflow', 'unknown')}")
    with workflow_tab:
        st.subheader(str(workflow.get("name", "Workflow")))
        if workflow.get("type") == "orchestrator":
            st.caption(
                f"Manager: {workflow.get('manager', '?')} · "
                f"Specialists: {', '.join(workflow.get('specialists', []))}"
            )
        else:
            st.caption(f"Start: {workflow.get('start', '?')}")
        last_run = st.session_state.get("last_run", {})
        history = last_run.get("history", []) if isinstance(last_run, dict) else []
        visited = {
            item["node"] for item in history
            if isinstance(item, dict) and isinstance(item.get("node"), str)
        }
        st.graphviz_chart(workflow_dot(workflow, visited), width="stretch")
        nodes = workflow.get("nodes", {})
        if isinstance(nodes, dict):
            st.markdown("**Nodes**")
            st.table([{"node": name, **value} for name, value in nodes.items()])
        st.markdown("**Transitions**")
        st.json(workflow.get("transitions", []))
        with st.expander("Full workflow configuration"):
            st.json(workflow)
    with agents_tab:
        st.subheader("Configured agents")
        if not agents:
            st.info("No agents were returned by the API.")
        for name, definition in agents.items():
            with st.expander(name):
                st.json(definition)
    with run_tab:
        st.subheader("Run and debug")
        target = st.radio("Target", ["Workflow", "Agent"], horizontal=True)
        agent_name = st.selectbox("Agent", list(agents)) if target == "Agent" else None
        with st.form("run_form"):
            input_format = st.radio("Input format", ["Text", "JSON object"], horizontal=True)
            input_text = st.text_area("Input", height=150)
            context_text = st.text_area("Context (JSON object)", value="{}", height=90)
            submitted = st.form_submit_button("Run", type="primary")
        if submitted:
            try:
                input_value, context = parse_payload(input_text, input_format, context_text)
                request: dict[str, Any] = {"input": input_value, "context": context}
                if target == "Agent":
                    if agent_name is None:
                        raise ValueError("Choose an agent")
                    run = client.run_agent(agent_name, input_value, context)
                else:
                    run = client.run_workflow(input_value, context)
                st.session_state["last_run"] = run
                st.session_state["last_request"] = request
                st.session_state["last_api_url"] = api_url
                st.session_state.pop("last_error", None)
            except (ApiUnavailable, ApiError, ValueError) as exc:
                st.session_state.pop("last_run", None)
                st.session_state["last_error"] = str(exc)
                st.session_state["last_api_url"] = api_url
                if isinstance(exc, ApiError) and exc.run_id:
                    st.session_state["last_failed_run_id"] = exc.run_id
        if st.session_state.get("last_api_url") == api_url and (
            error := st.session_state.get("last_error")
        ):
            st.error(error)
            if failed_id := st.session_state.get("last_failed_run_id"):
                st.caption(f"Failed run ID: {failed_id}. Inspect it in Runs & Traces.")
        if st.session_state.get("last_api_url") == api_url and (
            last_run := st.session_state.get("last_run")
        ):
            render_run(last_run, st.session_state["last_request"])
    with traces_tab:
        st.subheader("Recent runs and event traces")
        try:
            runs = client.runs()
        except (ApiUnavailable, ApiError) as exc:
            st.error(str(exc))
            runs = []
        if not runs:
            st.info("No runs are stored yet. Execute an agent or workflow to create a trace.")
        else:
            st.table(runs)
            run_ids = [str(record["run_id"]) for record in runs]
            selected_run = st.selectbox("Inspect run", run_ids)
            try:
                detail = client.run_detail(selected_run)
                events = client.run_events(selected_run)
            except (ApiUnavailable, ApiError) as exc:
                st.error(str(exc))
            else:
                st.caption(
                    f"{detail.get('kind', 'run')} · {detail.get('status', '?')} · "
                    f"revision {detail.get('revision', '?')}"
                )
                st.markdown("**Event timeline**")
                st.table([
                    {key: event.get(key) for key in ("time", "type", "node", "visit", "branch")}
                    for event in events
                ])
                if events:
                    event_index = st.selectbox(
                        "Inspect event", list(range(len(events))),
                        format_func=lambda index: (
                            f"{index + 1}. {events[index].get('type', 'event')}"
                        ),
                    )
                    st.json(events[event_index])
                with st.expander("Full run record"):
                    st.json(detail)


if __name__ == "__main__":
    main()

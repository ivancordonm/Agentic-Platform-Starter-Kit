"""Developer UI HTTP boundary and input validation tests."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.ui.client import ApiClient, ApiError, ApiUnavailable
from app.ui.streamlit_app import parse_payload


def test_client_uses_public_api_only() -> None:
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, payload))
        return httpx.Response(200, json={"ok": True})

    client = ApiClient("http://test.local/", transport=httpx.MockTransport(handler))
    assert client.health() == {"ok": True}
    assert client.project() == {"ok": True}
    assert client.agents() == {"ok": True}
    assert client.workflow() == {"ok": True}
    assert client.run_agent("analyzer", "Hello", {"x": 1}) == {"ok": True}
    assert client.run_workflow({"question": "Hi"}, {}) == {"ok": True}
    assert [path for _, path, _ in calls] == [
        "/health", "/project", "/agents", "/workflow", "/agents/analyzer/run", "/run"
    ]
    assert calls[-2][2] == {"input": "Hello", "context": {"x": 1}}


def test_client_reports_api_and_transport_errors() -> None:
    error_client = ApiClient(
        "http://test.local",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, json={"detail": "No model key"})
        ),
    )
    with pytest.raises(ApiError, match="API 503: No model key"):
        error_client.run_workflow("Hi", {})

    non_json_client = ApiClient(
        "http://test.local",
        transport=httpx.MockTransport(lambda request: httpx.Response(502, text="bad gateway")),
    )
    with pytest.raises(ApiUnavailable, match="non-JSON"):
        non_json_client.health()

    def disconnected(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    disconnected_client = ApiClient(
        "http://test.local", transport=httpx.MockTransport(disconnected)
    )
    with pytest.raises(ApiUnavailable, match="Cannot reach API"):
        disconnected_client.health()


def test_client_loads_traces_and_preserves_failed_run_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/runs":
            return httpx.Response(200, json=[{"run_id": "abc"}])
        if request.url.path == "/runs/abc/events":
            return httpx.Response(200, json=[{"type": "run_started"}])
        if request.url.path == "/runs/abc":
            return httpx.Response(200, json={"run_id": "abc", "status": "completed"})
        if request.url.path == "/reload":
            return httpx.Response(200, json={"revision": 2})
        return httpx.Response(502, json={"detail": "Failed"}, headers={"X-Run-ID": "bad"})

    client = ApiClient("http://test.local", transport=httpx.MockTransport(handler))
    assert client.runs() == [{"run_id": "abc"}]
    assert client.run_detail("abc")["status"] == "completed"
    assert client.run_events("abc") == [{"type": "run_started"}]
    assert client.reload()["revision"] == 2
    with pytest.raises(ApiError) as error:
        client.run_workflow("Hi", {})
    assert error.value.run_id == "bad"


def test_client_sends_bearer_token_without_exposing_it_in_payload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = ApiClient("http://test.local", token="secret", transport=httpx.MockTransport(handler))
    client.run_workflow("Hi", {})
    assert seen[0].headers["Authorization"] == "Bearer secret"
    assert b"secret" not in seen[0].content


@pytest.mark.parametrize("url", ["", "localhost:8000", "file:///tmp/api"])
def test_client_requires_http_url(url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        ApiClient(url)


def test_parse_payload_accepts_text_or_json_object() -> None:
    assert parse_payload("Hello", "Text", '{"id": 42}') == ("Hello", {"id": 42})
    assert parse_payload('{"question": "Hi"}', "JSON object", "{}") == (
        {"question": "Hi"}, {}
    )


@pytest.mark.parametrize(
    ("input_text", "input_format", "context_text", "message"),
    [
        ("Hi", "Text", "[1]", "Context must be a JSON object"),
        ("Hi", "Text", "{", "Context is not valid JSON"),
        ("[1]", "JSON object", "{}", "JSON input must be an object"),
        ("{", "JSON object", "{}", "Input is not valid JSON"),
    ],
)
def test_parse_payload_rejects_invalid_json_shapes(
    input_text: str, input_format: str, context_text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_payload(input_text, input_format, context_text)

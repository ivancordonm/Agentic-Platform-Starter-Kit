"""Small HTTP client for the Developer UI's public API boundary."""

from __future__ import annotations

from typing import Any

import httpx


class ApiUnavailable(Exception):
    """The configured API cannot be reached or returned an invalid response."""


class ApiError(Exception):
    """The API returned a well-formed non-success response."""

    def __init__(self, status_code: int, detail: str, run_id: str | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.run_id = run_id
        super().__init__(f"API {status_code}: {detail}")


class ApiClient:
    def __init__(
        self, base_url: str, *, token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("API URL must start with http:// or https://")
        self.transport = transport
        self.token = token

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        try:
            with httpx.Client(
                base_url=self.base_url,
                transport=self.transport,
                timeout=httpx.Timeout(3700.0, connect=5.0),
                headers={"Authorization": f"Bearer {self.token}"} if self.token else {},
            ) as client:
                response = client.request(method, path, json=payload)
        except httpx.RequestError as exc:
            raise ApiUnavailable(f"Cannot reach API at {self.base_url}: {exc}") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise ApiUnavailable("API returned a non-JSON response") from exc
        if response.is_error:
            detail = (
                body.get("detail", body.get("message", "Request failed"))
                if isinstance(body, dict) else "Request failed"
            )
            raise ApiError(response.status_code, str(detail), response.headers.get("X-Run-ID"))
        return body

    def _object(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = self._request(method, path, payload)
        if not isinstance(body, dict):
            raise ApiUnavailable("API returned an unexpected JSON response")
        return body

    def _list(self, path: str) -> list[dict[str, Any]]:
        body = self._request("GET", path)
        if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
            raise ApiUnavailable("API returned an unexpected JSON response")
        return body

    def health(self) -> dict[str, Any]:
        return self._object("GET", "/health")

    def project(self) -> dict[str, Any]:
        return self._object("GET", "/project")

    def agents(self) -> dict[str, Any]:
        return self._object("GET", "/agents")

    def workflow(self) -> dict[str, Any]:
        return self._object("GET", "/workflow")

    def reload(self) -> dict[str, Any]:
        return self._object("POST", "/reload")

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._list(f"/runs?limit={limit}")

    def run_detail(self, run_id: str) -> dict[str, Any]:
        return self._object("GET", f"/runs/{run_id}")

    def run_events(self, run_id: str) -> list[dict[str, Any]]:
        return self._list(f"/runs/{run_id}/events")

    def run_agent(
        self, name: str, input_value: str | dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        return self._object(
            "POST", f"/agents/{name}/run", {"input": input_value, "context": context}
        )

    def run_workflow(
        self, input_value: str | dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        return self._object("POST", "/run", {"input": input_value, "context": context})

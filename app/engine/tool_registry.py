"""Resolve trusted project tools with strict filesystem, HTTP and MCP boundaries."""

from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import unquote

import httpx
from agents import FunctionTool, function_tool
from agents.mcp import MCPServer, MCPServerStreamableHttp, create_static_tool_filter

from app.engine.definitions import (
    BuiltinToolDefinition,
    HttpToolDefinition,
    McpServerDefinition,
    McpToolDefinition,
    ToolDefinition,
)
from app.engine.exceptions import ConfigurationError, ToolNotFoundError


def read_project_file(root: Path, path: str) -> str:
    """Read only a small UTF-8 file resolved inside an allowlisted root."""
    if path.startswith("/") or "\\" in path or "\x00" in path:
        return "Invalid path"
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return "File not found or outside allowed directory"
    if target.stat().st_size > 100_000:
        return "File exceeds 100 KB limit"
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "File could not be read"


def validate_service_url(value: str, *, origin_only: bool = True) -> httpx.URL:
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError) as exc:
        raise ConfigurationError("Invalid tool service URL") from exc
    if not url.host or url.userinfo or url.query or url.fragment:
        raise ConfigurationError("Tool service URL must be an origin without credentials")
    if url.scheme != "https" and not (
        url.scheme == "http" and url.host in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ConfigurationError("Tool service URL must use HTTPS or loopback HTTP")
    if origin_only and url.path not in {"", "/"}:
        raise ConfigurationError("Tool service URL must not include a path")
    if not origin_only:
        validate_http_path(url.path)
    return url


def validate_http_path(path: str) -> str:
    decoded = unquote(path)
    if (
        not path.startswith("/") or path.startswith("//")
        or "?" in path or "#" in path or "\\" in decoded or "\x00" in decoded
        or any(part in {".", ".."} for part in decoded.split("/"))
    ):
        raise ConfigurationError(f"Invalid HTTP tool path: {path!r}")
    return path


class ToolRegistry:
    def __init__(
        self,
        project_root: Path,
        definitions: dict[str, ToolDefinition],
        mcp_servers: dict[str, McpServerDefinition] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self._definitions = definitions
        self._mcp_servers = mcp_servers or {}

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Unknown tool: {name}") from exc

    def validate(self, name: str) -> None:
        definition = self.get(name)
        if isinstance(definition, BuiltinToolDefinition):
            root = (self.project_root / (definition.root or "knowledge")).resolve()
            if not root.is_relative_to(self.project_root) or not root.is_dir():
                raise ConfigurationError(f"Tool {name!r} has an invalid filesystem root")
        elif isinstance(definition, HttpToolDefinition):
            validate_service_url(definition.base_url)
            for path in definition.allowed_paths:
                validate_http_path(path)
            if not definition.allowed_methods:
                raise ConfigurationError(f"Tool {name!r} has no allowed HTTP methods")
        elif isinstance(definition, McpToolDefinition):
            server = self._mcp_servers.get(definition.server)
            if server is None:
                raise ConfigurationError(f"Tool {name!r} references unknown MCP server")
            validate_service_url(server.url, origin_only=False)
        else:
            raise ConfigurationError(f"Tool {name!r} has an unsupported type")

    def resolve(self, name: str) -> FunctionTool:
        self.validate(name)
        definition = self.get(name)
        if isinstance(definition, BuiltinToolDefinition):
            root = (self.project_root / (definition.root or "knowledge")).resolve()

            @function_tool(name_override=f"{name}_read_file")
            def read_file(path: str) -> str:
                """Read a UTF-8 text file from the configured project knowledge directory."""
                return read_project_file(root, path)

            return read_file
        if isinstance(definition, HttpToolDefinition):
            base_url = validate_service_url(definition.base_url)

            @function_tool(name_override=f"{name}_request")
            async def http_request(
                method: str, path: str, query_json: str = "{}", body_json: str = "",
            ) -> str:
                """Call an allowlisted endpoint with JSON object strings for query and body."""
                if method not in definition.allowed_methods or path not in definition.allowed_paths:
                    return "HTTP method or path is not allowed"
                if len(query_json) > 4000:
                    return "Query exceeds limit"
                if len(body_json) > 64_000:
                    return "JSON body exceeds limit"
                try:
                    query = json.loads(query_json)
                    json_body = json.loads(body_json) if body_json else None
                except ValueError:
                    return "Invalid JSON query or body"
                if not isinstance(query, dict) or len(query) > 20 or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in query.items()
                ):
                    return "Query must be a small JSON object of strings"
                if json_body is not None and not isinstance(json_body, dict):
                    return "Body must be a JSON object"
                if method == "GET" and json_body is not None:
                    return "GET cannot include a JSON body"
                headers: dict[str, str] = {}
                if definition.auth_env:
                    token = os.getenv(definition.auth_env)
                    if not token:
                        return "HTTP tool credential is not configured"
                    headers["Authorization"] = f"Bearer {token}"
                url = base_url.copy_with(path=path)
                try:
                    async with httpx.AsyncClient(
                        timeout=definition.timeout_seconds, follow_redirects=False
                    ) as client:
                        async with client.stream(
                            method, url, params=query, json=json_body, headers=headers
                        ) as response:
                            data = bytearray()
                            async for chunk in response.aiter_bytes():
                                data.extend(chunk)
                                if len(data) > 100_000:
                                    return "HTTP response exceeds 100 KB limit"
                            content = data.decode("utf-8", errors="replace")
                            return f"HTTP {response.status_code}: {content}"
                except httpx.RequestError:
                    return "HTTP request failed"

            return http_request
        raise ConfigurationError(f"MCP tool {name!r} must be attached as an MCP server")

    async def open_mcp(
        self, tool_names: list[str], stack: AsyncExitStack
    ) -> list[MCPServer]:
        servers: list[MCPServer] = []
        for name in tool_names:
            definition = self.get(name)
            if not isinstance(definition, McpToolDefinition):
                continue
            self.validate(name)
            server_definition = self._mcp_servers[definition.server]
            headers: dict[str, str] = {}
            if server_definition.auth_env:
                token = os.getenv(server_definition.auth_env)
                if not token:
                    raise ConfigurationError(
                        f"MCP server {definition.server!r} credential is missing"
                    )
                headers["Authorization"] = f"Bearer {token}"
            server = MCPServerStreamableHttp(
                name=definition.server,
                params={
                    "url": server_definition.url,
                    "headers": headers,
                    "timeout": server_definition.timeout_seconds,
                },
                tool_filter=create_static_tool_filter(allowed_tool_names=definition.allowed_tools),
                cache_tools_list=True,
            )
            servers.append(await stack.enter_async_context(server))
        return servers

    def is_mcp(self, name: str) -> bool:
        return isinstance(self.get(name), McpToolDefinition)

    def all(self) -> dict[str, ToolDefinition]:
        return dict(self._definitions)

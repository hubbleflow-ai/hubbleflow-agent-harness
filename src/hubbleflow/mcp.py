"""MCP servers, configured the way Claude Code configures them.

Reads `.mcp.json` from the workspace and `~/.hubbleflow/mcp.json`, connects to
whatever they declare, and hands the resulting tools to the agent. A server
that won't start is reported and skipped -- it never blocks the session.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from hubbleflow.config import CONFIG_DIR

CONFIG_NAME = ".mcp.json"
GLOBAL_CONFIG = CONFIG_DIR / "mcp.json"
CONNECT_TIMEOUT = 30.0


@dataclass(slots=True)
class Servers:
    """The result of trying to bring up every configured server."""

    tools: list = field(default_factory=list)
    connected: dict[str, list[str]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    configured: int = 0

    @property
    def any_configured(self) -> bool:
        return self.configured > 0


def config_paths(workspace: Path) -> list[Path]:
    """Where a server definition may live, least specific first."""
    return [GLOBAL_CONFIG, workspace / CONFIG_NAME]


def read_config(workspace: Path) -> dict[str, dict]:
    """Merge every `mcpServers` block that exists; the workspace wins on a clash."""
    merged: dict[str, dict] = {}
    for path in config_paths(workspace):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        servers = payload.get("mcpServers") or payload.get("servers") or {}
        if isinstance(servers, dict):
            merged.update({name: spec for name, spec in servers.items() if isinstance(spec, dict)})
    return merged


def to_connection(spec: dict) -> dict:
    """Translate one Claude-Code-shaped entry into a langchain-mcp connection."""
    transport = spec.get("transport") or spec.get("type")
    if spec.get("command"):
        return {
            "transport": "stdio",
            "command": spec["command"],
            "args": list(spec.get("args") or []),
            "env": {**os.environ, **(spec.get("env") or {})},
            "cwd": spec.get("cwd"),
        }
    url = spec.get("url") or spec.get("endpoint")
    if not url:
        msg = "entry has neither a `command` nor a `url`"
        raise ValueError(msg)
    if transport in {"sse", "websocket"}:
        return {"transport": transport, "url": url, "headers": spec.get("headers") or {}}
    return {"transport": "streamable_http", "url": url, "headers": spec.get("headers") or {}}


async def connect(workspace: Path) -> Servers:
    """Start every configured server and collect its tools."""
    config = read_config(workspace)
    result = Servers(configured=len(config))
    if not config:
        return result

    connections, invalid = {}, {}
    for name, spec in config.items():
        if spec.get("disabled") or spec.get("enabled") is False:
            continue
        try:
            connections[name] = to_connection(spec)
        except ValueError as error:
            invalid[name] = str(error)
    result.failed.update(invalid)
    if not connections:
        return result

    from langchain_mcp_adapters.client import MultiServerMCPClient

    # One client per server: a single bad server shouldn't take the rest down,
    # and get_tools() on a combined client is all-or-nothing.
    for name, connection in connections.items():
        try:
            client = MultiServerMCPClient({name: connection})
            tools = await client.get_tools(server_name=name)
        except Exception as error:
            result.failed[name] = _brief(error)
            continue
        result.tools.extend(tools)
        result.connected[name] = [tool.name for tool in tools]

    return result


def _brief(error: Exception) -> str:
    text = " ".join(str(error).split())
    return text[:160] if text else error.__class__.__name__


EXAMPLE = {
    "mcpServers": {
        "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
        "github": {
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
        },
    }
}

"""Minimal MCP client over stdio (JSON-RPC 2.0, one message per line) - enough to call ha-mcp like Claude does.

ha-mcp runs as its own program (``ha-mcp`` from its own environment), exactly as in production; the test
process does not import it, so its dependencies cannot clash with Home Assistant's test environment.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

PROTOCOL_VERSION = "2025-06-18"


class McpError(Exception):
    """The server answered with a JSON-RPC error."""


class StdioMcpClient:
    """Start an MCP server process and call its tools."""

    def __init__(self, command: list[str], env: dict[str, str]) -> None:
        """Remember how to start the server."""
        self._command = command
        self._env = {**os.environ, **env}
        self._process: asyncio.subprocess.Process | None = None
        self._id = 0

    async def __aenter__(self) -> StdioMcpClient:
        """Start the server and run the MCP handshake."""
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self._env,
            limit=16 * 1024 * 1024,
        )
        await self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "gmx-mail-staging", "version": "1"}},
        )
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Stop the server."""
        if self._process is not None and self._process.returncode is None:
            assert self._process.stdin is not None
            self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), 10)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()

    async def _send(self, message: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(json.dumps(message).encode() + b"\n")
        await self._process.stdin.drain()

    async def request(self, method: str, params: dict[str, Any], timeout: float = 60) -> Any:
        """Send a request and return its result (notifications and log messages in between are skipped)."""
        self._id += 1
        request_id = self._id
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        assert self._process is not None and self._process.stdout is not None
        while True:
            line = await asyncio.wait_for(self._process.stdout.readline(), timeout)
            if not line:
                raise McpError("server closed the connection")
            message = json.loads(line)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise McpError(str(message["error"]))
            return message["result"]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool; returns the structured result (or the parsed text content)."""
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            payload: dict[str, Any] = structured.get("result", structured) if set(structured) == {"result"} else structured
        else:
            text = "".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")
            payload = json.loads(text) if text.startswith("{") else {"text": text}
        payload.setdefault("_is_error", bool(result.get("isError")))
        return payload

"""Hyperion, Aman's discovery engine, takes the questions that reach past a course: research, history, engineering
practice. mnemosyne asks it the way any Helios client does, through the Helios MCP server's `hyperion_investigate`
tool on the Docker network, so Hyperion's own store stays Hyperion's. The brief lands in Hyperion (and the vault's
Sources/Hyperion); the question keeps the investigation id.
"""

import json
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent


class Hyperion(Protocol):
    async def investigate(self, subject: str) -> dict[str, Any]: ...

    async def investigation(self, iid: str) -> dict[str, Any]: ...


class HyperionClient:
    def __init__(self, url: str, timeout: float = 60) -> None:
        self.url, self.timeout = url, timeout

    async def investigate(self, subject: str) -> dict[str, Any]:
        return await self.call("hyperion_investigate", {"subject": subject[:300], "kind": "topic"})

    async def investigation(self, iid: str) -> dict[str, Any]:
        """One investigation: its status and, once done, the brief."""
        return await self.call("hyperion_investigation", {"id_or_subject": iid})

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        async with (
            httpx.AsyncClient(timeout=self.timeout) as http,
            streamable_http_client(self.url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool, args)
        text = " ".join(c.text for c in result.content if isinstance(c, TextContent))
        if result.isError:
            raise RuntimeError(f"{tool}: {text[:300]}")
        if result.structuredContent is not None:
            return dict(result.structuredContent)
        loaded: Any = json.loads(text)
        return dict(loaded) if isinstance(loaded, dict) else {"raw": text[:300]}  # pyright: ignore[reportUnknownArgumentType]

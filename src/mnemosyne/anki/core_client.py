"""The only module that talks to anki-core, which owns the server's copy of the collection (invariant 1).

anki-core runs with destructive, schema, and full-sync tools disabled (invariants 2 and 3). Since P4 it
grants mnemosyne the admin scope, for filtered decks and today-only limits. So this client calls only the
tools in ALLOWED, and anything else (reposition, preset edits, FSRS changes, sync login) fails here
before it reaches the collection. Note writes stay upserts keyed on MnemosyneID (invariant 4).
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

KEY_FIELD = "MnemosyneID"
ALLOWED = frozenset({
    # read
    "anki_status", "anki_notes_search", "anki_notes_get", "anki_cards_search", "anki_cards_get",
    "anki_decks_list", "anki_decks_get", "anki_deck_options_get", "anki_deck_presets_list", "anki_review_stats",
    "anki_reviews_list",
    # write
    "anki_notes_upsert", "anki_notes_update_fields", "anki_notes_add_tags", "anki_notes_remove_tags",
    "anki_cards_suspend", "anki_cards_unsuspend",
    "anki_media_store",                  # mn_-prefixed audio the pipeline owns (IPA recordings, later TTS)
    # admin, P4 only: exam and walk decks, and the load governor's today-only limits (invariant 9 carve-out)
    "anki_filtered_decks_upsert", "anki_filtered_decks_rebuild", "anki_filtered_decks_remove",
    "anki_deck_limits_update",
})


class AnkiCoreError(RuntimeError):
    pass


def unwrap(exc: BaseException) -> BaseException:
    """The error itself, out of the MCP client's task groups. An error raised inside a session comes out as
    "unhandled errors in a TaskGroup (1 sub-exception)", which hides anki-core's code (DUPLICATE_NOTE, ...) from
    the writer that needs it to isolate one bad note (2026-10-20: one duplicate held back every approved card)."""
    found: BaseException = exc
    while isinstance(found, BaseExceptionGroup):
        group: BaseExceptionGroup[BaseException] = found  # pyright: ignore[reportUnknownVariableType]
        if not group.exceptions:
            break
        found = group.exceptions[0]
    return cast(BaseException, found)


def describe(exc: BaseException) -> str:
    return str(unwrap(exc))


class Caller(Protocol):
    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def allowed(tool: str, arguments: dict[str, Any]) -> None:
    if tool not in ALLOWED:
        raise AnkiCoreError(f"{tool} is not on mnemosyne's anki-core allowlist")
    if tool == "anki_deck_limits_update" and arguments.get("update", {}).get("scope") != "today":
        raise AnkiCoreError("mnemosyne only sets today-only limits; presets change on Desktop")


async def invoke(session: ClientSession, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    allowed(tool, arguments)
    result = await session.call_tool(tool, arguments)
    text = " ".join(c.text for c in result.content if isinstance(c, TextContent))
    if result.isError:
        raise AnkiCoreError(f"{tool}: {text}")
    if result.structuredContent is not None:
        return dict(result.structuredContent)
    loaded: Any = json.loads(text)
    if not isinstance(loaded, dict):
        raise AnkiCoreError(f"{tool}: expected an object, got {text[:200]}")
    return dict(loaded)  # pyright: ignore[reportUnknownArgumentType]


class _Session:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await invoke(self._session, tool, arguments)


@asynccontextmanager
async def reads(core: "Core") -> AsyncGenerator[Caller]:
    """Many calls over one MCP session when the client supports it (the real one does; test fakes don't)."""
    opener = getattr(core, "session", None)
    if opener is None:
        yield core
        return
    async with opener() as s:
        yield s


class Core(Protocol):
    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    async def upsert(self, notes: list[dict[str, Any]], idempotency_key: str) -> dict[str, Any]: ...

    async def status(self) -> dict[str, Any]: ...


class CoreClient:
    def __init__(self, url: str, token: str, timeout: float = 120) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Caller]:
        """One MCP session for a run of calls (a learner profile makes dozens of reads). Errors come out as
        themselves, not wrapped in the client's task groups (unwrap)."""
        try:
            async with (
                httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as http,
                streamable_http_client(self._url, http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                yield _Session(session)
        except BaseExceptionGroup as group:
            inner = unwrap(group)
            if isinstance(inner, Exception):
                raise inner from group
            raise

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed(tool, arguments)
        async with self.session() as s:
            return await s.call(tool, arguments)

    async def upsert(self, notes: list[dict[str, Any]], idempotency_key: str) -> dict[str, Any]:
        """One atomic, idempotent batch (at most 50 notes). Returns anki-core's mutation receipt."""
        return await self.call("anki_notes_upsert",
                               {"key_field": KEY_FIELD, "notes": notes, "idempotency_key": idempotency_key})

    async def status(self) -> dict[str, Any]:
        return await self.call("anki_status", {})

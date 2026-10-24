"""Walk decks (PLAN §5.4): what the phone plays on a walk, rebuilt at 04:15 after the exam decks.

A card sits in at most one filtered deck, and a rebuild only takes cards that aren't in one, so exam decks
keep their cards. ~Walk::New goes first: its rebuild sends yesterday's half-learned cards home, where
~Walk::Due then picks up the ones that are due.
"""

from datetime import date
from typing import Any

import structlog

from mnemosyne.anki.core_client import Core

log = structlog.get_logger()

DUE = "~Walk::Due"
NEW = "~Walk::New"
DUE_SEARCH = "tag:mode::audio is:due -is:suspended"
NEW_SEARCH = "tag:mode::audio is:new -is:suspended (note:M-Lang OR note:M-IPA OR deck:Core)"
DUE_LIMIT = 400
# Decks whose audio new cards come in on walks rather than at the desk (the ones NEW_SEARCH can reach).
WALK_HOMES = ("Core", "Lang::Spanish", "Lang::Hindi", "Lang::French")


def walk_new_search(deck: str) -> str:
    """The new cards in one deck that ~Walk::New could take."""
    return f'"deck:{deck}" {NEW_SEARCH}'


async def sync(core: Core, today: date, walk_new: int) -> dict[str, Any]:
    """Rebuild ~Walk::New with today's governed count (removed on a day with none), then ~Walk::Due."""
    day = today.isoformat()
    out: dict[str, Any] = {}
    if walk_new > 0:
        receipt = await core.call("anki_filtered_decks_upsert", {
            "name": NEW, "search": NEW_SEARCH, "limit": walk_new, "order": "due", "reschedule": True,
            "idempotency_key": f"mn-walk-new-{day}-{walk_new}"})
        out[NEW] = int((receipt.get("result") or receipt).get("cards", 0))
    else:
        receipt = await core.call("anki_filtered_decks_remove", {"name": NEW, "idempotency_key": f"mn-walk-new-{day}-0"})
        out[NEW] = 0
    receipt = await core.call("anki_filtered_decks_upsert", {
        "name": DUE, "search": DUE_SEARCH, "limit": DUE_LIMIT, "order": "retrievability_ascending",
        "reschedule": True, "idempotency_key": f"mn-walk-due-{day}"})
    out[DUE] = int((receipt.get("result") or receipt).get("cards", 0))
    log.info("walk_decks_synced", **out)
    return out

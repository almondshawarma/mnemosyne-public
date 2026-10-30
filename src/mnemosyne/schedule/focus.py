"""Focus decks (Aman, 2026-10-05): a subject he wants to practice actively, not only as it comes due on walks.

Each focus in config/budget.yaml (`focus:`) gets two filtered decks under `~Focus::<Name>`, the same shape as the
walk decks:
- `::New` takes a few new cards of the subject each day. They come out of the governor's daily new-card total, so
  the load budget still holds. `new_per_day: all` (Fresh, Aman 2026-10-20) takes every matching new card instead,
  for him to burn through when he has time; those don't count against the total (filtered decks have no daily
  limit, so counting them would starve every other deck), and the governor sees the reviews they add the next day.
- `::Due` takes the subject's cards that are due or fading below a recall threshold, weakest first.

They're rebuilt at 04:15 after the exam decks (a card sits in at most one filtered deck, and exams come first) and
before the walk decks, so the subject's due cards gather here instead of on walks. A focus taken out of the config has
its decks removed at the next 04:15, which sends their cards home with their history. This is the third kind of
filtered deck in CLAUDE.md invariant 9's standing exception.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from mnemosyne.anki.core_client import Caller, Core
from mnemosyne.schedule import exam_decks

log = structlog.get_logger()
PREFIX = "~Focus::"
UNCAPPED = 9999          # a filtered deck's limit for `new_per_day: all`: every matching card


@dataclass(frozen=True)
class Focus:
    name: str                  # "Plasma" → ~Focus::Plasma::New and ::Due
    search: str                # which cards belong to the subject
    new_per_day: int | None = 5    # None: all of them (`new_per_day: all`), outside the governor's total
    recall_below: float = 0.9  # seen cards come in once they're due, or their recall drops below this
    due_limit: int = 60
    starts: date | None = None     # no decks before this day (Circuits waits out an exam week)

    @property
    def new_deck(self) -> str:
        return f"{PREFIX}{self.name}::New"

    @property
    def due_deck(self) -> str:
        return f"{PREFIX}{self.name}::Due"

    @property
    def new_search(self) -> str:
        return f"({self.search}) is:new -is:suspended"

    @property
    def due_search(self) -> str:
        return f"({self.search}) -is:new -is:suspended (is:due OR prop:r<{self.recall_below:.2f})"


def quota(raw: Any) -> int | None:
    return None if raw is None or str(raw).strip().lower() == "all" else int(raw)


def from_budget(budget: dict[str, Any]) -> list[Focus]:
    raw: list[dict[str, Any]] = budget.get("focus") or []
    return [Focus(str(f["name"]), str(f["search"]), quota(f.get("new_per_day", 5)), float(f.get("recall_below", 0.9)),
                  int(f.get("due_limit", 60)), starts(f.get("starts"))) for f in raw]


def starts(raw: Any) -> date | None:
    """YAML reads an unquoted 2026-10-31 as a date already; a quoted one is a string."""
    return None if raw is None else raw if isinstance(raw, date) else date.fromisoformat(str(raw))


def live(focuses: list[Focus], today: date) -> list[Focus]:
    """The focuses that have started: one that hasn't yet builds no decks and takes no new cards from the day."""
    return [f for f in focuses if f.starts is None or f.starts <= today]


def reserved(focuses: list[Focus]) -> int:
    """New cards a day the capped focuses take out of the governor's total."""
    return sum(f.new_per_day for f in focuses if f.new_per_day is not None)


async def existing(core: Caller) -> set[str]:
    return {n for n in await exam_decks.all_decks(core) if n.startswith(PREFIX) and n.count("::") == 2}


def cards(receipt: dict[str, Any]) -> int:
    return int((receipt.get("result") or receipt).get("cards", 0))


async def build(core: Core, name: str, search: str, limit: int, order: str, key: str) -> int:
    """Build one filtered deck, or remove it when nothing matches: Anki refuses to build an empty one (a new focus
    has nothing due yet). Cards already in this deck still count, since a rebuild takes them back in."""
    matching = await core.call("anki_cards_search", {
        "query": f'({search}) (-deck:filtered OR "deck:{name}")', "offset": 0, "limit": 1})
    if int(matching.get("total", 0)) == 0:
        await core.call("anki_filtered_decks_remove", {"name": name, "idempotency_key": f"{key}-empty"})
        return 0
    return cards(await core.call("anki_filtered_decks_upsert", {
        "name": name, "search": search, "limit": limit, "order": order, "reschedule": True, "idempotency_key": key}))


async def sync(core: Core, today: date, focuses: list[Focus], new_room: int) -> dict[str, Any]:
    """Build each focus's decks. `new_room` is how many new cards the day can take in all; the focuses share it in
    config order. Returns the decks built and removed, `new`: the new cards the capped focuses took out of that
    total, and `uncapped`: the new cards `new_per_day: all` focuses gathered on top of it."""
    day = today.isoformat()
    built: dict[str, int] = {}
    room = max(new_room, 0)
    taken = 0
    extra = 0
    for f in focuses:
        if f.new_per_day is None:
            built[f.new_deck] = await build(core, f.new_deck, f.new_search, UNCAPPED, "due", f"mn-focus-new-{f.name}-{day}-all")
            extra += built[f.new_deck]
            built[f.due_deck] = await build(core, f.due_deck, f.due_search, f.due_limit, "retrievability_ascending",
                                            f"mn-focus-due-{f.name}-{day}")
            continue
        n = min(f.new_per_day, room - taken)
        if n > 0:
            built[f.new_deck] = await build(core, f.new_deck, f.new_search, n, "due", f"mn-focus-new-{f.name}-{day}-{n}")
            taken += built[f.new_deck]
        else:
            await core.call("anki_filtered_decks_remove", {"name": f.new_deck,
                                                           "idempotency_key": f"mn-focus-new-{f.name}-{day}-0"})
            built[f.new_deck] = 0
        built[f.due_deck] = await build(core, f.due_deck, f.due_search, f.due_limit, "retrievability_ascending",
                                        f"mn-focus-due-{f.name}-{day}")
    wanted = {d for f in focuses for d in (f.new_deck, f.due_deck)}
    removed: dict[str, int] = {}
    for name in sorted(await existing(core) - wanted):
        receipt = await core.call("anki_filtered_decks_remove", {"name": name, "idempotency_key": f"mn-focus-rm-{name}-{day}"})
        removed[name] = int((receipt.get("result") or receipt).get("returned_cards", 0))
    out: dict[str, Any] = {"built": built, "removed": removed, "new": taken, "uncapped": extra}
    log.info("focus_decks_synced", **out)
    return out

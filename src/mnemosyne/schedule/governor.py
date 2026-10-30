"""Load governor (PLAN §5.3): how many new cards today, and from which decks, so the day fits the budget.

Each morning at 04:15, after the exam decks:
1. **Measure** seconds per review over the last 14 Anki days, and record each day's study minutes.
2. **Project** the next 7 days' review load from what is already scheduled. Overdue cards count on day 0.
3. **Size** new cards: whatever time the projected reviews leave in the budget, divided by what one
   sustained new card a day costs (`governor.new_card_reviews` answers a day).
4. **Focus decks** (`budget.yaml` `focus:`, schedule/focus.py) take their new cards first, out of that total.
5. **Allocate** the rest by `priority_weights`, only to decks that have new cards to give. A deck's share goes first
   to ~Walk::New in proportion to its audio cards, and the rest becomes its **today-only** new limit.

Presets keep new/day 0 and are never touched. A today-only limit lapses by itself the next day, so a missed
morning means no new cards, never a runaway (CLAUDE.md invariant 9, standing exception).
"""

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

import structlog

from mnemosyne.anki.core_client import AnkiCoreError, Core
from mnemosyne.ledger.db import now
from mnemosyne.schedule import exam_decks, walk
from mnemosyne.schedule import focus as focus_decks

log = structlog.get_logger()

MEASURE_DAYS = 14
HORIZON = 7
MIN_TIMED_REVIEWS = 50


@dataclass(frozen=True)
class Budget:
    minutes: float                                  # walk + desk
    weights: dict[str, float]
    new_card_reviews: float = 5.0
    max_new: int = 25
    default_seconds: float = 8.0
    backlog_until: date | None = None
    backlog_zero: frozenset[str] = frozenset()
    backlog_walk_new: int = 0
    focus: tuple[focus_decks.Focus, ...] = ()

    @classmethod
    def from_yaml(cls, b: dict[str, Any]) -> "Budget":
        g: dict[str, Any] = b.get("governor") or {}
        bc: dict[str, Any] = b.get("backlog_clear") or {}
        until = bc.get("until")
        weights: dict[str, Any] = b.get("priority_weights") or {}
        zero: list[Any] = bc.get("zero_new_in") or []
        return cls(
            minutes=float(b.get("walk_minutes", 0)) + float(b.get("desk_minutes", 0)),
            weights={str(k): float(v) for k, v in weights.items()},
            new_card_reviews=float(g.get("new_card_reviews", 5)),
            max_new=int(g.get("max_new_per_day", 25)),
            default_seconds=float(g.get("seconds_per_review", 8)),
            backlog_until=date.fromisoformat(str(until)) if until else None,
            backlog_zero=frozenset(str(d) for d in zero),
            backlog_walk_new=int(bc.get("walk_new", 0)),
            focus=tuple(focus_decks.from_budget(b)),
        )

    def in_backlog(self, today: date) -> bool:
        return self.backlog_until is not None and today <= self.backlog_until


@dataclass(frozen=True)
class Measure:
    seconds_per_review: float
    projected_reviews: float
    days: dict[str, tuple[int, float]] = field(default_factory=dict[str, tuple[int, float]])  # date → (reviews, minutes)


@dataclass
class Plan:
    total: int
    allocation: dict[str, int]
    walk: dict[str, int]            # the part of each deck's share that comes in through ~Walk::New
    home: dict[str, int]            # today-only new limit set on the deck itself
    walk_new: int
    backlog: bool


def seconds_per_review(daily: list[dict[str, Any]], default: float) -> float:
    """Mean answer time over the last 14 complete days (offsets −14…−1). Too few reviews → the default."""
    days = [d for d in daily if -MEASURE_DAYS <= int(d["day"]) <= -1]
    reviews = sum(int(d["reviews"]) for d in days)
    seconds = sum(float(d["answer_seconds"]) for d in days)
    return round(seconds / reviews, 2) if reviews >= MIN_TIMED_REVIEWS else default


def day_minutes(daily: list[dict[str, Any]], today: date) -> dict[str, tuple[int, float]]:
    """Complete days only (today is still going), keyed by their date."""
    return {(today + timedelta(days=int(d["day"]))).isoformat(): (int(d["reviews"]), round(float(d["answer_seconds"]) / 60, 1))
            for d in daily if -MEASURE_DAYS <= int(d["day"]) <= -1}


def projected(future_due: dict[str, Any]) -> float:
    """Mean reviews per day over the next 7 days; everything overdue lands on day 0."""
    total = sum(int(n) for day, n in future_due.items() if int(day) < HORIZON)
    return round(total / HORIZON, 1)


def new_total(b: Budget, m: Measure) -> int:
    spare = b.minutes * 60 - m.projected_reviews * m.seconds_per_review
    per_card = b.new_card_reviews * m.seconds_per_review
    return max(0, min(b.max_new, int(spare // per_card)))


def allocate(total: int, weights: dict[str, float], available: dict[str, int]) -> dict[str, int]:
    """Split `total` by weight among decks with new cards; a deck that runs out passes the rest on."""
    alloc = dict.fromkeys(weights, 0)
    open_ = {d for d, w in weights.items() if w > 0 and available.get(d, 0) > 0}
    left = total
    while left > 0 and open_:
        wsum = sum(weights[d] for d in open_)
        share = {d: left * weights[d] / wsum for d in open_}
        base = {d: int(share[d]) for d in open_}
        for d in sorted(open_, key=lambda d: (base[d] - share[d], d))[:left - sum(base.values())]:
            base[d] += 1                                            # largest remainder
        given = 0
        for d in sorted(open_):
            g = min(base[d], available[d] - alloc[d])
            alloc[d] += g
            given += g
            if alloc[d] >= available[d]:
                open_.discard(d)
        left -= given
        if given == 0:
            break
    return alloc


def split(n: int, audio: int, available: int) -> int:
    """How much of a deck's share comes in on walks: in proportion to its audio cards, so desk-only cards
    (graphs, long derivations) aren't starved while a long audio run like the multiplication facts lasts."""
    if not available or not n:
        return 0
    return max(n - (available - audio), min(audio, round(n * audio / available)))


def plan(b: Budget, m: Measure, available: dict[str, int], audio: dict[str, int], today: date,
         reserved: int = 0) -> Plan:
    """`available`: new cards per deck outside exam and focus decks. `audio`: how many of those ~Walk::New can
    take. `reserved`: new cards the focus decks already took today, out of the same total."""
    backlog = b.in_backlog(today)
    weights = {d: (0.0 if backlog and d in b.backlog_zero else w) for d, w in b.weights.items()}
    total = max(0, new_total(b, m) - reserved)
    alloc = allocate(total, weights, available)
    walk_part = {d: split(n, audio.get(d, 0), available.get(d, 0)) for d, n in alloc.items() if d in walk.WALK_HOMES}
    home = {d: n - walk_part.get(d, 0) for d, n in alloc.items()}
    walk_new = sum(walk_part.values())
    if backlog:                                   # PLAN §5.5: the multiplication facts, 10 a day on walks
        walk_new += min(b.backlog_walk_new, sum(audio.get(d, 0) - walk_part.get(d, 0) for d in walk.WALK_HOMES))
    return Plan(total, alloc, {d: n for d, n in walk_part.items() if n}, {d: n for d, n in home.items() if n},
                walk_new, backlog)


async def count(core: Core, query: str) -> int:
    page = await core.call("anki_cards_search", {"query": query, "offset": 0, "limit": 1})
    return int(page.get("total", 0))


def dig(obj: Any, *keys: str) -> Any:
    """obj[k1][k2]… through anki-core's nested stats, or None where a level is missing."""
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return obj  # pyright: ignore[reportUnknownVariableType]


async def measure(core: Core, b: Budget, today: date) -> Measure:
    """From anki-core's review stats. If the review log is too big to read, fall back and say so."""
    daily: list[dict[str, Any]] = []
    try:
        done = await core.call("anki_review_stats", {"query": f"rated:{MEASURE_DAYS + 1}", "days": 30})
        daily = dig(done, "summary", "daily") or []
    except AnkiCoreError as e:
        log.warning("governor_measure_failed", error=str(e)[:300])
    ahead = await core.call("anki_review_stats", {"query": f"(is:due OR prop:due<{HORIZON}) -is:suspended",
                                                  "days": 30, "include_sections": ["future_due"]})
    fd: dict[str, Any] = dig(ahead, "sections", "future_due", "future_due") or {}
    return Measure(seconds_per_review(daily, b.default_seconds), projected(fd), day_minutes(daily, today))


async def availability(core: Core, decks: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    available: dict[str, int] = {}
    audio: dict[str, int] = {}
    for d in decks:
        available[d] = await count(core, f'"deck:{d}" is:new -is:suspended -"deck:~Exam*" -"deck:~Focus*"')
        if d in walk.WALK_HOMES:
            audio[d] = await count(core, walk.walk_new_search(d))
    return available, audio


def record(con: sqlite3.Connection, today: date, m: Measure, p: Plan, applied: dict[str, Any]) -> None:
    stamp = now()
    con.execute("BEGIN IMMEDIATE")
    for day, (reviews, minutes) in m.days.items():
        con.execute("INSERT INTO load_day (day, reviews, minutes, measured_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(day) DO UPDATE SET reviews = excluded.reviews, minutes = excluded.minutes, "
                    "measured_at = excluded.measured_at", (day, reviews, minutes, stamp))
    con.execute("INSERT OR REPLACE INTO governor_run VALUES (?, ?, ?, ?, ?, ?, ?)",
                (today.isoformat(), m.seconds_per_review, m.projected_reviews, p.total,
                 json.dumps(asdict(p)), json.dumps(applied), stamp))
    con.execute("COMMIT")


async def run(con: sqlite3.Connection, core: Core, b: Budget, today: date, apply: bool = True) -> dict[str, Any]:
    """Measure, plan, rebuild the walk decks, then set today's new limits. Same day, same writes: a no-op.
    With apply=False it only reads: the plan comes back and nothing is written, to Anki or the Ledger."""
    decks = await exam_decks.all_decks(core)
    governed = [d for d in b.weights if d in decks]
    m = await measure(core, b, today)
    if not apply:
        reserved = min(new_total(b, m), focus_decks.reserved(focus_decks.live(list(b.focus), today)))
        available, audio = await availability(core, governed)
        p = plan(b, m, available, audio, today, reserved)
        return {"measure": asdict(m), "available": available, "audio": audio, "plan": asdict(p),
                "focus_new": reserved, "applied": None}
    # Focus decks first: their new cards come out of today's total, and their due cards stay off walks
    focused = (await focus_decks.sync(core, today, focus_decks.live(list(b.focus), today), new_total(b, m))
               if b.focus else None)
    available, audio = await availability(core, governed)
    p = plan(b, m, available, audio, today, int(focused["new"]) if focused else 0)
    applied: dict[str, Any] = {"focus": focused, "walk": await walk.sync(core, today, p.walk_new), "limits": {}}
    for d, n in sorted(p.home.items()):
        await core.call("anki_deck_limits_update", {
            "deck_id": decks[d], "update": {"scope": "today", "values": {"new_cards_per_day": n}},
            "idempotency_key": f"mn-gov-{d}-{today.isoformat()}-{n}"})
        applied["limits"][d] = n
    record(con, today, m, p, applied)
    out: dict[str, Any] = {"measure": {"seconds_per_review": m.seconds_per_review,
                                       "projected_reviews": m.projected_reviews},
                           "plan": asdict(p), "applied": applied}
    log.info("governor_ran", **out)
    return out


def history(con: sqlite3.Connection, b: Budget, days: int = 14) -> dict[str, Any]:
    """The acceptance check: each measured day against the budget, ±15%."""
    rows = [dict(r) for r in con.execute("SELECT day, reviews, minutes FROM load_day ORDER BY day DESC LIMIT ?", (days,))]
    for r in rows:
        r["within_15pct"] = abs(r["minutes"] - b.minutes) <= 0.15 * b.minutes
    runs: list[dict[str, Any]] = []
    for row in con.execute("SELECT * FROM governor_run ORDER BY day DESC LIMIT ?", (days,)):
        r = dict(row)
        runs.append(r | {"plan": json.loads(r.pop("plan_json")), "applied": json.loads(r.pop("applied_json"))})
    return {"budget_minutes": b.minutes, "days": rows, "runs": runs}

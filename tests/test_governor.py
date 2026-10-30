"""P4 load governor and walk decks: sizing new cards from the budget, splitting them, and what gets written."""
import asyncio
from datetime import date
from typing import Any

import pytest

from mnemosyne.anki.core_client import AnkiCoreError
from mnemosyne.schedule import governor as gv
from mnemosyne.schedule import walk

WEIGHTS = {"Lang::Spanish": 0.45, "Now": 0.30, "Core": 0.15, "Lang::Hindi": 0.05, "Lang::French": 0.05}
YAML: dict[str, Any] = {
    "walk_minutes": 40, "desk_minutes": 15, "priority_weights": WEIGHTS,
    "backlog_clear": {"until": date(2026, 10, 4), "zero_new_in": ["Core", "Now", "Lang::Hindi", "Lang::French"],
                      "walk_new": 10},
    "governor": {"new_card_reviews": 5, "max_new_per_day": 25, "seconds_per_review": 8}}
B = gv.Budget.from_yaml(YAML)
AFTER = date(2026, 10, 5)
DURING = date(2026, 9, 27)


def test_budget_reads_the_yaml() -> None:
    assert B.minutes == 55 and B.max_new == 25 and B.backlog_walk_new == 10
    assert B.in_backlog(date(2026, 10, 4)) and not B.in_backlog(AFTER)


def test_seconds_per_review_uses_complete_days_and_falls_back() -> None:
    daily = [{"day": -1, "reviews": 40, "answer_seconds": 400.0}, {"day": -3, "reviews": 20, "answer_seconds": 140.0},
             {"day": 0, "reviews": 500, "answer_seconds": 99.0}, {"day": -20, "reviews": 500, "answer_seconds": 99.0}]
    assert gv.seconds_per_review(daily, 8.0) == 9.0, "today and days before the window don't count"
    assert gv.seconds_per_review(daily[:1], 8.0) == 8.0, "under 50 timed reviews: the default"
    assert gv.day_minutes(daily, date(2026, 10, 5)) == {"2026-10-04": (40, 6.7), "2026-10-02": (20, 2.3)}


def test_projection_puts_overdue_on_day_zero() -> None:
    assert gv.projected({"-30": 70, "0": 50, "3": 21, "6": 5, "7": 900}) == 20.9


def test_new_total_is_what_the_budget_leaves() -> None:
    assert gv.new_total(B, gv.Measure(10.0, 200.0)) == 25, "3300 s − 2000 s leaves 26 cards; capped at 25"
    assert gv.new_total(B, gv.Measure(10.0, 300.0)) == 6
    assert gv.new_total(B, gv.Measure(10.0, 400.0)) == 0, "over budget: nothing new, never negative"


def test_allocate_passes_on_what_a_deck_cannot_use() -> None:
    assert gv.allocate(20, WEIGHTS, {"Now": 100, "Core": 100}) == {
        "Lang::Spanish": 0, "Now": 13, "Core": 7, "Lang::Hindi": 0, "Lang::French": 0}, "no Spanish yet: 0.30:0.15"
    assert gv.allocate(20, WEIGHTS, {"Now": 3, "Core": 100, "Lang::Hindi": 100})["Core"] == 13
    assert sum(gv.allocate(7, WEIGHTS, dict.fromkeys(WEIGHTS, 50)).values()) == 7
    assert gv.allocate(9, WEIGHTS, {}) == dict.fromkeys(WEIGHTS, 0)


def test_plan_after_the_backlog_splits_walk_and_desk() -> None:
    p = gv.plan(B, gv.Measure(10.0, 250.0), {"Now": 40, "Core": 300}, {"Core": 200}, AFTER)
    assert p.total == 16 and p.allocation["Now"] == 11 and p.allocation["Core"] == 5
    assert p.walk == {"Core": 3} and p.walk_new == 3, "Core's audio cards come in on walks"
    assert p.home == {"Now": 11, "Core": 2}, "the rest become today-only limits"


def test_walk_split_is_proportional_and_never_starves_desk_cards() -> None:
    assert gv.split(25, 442, 647) == 17, "the multiplication facts don't take all of Core's share"
    assert gv.split(10, 0, 50) == 0 and gv.split(10, 50, 50) == 10
    assert gv.split(10, 48, 50) == 10, "only 2 desk cards: the other 8 go to walks"
    assert gv.split(5, 4, 300) == 0 and gv.split(0, 4, 4) == 0


def test_plan_during_the_backlog_is_the_multiplication_facts_only() -> None:
    p = gv.plan(B, gv.Measure(10.0, 100.0), {"Now": 40, "Core": 300, "Lang::Hindi": 50}, {"Core": 250}, DURING)
    assert p.backlog and p.home == {} and p.walk_new == 10
    assert gv.plan(B, gv.Measure(10.0, 100.0), {"Core": 3}, {"Core": 3}, DURING).walk_new == 3


class GovCore:
    def __init__(self, stats_fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stats_fail = stats_fail

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, arguments))
        if tool == "anki_decks_list":
            names = ["Core", "Now", "Lang::Hindi", "~Walk::Due", "~Exam::PHYS207-Exam-I"]
            return {"items": [{"name": n, "id": 100 + i} for i, n in enumerate(names)], "has_more": False}
        if tool == "anki_review_stats":
            if "include_sections" in arguments:
                return {"sections": {"future_due": {"future_due": {"-5": 700, "0": 50, "2": 20}}}}
            if self.stats_fail:
                raise AnkiCoreError("anki_review_stats: review history exceeds MCP_MAX_SEARCH_SCAN")
            return {"summary": {"daily": [{"day": -1, "reviews": 300, "answer_seconds": 2400.0},
                                          {"day": -2, "reviews": 100, "answer_seconds": 800.0}]}}
        if tool == "anki_cards_search":
            q = arguments["query"]
            total = 0 if "Hindi" in q else 250 if "tag:mode::audio" in q else 400 if "Core" in q else 30
            return {"items": [], "total": total}
        if tool in ("anki_filtered_decks_upsert", "anki_filtered_decks_remove"):
            return {"state": "committed", "result": {"cards": arguments.get("limit", 0), "removed": True}}
        if tool == "anki_deck_limits_update":
            return {"state": "committed", "result": {"updated": True}}
        raise AssertionError(tool)

    async def upsert(self, notes: list[dict[str, Any]], idempotency_key: str) -> dict[str, Any]:
        raise NotImplementedError

    async def status(self) -> dict[str, Any]:
        return {}


@pytest.fixture
def con(tmp_path: Any) -> Any:
    from mnemosyne.ledger import db
    c = db.connect(tmp_path / "h.db")
    db.migrate(c)
    return c


def test_run_during_backlog_rebuilds_walk_decks_and_sets_no_limits(con: Any) -> None:
    core = GovCore()
    out = asyncio.run(gv.run(con, core, B, DURING))
    tools = [t for t, _ in core.calls]
    assert "anki_deck_limits_update" not in tools, "backlog: presets' 0 new/day stands"
    upserts = [a for t, a in core.calls if t == "anki_filtered_decks_upsert"]
    assert [a["name"] for a in upserts] == [walk.NEW, walk.DUE], "new first, so half-learned cards go home"
    assert upserts[0]["limit"] == 10 and upserts[0]["order"] == "due"
    assert upserts[1]["search"] == walk.DUE_SEARCH and upserts[1]["order"] == "retrievability_ascending"
    assert out["measure"]["projected_reviews"] == 110.0
    days = {r["day"]: r["minutes"] for r in con.execute("SELECT * FROM load_day")}
    assert days == {"2026-09-26": 40.0, "2026-09-25": 13.3}


def test_run_after_backlog_sets_today_only_limits(con: Any) -> None:
    core = GovCore()
    out = asyncio.run(gv.run(con, core, B, AFTER))
    # 3300 s − 110 × 8 s = 2420 s → 60 cards at 40 s each, capped at 25: Now 17, Core 8 (Spanish and French
    # have no deck, Hindi no new cards). Core has 250 audio of 400: 5 on walks, 3 at the desk.
    assert out["plan"]["allocation"]["Now"] == 17 and out["plan"]["allocation"]["Core"] == 8
    limits = [(a["deck_id"], a["update"]["values"]["new_cards_per_day"], a["idempotency_key"])
              for t, a in core.calls if t == "anki_deck_limits_update"]
    assert limits == [(100, 3, "mn-gov-Core-2026-10-05-3"), (101, 17, "mn-gov-Now-2026-10-05-17")]
    assert all(a["update"]["scope"] == "today" for t, a in core.calls if t == "anki_deck_limits_update")
    walk_new = next(a for t, a in core.calls if t == "anki_filtered_decks_upsert" and a["name"] == walk.NEW)
    assert walk_new["limit"] == 5
    counts = [a["query"] for t, a in core.calls if t == "anki_cards_search"]
    assert all('-"deck:~Exam*"' in q for q in counts if "tag:mode::audio" not in q), "exam decks' cards are taken"
    hist = gv.history(con, B)
    assert hist["runs"][0]["new_total"] == 25 and hist["days"][0]["within_15pct"] is False


def test_a_day_with_no_walk_new_removes_the_deck(con: Any) -> None:
    core = GovCore()
    asyncio.run(walk.sync(core, AFTER, 0))
    assert core.calls[0][0] == "anki_filtered_decks_remove" and core.calls[0][1]["name"] == walk.NEW


def test_an_unreadable_review_log_falls_back_to_the_default(con: Any) -> None:
    out = asyncio.run(gv.run(con, GovCore(stats_fail=True), B, AFTER))
    assert out["measure"]["seconds_per_review"] == 8.0


def test_a_dry_run_writes_nothing(con: Any) -> None:
    core = GovCore()
    out = asyncio.run(gv.run(con, core, B, AFTER, apply=False))
    assert out["plan"]["total"] == 25 and out["applied"] is None
    assert {t for t, _ in core.calls} == {"anki_decks_list", "anki_review_stats", "anki_cards_search"}
    assert con.execute("SELECT COUNT(*) FROM governor_run").fetchone()[0] == 0


# ---------- focus decks ----------

FOCUSED = gv.Budget.from_yaml({**YAML, "focus": [{"name": "Plasma", "search": "tag:topic::plasma*", "new_per_day": 4,
                                                   "recall_below": 0.9, "due_limit": 60}]})


def test_a_focus_takes_its_new_cards_out_of_the_day_s_total(con: Any) -> None:
    core = GovCore()
    out = asyncio.run(gv.run(con, core, FOCUSED, AFTER))
    upserts = [a for t, a in core.calls if t == "anki_filtered_decks_upsert"]
    assert [a["name"] for a in upserts][:2] == ["~Focus::Plasma::New", "~Focus::Plasma::Due"], "before the walk decks"
    assert upserts[0]["limit"] == 4 and upserts[0]["search"] == "(tag:topic::plasma*) is:new -is:suspended"
    assert upserts[1]["search"] == "(tag:topic::plasma*) -is:new -is:suspended (is:due OR prop:r<0.90)"
    assert upserts[1]["order"] == "retrievability_ascending" and upserts[1]["limit"] == 60
    assert out["applied"]["focus"]["new"] == 4 and out["plan"]["total"] == 21, "25 a day in all, 4 of them plasma"
    counts = [a["query"] for t, a in core.calls if t == "anki_cards_search"]
    assert all('-"deck:~Focus*"' in q for q in counts if q.startswith('"deck:') and "tag:mode::audio" not in q)


def test_an_uncapped_focus_takes_every_fresh_card_outside_the_total(con: Any) -> None:
    budget = gv.Budget.from_yaml({**YAML, "focus": [
        {"name": "Fresh", "search": "added:3", "new_per_day": "all", "recall_below": 0.9, "due_limit": 60},
        {"name": "Plasma", "search": "tag:topic::plasma*", "new_per_day": 4, "recall_below": 0.9, "due_limit": 60}]})
    assert [f.new_per_day for f in budget.focus] == [None, 4]
    core = GovCore()
    out = asyncio.run(gv.run(con, core, budget, AFTER))
    upserts = {a["name"]: a for t, a in core.calls if t == "anki_filtered_decks_upsert"}
    assert upserts["~Focus::Fresh::New"]["limit"] == 9999 and upserts["~Focus::Fresh::New"]["search"] == "(added:3) is:new -is:suspended"
    assert upserts["~Focus::Plasma::New"]["limit"] == 4
    focus_out = out["applied"]["focus"]
    assert focus_out["uncapped"] == 9999 and focus_out["new"] == 4
    assert out["plan"]["total"] == 21, "only the capped focus comes out of the day's 25"
    dry = asyncio.run(gv.run(con, GovCore(), budget, AFTER, apply=False))
    assert dry["focus_new"] == 4


def test_a_focus_dropped_from_the_config_is_removed() -> None:
    from mnemosyne.schedule import focus

    class Left(GovCore):
        async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if tool == "anki_decks_list":
                self.calls.append((tool, arguments))
                names = ["Core", "~Focus", "~Focus::Plasma::New", "~Focus::Plasma::Due", "~Focus::Optics::Due"]
                return {"items": [{"name": n, "id": i} for i, n in enumerate(names)], "has_more": False}
            return await super().call(tool, arguments)

    core = Left()
    out = asyncio.run(focus.sync(core, AFTER, list(FOCUSED.focus), new_room=0))
    assert out["removed"] == {"~Focus::Optics::Due": 0} and out["new"] == 0
    assert ("anki_filtered_decks_remove", {"name": "~Focus::Plasma::New", "idempotency_key":
                                           "mn-focus-new-Plasma-2026-10-05-0"}) in core.calls, "no room: no new deck"


def test_a_focus_with_nothing_due_skips_its_due_deck() -> None:
    from mnemosyne.schedule import focus

    class AllNew(GovCore):
        async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if tool == "anki_cards_search" and "-is:new" in arguments["query"]:
                self.calls.append((tool, arguments))
                return {"items": [], "total": 0}       # Anki refuses to build an empty filtered deck
            return await super().call(tool, arguments)

    core = AllNew()
    out = asyncio.run(focus.sync(core, AFTER, list(FOCUSED.focus), new_room=10))
    assert out["built"] == {"~Focus::Plasma::New": 4, "~Focus::Plasma::Due": 0}
    upserts = [a["name"] for t, a in core.calls if t == "anki_filtered_decks_upsert"]
    assert upserts == ["~Focus::Plasma::New"]
    counted = [a["query"] for t, a in core.calls if t == "anki_cards_search"]
    assert '(-deck:filtered OR "deck:~Focus::Plasma::Due")' in counted[-1], "its own cards still count on a rebuild"


def test_the_real_budget_s_focus_decks() -> None:
    """Fresh first (a recent card lands there before another focus takes it); Metis searches only its own course
    tag, since MATH 309's least-squares cards carry topic::computing as a connection."""
    from pathlib import Path

    import yaml

    from mnemosyne.schedule import focus

    real = yaml.safe_load((Path(__file__).parent.parent / "config" / "budget.yaml").read_text(encoding="utf-8"))
    budget = gv.Budget.from_yaml(real)
    assert [f.name for f in budget.focus] == ["Fresh", "Circuits", "Plasma", "Metis"]
    metis = budget.focus[3]
    assert metis.search == "tag:course::metis*" and metis.new_deck == "~Focus::Metis::New"
    assert focus.reserved(list(budget.focus)) == 23, "Circuits' 15, Plasma's 5, and Metis's 3 come out of the total"
    assert budget.focus[1].starts == date(2026, 10, 31)


def test_a_focus_builds_nothing_before_it_starts(con: Any) -> None:
    from mnemosyne.schedule import focus
    budget = gv.Budget.from_yaml({**YAML, "focus": [
        {"name": "Circuits", "search": "tag:course::phys207::l07*", "new_per_day": 4, "starts": "2026-10-31"}]})
    assert focus.live(list(budget.focus), date(2026, 10, 30)) == []
    assert [f.name for f in focus.live(list(budget.focus), date(2026, 10, 31))] == ["Circuits"]
    core = GovCore()
    out = asyncio.run(gv.run(con, core, budget, AFTER))
    assert not any(a["name"].startswith("~Focus::Circuits") for t, a in core.calls if t == "anki_filtered_decks_upsert")
    assert out["applied"]["focus"]["new"] == 0 and out["plan"]["total"] == 25, "it takes none of the day's 25 yet"

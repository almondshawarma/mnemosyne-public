"""P4 exam decks: which decks exist on a given day, their searches, and self-removal after the exam."""
import asyncio
import json
from datetime import date
from typing import Any

import pytest

from mnemosyne.anki.core_client import AnkiCoreError, CoreClient
from mnemosyne.schedule import exam_decks as ed

TZ = "America/Chicago"
W = ed.Window(10, 0.85, 0.95)


def exam(course: str, title: str, starts: str, lectures: list[int] | None, confirmed: int = 1,
         kind: str = "exam") -> dict[str, Any]:
    return {"course": course, "title": title, "starts_at": starts, "kind": kind, "confirmed": confirmed,
            "lectures": None if lectures is None else json.dumps(lectures)}


EXAMS = [exam("PHYS207", "Exam I", "2026-09-30T10:20:00-05:00", [2, 3, 4, 5, 6]),
         exam("NUEN201", "Exam I", "2026-10-06T15:55:00-05:00", [1, 2]),
         exam("PHYS207", "Exam II", "2026-10-21T10:20:00-05:00", []),
         exam("MEEN221", "Exam II", "2026-10-30T15:00:00-05:00", None, confirmed=0),
         exam("PHYS207", "Chapter 24 Concept Quiz", "2026-09-27", None, kind="quiz")]


def test_names_match_the_decks_built_by_hand() -> None:
    assert ed.deck_name("PHYS207", "Exam I") == "~Exam::PHYS207-Exam-I"


def test_threshold_ramps_from_start_to_end() -> None:
    assert [ed.threshold(d, W) for d in (10, 9, 5, 1, 0)] == [0.85, 0.86, 0.91, 0.95, 0.95]


def test_exam_day_is_local() -> None:
    assert ed.exam_day("2026-10-01T02:00:00+00:00", TZ) == date(2026, 9, 30), "late evening in Texas"
    assert ed.exam_day("2026-10-02", TZ) == date(2026, 10, 2)


def test_search_takes_new_cards_and_weak_ones() -> None:
    s = ed.search("PHYS207", (2, 3), 0.9)
    assert s == "(tag:course::phys207::l02* OR tag:course::phys207::l03*) -is:suspended (is:new OR prop:r<0.90)"


def test_plan_on_a_given_day() -> None:
    p = ed.plan(EXAMS, date(2026, 9, 26), TZ, W)
    assert [(d.name, d.threshold) for d in p.build] == [("~Exam::PHYS207-Exam-I", 0.92), ("~Exam::NUEN201-Exam-I", 0.85)]
    assert p.passed == [] and p.unmapped == []
    later = ed.plan(EXAMS, date(2026, 10, 12), TZ, W)
    assert later.passed == ["~Exam::PHYS207-Exam-I", "~Exam::NUEN201-Exam-I"]
    assert later.unmapped == ["~Exam::PHYS207-Exam-II"], "confirmed but with no lectures: flagged, not built"
    assert ed.plan(EXAMS, date(2026, 10, 20), TZ, W).unmapped == ["~Exam::PHYS207-Exam-II", "~Exam::MEEN221-Exam-II"]


class DeckCore:
    def __init__(self, decks: set[str]) -> None:
        self.decks = set(decks)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, arguments))
        if tool == "anki_decks_list":
            assert 1 <= arguments["limit"] <= 100, "anki-core caps pages at 100"
            names = sorted(self.decks)[arguments["offset"]:arguments["offset"] + 2]
            return {"items": [{"name": n} for n in names], "has_more": arguments["offset"] + 2 < len(self.decks)}
        if tool == "anki_filtered_decks_upsert":
            self.decks.add(arguments["name"])
            return {"state": "committed", "result": {"id": 1, "created": False, "cards": 12}}
        if tool == "anki_filtered_decks_remove":
            self.decks.discard(arguments["name"])
            return {"state": "committed", "result": {"removed": True, "returned_cards": 7}}
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
    for e in EXAMS:
        c.execute("INSERT INTO exam (course, kind, title, starts_at, lectures, confirmed) VALUES (?, ?, ?, ?, ?, ?)",
                  (e["course"], e["kind"], e["title"], e["starts_at"], e["lectures"], e["confirmed"]))
    return c


def test_sync_builds_then_removes_after_the_exam(con: Any) -> None:
    core = DeckCore({"~Exam", "~Exam::PHYS207-Exam-I", "~Walk::Due"})
    out = asyncio.run(ed.sync(con, core, date(2026, 9, 30), TZ, W))
    assert out["built"] == {"~Exam::PHYS207-Exam-I": 12, "~Exam::NUEN201-Exam-I": 12} and out["removed"] == {}
    upsert = next(a for t, a in core.calls if t == "anki_filtered_decks_upsert")
    assert upsert["order"] == "retrievability_ascending" and upsert["reschedule"] is True
    assert "prop:r<0.95" in upsert["search"], "exam day uses the top threshold"

    core.calls.clear()
    out = asyncio.run(ed.sync(con, core, date(2026, 10, 1), TZ, W))
    assert out["removed"] == {"~Exam::PHYS207-Exam-I": 7}, "the day after the exam, its deck goes"
    assert "~Walk::Due" in core.decks and "~Exam" in core.decks, "only past exam decks are removed"
    again = asyncio.run(ed.sync(con, core, date(2026, 10, 1), TZ, W))
    assert again["removed"] == {}, "removal happens once"


def test_the_client_refuses_tools_off_the_allowlist() -> None:
    client = CoreClient("http://unused.invalid/mcp", "t")
    with pytest.raises(AnkiCoreError, match="allowlist"):
        asyncio.run(client.call("anki_cards_reposition", {"card_ids": [1]}))
    with pytest.raises(AnkiCoreError, match="today-only"):
        asyncio.run(client.call("anki_deck_limits_update", {"deck_id": 1, "update": {"scope": "this_deck"}}))

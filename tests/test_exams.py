"""Exams from the calendar and the syllabus → exam map, confirmed in Telegram."""
import asyncio
import json
import sqlite3
from collections.abc import Iterator
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mnemosyne.api import create_app
from mnemosyne.config import Settings
from mnemosyne.extract import Page
from mnemosyne.generate.llm import Content
from mnemosyne.ledger import db
from mnemosyne.schedule import exams
from mnemosyne.telegram import parse

ROOT = Path(__file__).resolve().parent.parent
COURSES = {"NUEN201", "PHYS207", "MATH309", "MEEN221", "PHYS221", "PHYS217"}


@pytest.mark.parametrize(("title", "expected"), [
    ("PHYS-207-210 Exam II", ("PHYS207", "exam", "Exam II")),
    (" MATH-309-501 Exam I", ("MATH309", "exam", "Exam I")),
    ("Quiz 2 Special Theory of Relativity [NUEN 201 200:]", ("NUEN201", "quiz", "Quiz 2 Special Theory of Relativity")),
    ("First Week Quiz [ENGR-217:201,202,PHYS-217:201,202]", ("PHYS217", "quiz", "First Week Quiz")),
    ("UIL State Finals (if qualified)", None),
    ("PHYS-207 Lab report", None),
])
def test_classify(title: str, expected: tuple[str, str, str] | None) -> None:
    assert exams.classify(title, COURSES) == expected


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    c.execute("CREATE TABLE calendar_event (source TEXT, source_id TEXT, account TEXT, calendar TEXT, title TEXT,"
              " start TEXT, PRIMARY KEY (source, source_id))")
    rows = [("a", "1", "Exams/Assignments", "PHYS-207-210 Exam I", "2026-09-30T10:20"),
            ("b", "1", "Exams/Assignments", "PHYS-207-210 Exam I", "2026-09-30T10:20"),   # second Google account
            ("a", "2", "Exams/Assignments", "PHYS-207-210 Exam II", "2026-10-21T10:20"),
            ("a", "3", "Aman Sharma Calendar (Canvas)", "Chapter 25 Concept Quiz [PHYS-207:207,208]", "2026-10-04"),
            ("a", "4", "Personal", "PHYS-207 Exam I study group", "2026-09-28T18:00")]
    c.executemany("INSERT INTO calendar_event (source, source_id, calendar, title, start) VALUES (?, ?, ?, ?, ?)", rows)
    yield c
    c.close()


def test_calendar_rows_become_deduplicated_exams(con: sqlite3.Connection) -> None:
    found = exams.from_calendar(con, COURSES, "2026-08-01")
    assert [(e.course, e.kind, e.title) for e in found] == [
        ("PHYS207", "exam", "Exam I"), ("PHYS207", "quiz", "Chapter 25 Concept Quiz"), ("PHYS207", "exam", "Exam II")]
    assert exams.upsert(con, found) == {"added": 3, "moved": 0, "unchanged": 0, "kept": 0}
    con.execute("UPDATE calendar_event SET start = '2026-10-23T10:20' WHERE title = 'PHYS-207-210 Exam II'")
    assert exams.upsert(con, exams.from_calendar(con, COURSES, "2026-08-01")) == {
        "added": 0, "moved": 1, "unchanged": 2, "kept": 0}



def test_a_date_moved_by_hand_survives_the_calendar_until_it_agrees(con: sqlite3.Connection) -> None:
    exams.upsert(con, exams.from_calendar(con, COURSES, "2026-08-01"))
    [row] = con.execute("SELECT id, starts_at FROM exam WHERE title = 'Exam II'").fetchall()
    moved = exams.set_date(con, row["id"], date(2026, 11, 3), None, "page")
    assert moved is not None
    sync = exams.upsert(con, exams.from_calendar(con, COURSES, "2026-08-01"))
    assert sync["kept"] == 1 and sync["moved"] == 0
    got = con.execute("SELECT starts_at, date_set_by, calendar_starts_at FROM exam WHERE id = ?", (row["id"],)).fetchone()
    assert got["starts_at"] == moved and got["date_set_by"] == "page" and got["calendar_starts_at"] == row["starts_at"]

    con.execute("UPDATE calendar_event SET start = ? WHERE title = 'PHYS-207-210 Exam II'", (moved,))
    exams.upsert(con, exams.from_calendar(con, COURSES, "2026-08-01"))
    assert con.execute("SELECT date_set_by FROM exam WHERE id = ?", (row["id"],)).fetchone()[0] is None, \
        "once the calendar agrees, it leads again"
    assert exams.set_date(con, 9999, date(2026, 11, 3), None, "page") is None


def test_moving_across_the_end_of_daylight_saving_keeps_the_clock_time() -> None:
    # NUEN 201 Exam II: Thu Oct 29 3:55 pm CDT, moved to Tue Nov 3 (CST from Nov 1)
    assert exams.moved_to("2026-10-29T15:55:00-05:00", date(2026, 11, 3)) == "2026-11-03T15:55:00-06:00"
    assert exams.moved_to("2026-08-27T22:20:00Z", date(2026, 9, 3)) == "2026-09-03T17:20:00-05:00"
    assert exams.moved_to("2026-10-29T15:55:00-05:00", date(2026, 11, 3), time(9, 0)) == "2026-11-03T09:00:00-06:00"
    assert exams.same_moment("2026-11-03T21:55:00Z", "2026-11-03T15:55:00-06:00")


class ScriptedLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.content: Content = []

    async def complete(self, route: str, system: str, content: Content) -> str:
        assert route == "syllabus"
        self.content = content
        return self.reply


def test_propose_and_confirm_a_map(con: sqlite3.Connection) -> None:
    exams.upsert(con, exams.from_calendar(con, COURSES, "2026-08-01"))
    reply = "```json\n" + json.dumps({"exams": [
        {"title": "Exam I", "lectures": [1, 2, 3, 4, 5, 6, 7, 8], "topics": "Ch. 21–24"},
        {"title": "Exam 2", "lectures": [9, "10", 11, "x"], "topics": "Ch. 25–28"},
        {"title": "Exam IV", "lectures": [1], "topics": "not on the calendar"}], "notes": "not cumulative"}) + "\n```"
    llm = ScriptedLLM(reply)
    proposal = asyncio.run(exams.propose_map(llm, "PHYS207", exams.course_exams(con, "PHYS207"),
                                             [Page(1, "Week 1 ... Exam I", None)]))
    assert [(e["title"], e["lectures"]) for e in proposal["exams"]] == [("Exam I", [1, 2, 3, 4, 5, 6, 7, 8]),
                                                                        ("Exam II", [9, 10, 11])]
    assert "Exam I on 2026-09-30" in llm.content[0]["text"]
    exams.save_map(con, "PHYS207", 12, proposal)
    text = exams.announcement("PHYS207", proposal)
    assert "PHYS 207" in text and "L01–L08" in text and "L09–L11" in text
    assert exams.decide_map(con, "PHYS207", True)
    assert not exams.decide_map(con, "PHYS207", True), "a decision happens once"
    got = {r["title"]: (json.loads(r["lectures"]), r["confirmed"]) for r in exams.course_exams(con, "PHYS207")}
    assert got == {"Exam I": ([1, 2, 3, 4, 5, 6, 7, 8], 1), "Exam II": ([9, 10, 11], 1)}


def test_map_buttons_round_trip_through_the_api(tmp_path: Path, con: sqlite3.Connection) -> None:
    (tmp_path / "approve").write_text("a-token")
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, approve_token_file=tmp_path / "approve")
    exams.upsert(con, exams.from_calendar(con, COURSES, "2026-08-01"))
    exams.save_map(con, "PHYS207", 12, {"exams": [{"title": "Exam I", "starts_at": "2026-09-30T10:20",
                                                   "lectures": [1, 2], "topics": ""}], "notes": ""})
    client = TestClient(create_app(settings, core=None, schedule=False))
    auth = {"Authorization": "Bearer a-token"}
    outbox: list[dict[str, Any]] = client.get("/telegram/outbox", headers=auth).json()
    item = next(o for o in outbox if o["batch_id"] == "map:PHYS207")
    confirm = item["keyboard"][0][0]["callback_data"]
    assert parse(confirm) == ("c", "PHYS207")
    client.post("/telegram/sent", json={"batch_id": "map:PHYS207", "message_ref": "7"}, headers=auth)
    assert all(o["batch_id"] != "map:PHYS207" for o in client.get("/telegram/outbox", headers=auth).json())
    reply = client.post("/telegram/callback", json={"data": confirm}, headers=auth).json()
    assert reply["toast"] == "Confirmed"
    row = con.execute("SELECT confirmed, lectures FROM exam WHERE title = 'Exam I'").fetchone()
    assert (row["confirmed"], json.loads(row["lectures"])) == (1, [1, 2])


@pytest.mark.parametrize(("a", "b"), [("Exam II", "exam 2"), ("Exam I", "EXAM-1"), ("Midterm Exam 3", "Exam III")])
def test_title_key_matches_numbering_styles(a: str, b: str) -> None:
    assert exams.title_key(a) == exams.title_key(b)

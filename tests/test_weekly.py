"""The weekly audit's evidence (weekly.py): one week of cards, Aman's decisions, how new cards hold, and the gaps."""
import asyncio
import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemosyne import weekly
from mnemosyne.api import create_app
from mnemosyne.config import Settings
from mnemosyne.ledger import db, store
from test_service import APPROVE, PROPOSE, bearer
from test_tutor_and_leeches import ROOT, TZ, Collection

IN = "2026-10-06T15:00:00+00:00"       # inside the week to Sat Oct 10
BEFORE = "2026-10-01T15:00:00+00:00"   # the week before


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "h.db")
    db.migrate(c)
    yield c
    c.close()


def card(con: sqlite3.Connection, i: int, batch: str, status: str, *, at: str = IN, reason: str | None = None,
         by: str | None = None, note: str | None = None, before: dict[str, str] | None = None) -> None:
    fields = {"Front": f"prompt {i}", "Back": "answer", "SpeakFront": "x"}
    con.execute("INSERT INTO card_draft (mnemosyne_id, batch_id, content_hash, note_type, deck, fields_json, tags_json,"
                " mode, status, review_reason, decided_by, decision_note, before_json, decided_at, created_at,"
                " updated_at) VALUES (?, ?, ?, 'M-Basic', 'Now', ?, ?, 'audio', ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"m{i}", batch, f"h{i}", json.dumps(fields), json.dumps(["type::result", "topic::physics::x"]),
                 status, reason, by, note, json.dumps(before) if before else None, at if by else None, at, at))


def seed(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO card_source (id, payload_hash, origin, kind, course, lecture, ref, received_at)"
                " VALUES (1, 'p1', 'paperless', 'slides', 'PHYS207', 9, 'PHYS 207 L09 - DC Circuits', ?)", (IN,))
    con.execute("INSERT INTO card_batch (id, source_id, status, created_at) VALUES ('b1', 1, 'pending', ?)", (IN,))
    store.save_skipped(con, "b1", [{"what": "slide 12's example numbers", "why": "one device's values"}])
    card(con, 1, "b1", "written")                                                     # the approval policy
    card(con, 2, "b1", "approved", reason="duplicate_close: 0.91 to 'τ = RC'", by="review-page",
         note="too long", before={"Front": "prompt 2", "Back": "answer, and why it holds"})
    card(con, 3, "b1", "rejected", reason="verify_failed: 9.6 nC", by="review-page", note="too specific")
    card(con, 4, "b1", "rejected", reason="duplicate_close: 0.95", by="claude", note="repeats t = RC ln n")
    card(con, 5, "b1", "pending", reason="duplicate_close: 0.90")
    card(con, 6, "b1", "written", at=BEFORE)                                          # last week's: left out
    con.execute("INSERT INTO leech_case (card_id, note_id, mnemosyne_id, note_type, deck, fields_json, tags_json,"
                " signals_json, lapses, status, diagnosis, explanation, action, found_at)"
                " VALUES (11, 1011, 'm1', 'M-Basic', 'Now', ?, '[]', '{}', 5, 'proposed', 'not_atomic',"
                " 'two facts', 'replace', ?)", (json.dumps({"Front": "prompt 1"}), IN))
    con.execute("UPDATE card_draft SET note_id = 1011 WHERE mnemosyne_id = 'm1'")
    con.execute("INSERT INTO question (origin, course, lecture, asked, kind, status, no_card, created_at, updated_at)"
                " VALUES ('lecture', 'PHYS207', 9, 'why RC?', 'mechanism', 'open', NULL, ?, ?)", (IN, IN))
    con.execute("INSERT INTO question (origin, course, lecture, asked, kind, status, no_card, created_at, updated_at)"
                " VALUES ('lecture', 'PHYS207', 9, 'what is mA?', 'clarify', 'answered', 'notation', ?, ?)", (IN, IN))
    con.execute("INSERT INTO mistake (doc_id, course, question, kind, concept, evidence, created_at, updated_at)"
                " VALUES (466, 'PHYS207', '5', 'conceptual', 'field between sheets', 'his_note', ?, ?)", (IN, IN))
    con.execute("INSERT INTO probe (course, hook, question, right, origin, created_at)"
                " VALUES ('PHYS207', 'τ = RC', 'what sets the time to half charge?', 0, 'mcp', ?)", (IN,))


def test_the_week_counts_intake_his_decisions_skips_and_gaps(con: sqlite3.Connection) -> None:
    seed(con)
    week = asyncio.run(weekly.week(None, con, TZ, date(2026, 10, 10), date(2026, 10, 11), 100.0))
    assert week["window"] == {"first": "2026-10-04", "last": "2026-10-10", "days": 7}
    intake = week["intake"]
    assert intake["cards"] == 5, "last week's card is outside the window"
    src = intake["sources"][0]
    assert (src["cards"], src["auto_approved"], src["held"], src["edited"], src["rejected"], src["waiting"]) == (
        5, 1, 4, 1, 2, 1)
    assert intake["held_because"] == {"duplicate_close": 3, "verify_failed": 1}
    assert weekly.reason_kind("dedupe_flagged (0.90): close to 'τ = RC'") == "dedupe_flagged"
    assert weekly.reason_kind("graded-exam needs human review") == "graded-exam"
    said = week["said"]
    assert said["edits"][0]["changed"] == {"Back": {"was": "answer, and why it holds", "now": "answer"}}
    assert [r["note"] for r in said["rejected_by_aman"]] == ["too specific"] and said["rejected_by_claude"] == 1
    assert week["skipped"] == [{"course": "PHYS207", "ref": "PHYS 207 L09 - DC Circuits",
                                "what": "slide 12's example numbers", "why": "one device's values"}]
    assert week["leeches"]["by_diagnosis"] == {"not_atomic": 1} and week["leeches"]["pipeline_cards"] == 1
    assert week["questions"]["no_card"] == [{"asked": "what is mA?", "why": "notation"}]
    assert [q["asked"] for q in week["questions"]["open"]] == ["why RC?"]
    assert week["mistakes"]["open_never_tried_total"] == 1
    assert week["probes"][0]["right"] is False
    assert "holding" not in week, "no anki-core, no Anki section"


def test_decisions_before_decided_at_existed_still_count(con: sqlite3.Connection) -> None:
    seed(con)
    con.execute("UPDATE card_draft SET decided_at = NULL")   # decided before 2026-10-07: only updated_at
    said = asyncio.run(weekly.week(None, con, TZ, date(2026, 10, 10), date(2026, 10, 11), 100.0))["said"]
    assert said["edits_total"] == 1 and said["rejected_by_aman_total"] == 1


def test_job_errors_drop_the_gateways_html_page() -> None:
    assert weekly.short_error("lecture via tamu: HTTP 524: <!DOCTYPE html>\n<html>…") == (
        "lecture via tamu: HTTP 524 (an HTML error page)")
    assert weekly.short_error("can't tell course and lecture") == "can't tell course and lecture"


def test_holding_shares_what_new_cards_already_failed() -> None:
    col = Collection()
    col.totals["introduced:8"] = 10
    col.totals["introduced:8 rated:8:1"] = 3
    col.totals["introduced:8 tag:course::phys207::*"] = 4
    col.totals["introduced:8 rated:8:1 tag:course::phys207::*"] = 2
    col.add(21, "RC time constant", "τ = RC", lapses=2)
    col.searches["introduced:8 prop:lapses>=1 -is:suspended"] = [21]
    held = asyncio.run(weekly.holding(col, 8, ["PHYS207", "MATH309"]))  # pyright: ignore[reportArgumentType]
    assert held["all"] == {"introduced": 10, "failed_once": 3, "fail_share": 0.3}
    assert held["by_course"] == {"PHYS207": {"introduced": 4, "failed_once": 2, "fail_share": 0.5}}
    assert held["lapsed"]["total"] == 1 and held["lapsed"]["cards"][0]["prompt"] == "RC time constant"


def test_the_audit_reads_with_the_propose_token(tmp_path: Path) -> None:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve")
    client = TestClient(create_app(settings, core=Collection(), con=db.connect(settings.ledger), schedule=False))
    r = client.get("/weekly", params={"last": "2026-10-10"}, headers=bearer(PROPOSE))
    assert r.status_code == 200 and r.json()["window"]["first"] == "2026-10-04"
    assert "holding" in r.json() and "exams" in r.json()
    assert client.get("/weekly", params={"last": "Sunday"}, headers=bearer(PROPOSE)).status_code == 422
    assert client.get("/weekly").status_code == 401

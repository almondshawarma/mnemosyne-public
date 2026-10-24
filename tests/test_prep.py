"""Exam prep (prep.py): telling prep material from lectures, finding its exam, reading it (with the retry), attaching
it through the pipeline, the pack's coverage, and probes over the API."""
import asyncio
import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mnemosyne import learner, prep
from mnemosyne.api import create_app
from mnemosyne.cards import Schema
from mnemosyne.config import Settings
from mnemosyne.generate.propose import Context
from mnemosyne.ingest.paperless import Doc
from mnemosyne.ledger import db
from mnemosyne.ledger.db import now
from mnemosyne.pipeline import Services, Tags, attach_prep, is_prep
from test_graded import ExamPaperless
from test_pipeline import BagEmbedder, FakeCore, ScriptedLLM
from test_service import APPROVE, PROPOSE, bearer
from test_tutor_and_leeches import Collection

ROOT = Path(__file__).resolve().parent.parent
TAGS = Tags({5: "MATH309"}, 1, 2, 90, 91, [90, 91], 70, exam_type=3, assignment_type=4, graded=71)
SUBSPACES = "topic::math::linear-algebra::subspaces"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


def exams(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO exam (id, course, kind, title, starts_at, lectures, confirmed) VALUES "
                "(11, 'MATH309', 'exam', 'Exam I', '2026-10-02T09:10:00-05:00', '[1, 2, 3, 5]', 1), "
                "(23, 'MATH309', 'exam', 'Exam II', '2026-11-13T09:10:00-06:00', '[6, 7]', 1)")


def reply(**over: Any) -> str:
    body: dict[str, Any] = {
        "summary": "Eighteen review exercises for Exam 1.", "scope": ["Chapters 1-3"],
        "format": "5 T/F, 3 fill-in, 5 work-out", "rules": None, "emphasis": ["Fundamental subspaces: definitely on exam"],
        "problems": [
            {"number": 16, "summary": "Bases for Row, Col, Nul of a 3x4 matrix", "format": "work-out", "lectures": [5],
             "topics": [SUBSPACES], "his_status": "worked", "his_marks": "why pivot columns of A, not of R?"},
            {"number": "18", "summary": "Change-of-basis matrix from C to B", "format": "Work-Out", "lectures": [],
             "topics": ["topic::math::linear-algebra::change-of-basis"], "his_status": "?"}]}
    body.update(over)
    return "```json\n" + json.dumps(body) + "\n```"


def test_prep_material_is_told_from_lectures_by_its_title() -> None:
    assert prep.kind_of("MATH 309 Exam 1 Review") == "review"
    assert prep.kind_of("PHYS 207 Exam II Practice - Spring 2025") == "practice"
    assert prep.kind_of("PHYS 207 Practice Exam KEY") == "key", "a key wins over practice"
    assert prep.kind_of("MATH 309 Review 1 Solutions") == "key"
    assert prep.kind_of("MATH 309 L08 - Review of Subspaces") is None, "a lecture titled review is a lecture"
    assert prep.kind_of("PHYS 207 Exam 01") is None, "a returned exam is graded work"
    assert prep.kind_of("PHYS 207 Exam II Formula Sheet") == "sheet"
    assert prep.kind_of("PHYS 221 Practice Exam 2 Equation Sheet") == "sheet", "a sheet wins over practice"
    review = Doc(455, "MATH 309 Exam 1 Review", frozenset({5}), 2, "2026-10-01")
    assert is_prep(review, TAGS) and not is_prep(Doc(456, "MATH 309 Exam 1 Review", frozenset({5}), 4, ""), TAGS)


def test_prep_finds_its_exam_by_number_else_the_next_one(con: sqlite3.Connection) -> None:
    exams(con)
    assert (prep.exam_for(con, "MATH309", "MATH 309 Exam 1 Review", "2026-10-01") or {})["id"] == 11
    assert (prep.exam_for(con, "MATH309", "MATH 309 Exam II Practice", "2026-09-01") or {})["id"] == 23
    assert (prep.exam_for(con, "MATH309", "MATH 309 Review Problems", "2026-10-05") or {})["id"] == 23
    assert prep.exam_for(con, "MATH309", "MATH 309 Final Review", "2026-12-01") is None
    assert (prep.pick_exam(con, "MATH 309", "2", date(2026, 10, 1)) or {})["id"] == 23
    assert (prep.pick_exam(con, "math309", "", date(2026, 10, 1)) or {})["id"] == 11


def test_a_reply_that_breaks_the_vocabulary_is_retried_once(con: sqlite3.Connection) -> None:
    exams(con)
    bad = reply(problems=[{"number": "1", "summary": "REF or RREF", "topics": ["row-reduction"]}])
    llm = ScriptedLLM(bad, reply())
    doc = prep.Doc(455, "MATH309", "review", "MATH 309 Exam 1 Review", "http://p/documents/455/details",
                   prep.exam_for(con, "MATH309", "Exam 1", "2026-10-01"), ((5, "MATH 309 L05 - Fundamental Subspaces"),))
    found = asyncio.run(prep.read(llm, Context("", ""), doc, []))  # type: ignore[arg-type]
    assert len(llm.prompts) == 2 and "isn't a topic:: tag" in str(llm.prompts[1][1][-1]["text"])  # type: ignore[index]
    assert [(p.number, p.format, p.his_status) for p in found.problems] == [("16", "work-out", "worked"),
                                                                            ("18", "work-out", "blank")]
    assert "L05: MATH 309 L05 - Fundamental Subspaces" in str(llm.prompts[0][1][0])
    prep.save(con, doc, found)
    prep.save(con, doc, found)
    [m] = prep.materials(con, 11)
    assert m["kind"] == "review" and m["emphasis"] == ["Fundamental subspaces: definitely on exam"]
    with pytest.raises(ValueError):
        asyncio.run(prep.read(ScriptedLLM("no json", "still none"), Context("", ""), doc, []))  # type: ignore[arg-type]


def test_the_poller_attaches_a_review_sheet_and_makes_no_cards(con: sqlite3.Connection, tmp_path: Path) -> None:
    exams(con)
    schema = Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")
    llm = ScriptedLLM(reply())
    svc = Services(con, schema, FakeCore({}), llm, BagEmbedder(), ExamPaperless(), ROOT,  # type: ignore[arg-type]
                   ROOT / ".claude" / "skills" / "mnemosyne-cards", notes_dir=tmp_path)
    asyncio.run(attach_prep(svc, Doc(455, "MATH 309 Exam 1 Review", frozenset({5}), 2, "2026-10-01"), TAGS))
    [m] = prep.materials(con, 11)
    assert m["course"] == "MATH309" and len(m["problems"]) == 2
    assert con.execute("SELECT COUNT(*) FROM card_draft").fetchone()[0] == 0, "prep material is never carded"
    assert "topic::" in llm.prompts[0][0], "the prompt carries the topic vocabulary"


def test_the_pack_counts_coverage_by_lecture_and_topic(con: sqlite3.Connection) -> None:
    exams(con)
    con.execute("INSERT INTO card_source (payload_hash, origin, kind, course, lecture, ref, link, received_at) VALUES "
                "('a', 'paperless', 'slides', 'MATH309', 5, 'MATH 309 L05 - Fundamental Subspaces',"
                " 'http://p/documents/450/details', ?)", (now(),))
    doc = prep.Doc(455, "MATH309", "review", "MATH 309 Exam 1 Review", None, {"id": 11})
    prep.save(con, doc, prep.Material.model_validate(json.loads(reply().split("```json\n")[1].split("\n```")[0])))
    prep.add_probe(con, prep.ProbeIn(course="MATH 309", hook="Nul(A) is the solution set of Ax = 0",
                                     question="Why is every null vector orthogonal to every row?", right=False,
                                     exam="Exam I"), "mcp")
    col = Collection()
    l05 = learner.lecture_query("MATH309", [5])
    col.totals.update({l05: 12, f"{l05} -is:new prop:r>=0.9 prop:ivl>=21": 6, f"tag:{SUBSPACES}* -is:suspended": 30})
    out = asyncio.run(prep.pack(col, con, {"id": 11, "course": "MATH309", "title": "Exam I",  # type: ignore[arg-type]
                                           "starts_at": "2026-10-02", "lectures": "[1, 2, 3, 5]", "confirmed": 1},
                                None, date(2026, 10, 1)))
    by = {x["lecture"]: x for x in out["lectures"]}
    assert by[5]["docs"] == [{"doc_id": 450, "title": "MATH 309 L05 - Fundamental Subspaces"}] and by[5]["cards"] == 12
    assert out["readiness"] == 0.5 and out["rules"]["sheet"] == "unknown"
    problems = {p["number"]: p for p in out["prep"][0]["problems"]}
    assert problems["16"]["cards"] == {"lectures": 12, "topics": 30}
    assert out["prep"][0]["uncovered"] == ["18"], "change of basis has no cards anywhere"
    assert [p["right"] for p in out["probes"]] == [False]
    assert con.execute("SELECT exam_id FROM probe").fetchone()[0] == 11


def test_a_formula_sheet_is_read_for_its_contents_and_rides_in_the_pack(con: sqlite3.Connection) -> None:
    exams(con)
    doc = prep.Doc(470, "MATH309", "sheet", "MATH 309 Exam II Formula Sheet", None, {"id": 23, "title": "Exam II"})
    no_items = reply(problems=[], contents=[])
    items = reply(problems=[], contents=[r"rank–nullity: \(\operatorname{rank}A + \dim\operatorname{Nul}A = n\)"])
    llm = ScriptedLLM(no_items, items)
    found = asyncio.run(prep.read(llm, Context("", ""), doc, []))  # type: ignore[arg-type]
    assert "list every item on it" in str(llm.prompts[1][1][-1]["text"])  # type: ignore[index]
    prep.save(con, doc, found)
    out = asyncio.run(prep.pack(Collection(), con, {"id": 23, "course": "MATH309", "title": "Exam II",  # type: ignore[arg-type]
                                                    "starts_at": "2026-11-13", "lectures": "[6]", "confirmed": 1},
                                {"sheet": "provided"}, date(2026, 11, 1)))
    assert out["formula_sheet"] == found.contents and out["prep"] == [], "the sheet isn't a practice problem set"
    out = asyncio.run(prep.pack(Collection(), con, {"id": 11, "course": "MATH309", "title": "Exam I",  # type: ignore[arg-type]
                                                    "starts_at": "2026-10-02", "lectures": "[]", "confirmed": 1},
                                {"sheet": "provided"}, date(2026, 10, 1)))
    assert "not filed yet" in out["formula_sheet"]


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection]:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve")
    c = db.connect(settings.ledger)
    return TestClient(create_app(settings, core=Collection(), con=c, schedule=False)), c


def test_probes_and_the_pack_over_the_api(api: tuple[TestClient, sqlite3.Connection]) -> None:
    client, c = api
    exams(c)
    made = client.post("/probes", json={"course": "MATH309", "hook": "rank + nullity = n", "right": True,
                                        "question": "A 5x7 matrix has rank 3: how many free variables?"},
                       headers=bearer(PROPOSE))
    assert made.status_code == 200 and made.json()["id"] == 1
    assert client.post("/probes", json={"course": "MATH309", "hook": "x", "question": "y", "right": True,
                                        "fixed": True}, headers=bearer(PROPOSE)).status_code == 422
    r = client.get("/exams/pack", params={"course": "MATH 309", "exam": "Exam II"}, headers=bearer(PROPOSE))
    assert r.status_code == 200 and r.json()["exam"]["id"] == 23
    assert r.json()["rules"]["sheet"] == "none" and r.json()["formula_sheet"] is None
    assert client.get("/exams/pack", params={"course": "MEEN221"}, headers=bearer(PROPOSE)).status_code == 404
    assert learner.probe_summary(c, date.fromisoformat(now()[:10]))["tried"] == 1

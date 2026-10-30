"""Graded work (PLAN §4.6): reading the title, matching the exam, the debrief's checks, and one returned exam end to
end: mistakes kept, error cards held (with the doubt when the diagnosis rests on the key alone), careless misses
logged without cards, and the debrief in the stub."""
import asyncio
import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from mnemosyne import graded
from mnemosyne.cards import Payload, Schema
from mnemosyne.inbox import Verdict, propose
from mnemosyne.ingest.paperless import Doc
from mnemosyne.ledger import db, store
from mnemosyne.ledger.db import now
from mnemosyne.pipeline import Services, Tags, debrief, is_graded
from mnemosyne.project import obsidian
from test_pipeline import BagEmbedder, FakeCore, FakePaperless, ScriptedLLM, make_pdf

ROOT = Path(__file__).resolve().parent.parent
LTAG = "course::phys207::l03-electric-field"
TAGS = Tags({5: "PHYS207"}, 1, 2, 90, 91, [90, 91], 70, exam_type=3, assignment_type=4, graded=71)


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


def error_card(situation: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "note_type": "M-Error", "deck": "Now", "mode": "audio",
        "fields": {"Situation": situation, "Trap": "Treating two of three sheets as a capacitor",
                   "Fix": "Add every sheet's field, \\(\\sigma/2\\varepsilon_0\\) each", "Why": "A sheet's field doesn't fall off",
                   "SpeakSituation": situation, "SpeakFix": "add every sheet's field, sigma over two epsilon nought each",
                   "Source": "PHYS 207 Exam 01, Q5"},
        "tags": [LTAG, "topic::physics::em::field", "type::trap", "src::exam"]}
    base.update(over)
    return base


def seed_lecture_cards(con: sqlite3.Connection, schema: Schema) -> None:
    """Cards already carded from L03, under its usual tag and one stray spelling."""
    cards = [error_card("seed one"), error_card("seed two"),
             error_card("seed three", tags=["course::phys207::l03-e-field", "topic::physics::em::field", "type::trap",
                                            "src::slides"])]
    propose(con, schema, Payload.model_validate({"source": {"kind": "slides"}, "cards": cards}), "inbox",
            lambda _i, _c: Verdict(approved=True))


def miss(question: str, kind: str, evidence: str, cards: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"question": question, "qid": "7", "possible": 6, "earned": 1, "his_answer": "C",
                            "correct_answer": "G", "his_words": "Don't drop sheet 1!!!", "kind": kind,
                            "concept": "superposing infinite sheets", "lecture": 3, "topic": "topic::physics::em::field",
                            "trap": "t", "fix": "f", "why": "w", "evidence": evidence, "solved": "11/ε₀",
                            "agrees_with_key": True, "cards": cards}
    base.update(over)
    return base


def reply(misses: list[dict[str, Any]]) -> str:
    return "```json\n" + json.dumps({"earned": 41, "possible": 50, "feedback": "key", "summary": "Two misses.",
                                     "misses": misses}) + "\n```"


# ---------- reading the document ----------

@pytest.mark.parametrize(("title", "expected"), [
    ("PHYS 207 Exam 01", ("exam", 1)), ("PHYS 207 Exam II", ("exam", 2)), ("MATH 309 HW04", ("homework", 4)),
    ("MEEN 221 Quiz 3 - Trusses", ("quiz", 3)), ("NUEN 201 Final", ("exam", None)),
])
def test_work_ref(title: str, expected: tuple[str, int | None]) -> None:
    assert graded.work_ref(title) == expected


def test_match_exam_by_number_then_by_date(con: sqlite3.Connection) -> None:
    con.executemany("INSERT INTO exam (id, course, kind, title, starts_at, lectures, confirmed) VALUES (?, ?, ?, ?, ?, ?, 1)",
                    [(9, "PHYS207", "exam", "Exam I", "2026-09-30T10:20:00-05:00", "[2, 3]"),
                     (16, "PHYS207", "exam", "Exam II", "2026-10-21T10:20:00-05:00", "[]"),
                     (5, "PHYS207", "quiz", "Chapter 21 Concept Quiz", "2026-09-06", None)])
    assert (graded.match_exam(con, "PHYS207", 1, "2026-10-06") or {})["id"] == 9
    assert (graded.match_exam(con, "PHYS207", 2, "2026-10-06") or {})["id"] == 16
    assert (graded.match_exam(con, "PHYS207", None, "2026-10-06") or {})["id"] == 9, "the last one held before"


def test_readiness_before_is_the_last_recorded_day_before_the_exam(con: sqlite3.Connection) -> None:
    for day, r in (("2026-09-28", 0.49), ("2026-09-29", 0.4), ("2026-09-30", 0.1)):
        con.execute("INSERT INTO metric_day (day, metrics_json, created_at) VALUES (?, ?, ?)",
                    (day, json.dumps({"exam_readiness": {"PHYS207-Exam-I": r}}), now()))
    assert graded.readiness_before(con, "PHYS207", "Exam I", "2026-09-30") == 0.4


def test_only_exams_and_marked_assignments_are_graded_work() -> None:
    assert is_graded(Doc(1, "PHYS 207 Exam 01", frozenset({5}), 3, "2026-10-06"), TAGS)
    assert not is_graded(Doc(2, "MATH 309 HW04", frozenset({5}), 4, "2026-10-06"), TAGS), "a blank assignment"
    assert is_graded(Doc(3, "MATH 309 HW04", frozenset({5, 71}), 4, "2026-10-06"), TAGS)
    assert not is_graded(Doc(4, "PHYS 207 L08", frozenset({5}), 2, "2026-10-06"), TAGS)


# ---------- the debrief's checks ----------

def work() -> graded.Work:
    return graded.Work(466, "PHYS207", "exam", 1, "PHYS 207 Exam 01", "http://p/documents/466/details",
                       lecture_tags=(LTAG,))


def test_check_flags_cards_on_misses_a_card_cant_fix_and_missing_tags(schema: Schema) -> None:
    doc = json.loads(reply([miss("5", "conceptual", "his_note", [error_card("x", tags=[LTAG, "type::trap"])]),
                            miss("7", "careless", "his_note", [error_card("y")]),
                            miss("8", "vibes", "none", [])]).strip("`json\n"))
    found, problems = graded.check(doc, work(), schema, None)
    assert found is not None and [m.question for m in found.misses] == ["5", "7"], "an unknown kind isn't kept"
    said = json.dumps(problems)
    assert "src::exam" in said and "a careless miss gets no cards" in said and "kind must be one of" in said


def test_a_diagnosis_is_confirmed_only_by_something_written() -> None:
    def m(**over: Any) -> graded.Miss:
        return graded.Miss.model_validate({**miss("5", "conceptual", "his_note", []), **over})
    assert m().confirmed and m().doubt() is None
    assert not m(evidence="key_only").confirmed and "nothing written" in (m(evidence="key_only").doubt() or "")
    assert not m(agrees_with_key=False).confirmed and "doesn't match the key" in (m(agrees_with_key=False).doubt() or "")


# ---------- one returned exam end to end ----------

class ExamPaperless(FakePaperless):
    async def download(self, doc_id: int) -> bytes:
        return make_pdf("Problem 5: three infinite sheets", "Problem 7: a point charge")

    async def notes(self, doc_id: int) -> list[str]:
        return []


def test_a_returned_exam_end_to_end(con: sqlite3.Connection, schema: Schema, tmp_path: Path) -> None:
    from test_questions import stub
    con.execute("INSERT INTO exam (id, course, kind, title, starts_at, lectures, confirmed) VALUES "
                "(9, 'PHYS207', 'exam', 'Exam I', '2026-09-30T10:20:00-05:00', '[3]', 1)")
    con.execute("INSERT INTO metric_day (day, metrics_json, created_at) VALUES (?, ?, ?)",
                ("2026-09-29", json.dumps({"exam_readiness": {"PHYS207-Exam-I": 0.4}}), now()))
    seed_lecture_cards(con, schema)
    assert graded.lecture_tags(con, "PHYS207") == [LTAG], "one tag per lecture, its most common spelling"
    root = tmp_path / "Paperless"
    path = stub(root, 466, "PHYS 207 Exam 01")
    llm = ScriptedLLM(reply([
        miss("5", "conceptual", "his_note", [error_card("Field between two of three charged sheets")]),
        miss("7", "conceptual", "key_only", [error_card("One component of a point charge's field")], earned=2),
        miss("9", "careless", "his_note", [], concept="sign slip", earned=4),
    ]))
    svc = Services(con, schema, FakeCore({}), llm, BagEmbedder(), ExamPaperless(), ROOT,  # type: ignore[arg-type]
                   ROOT / ".claude" / "skills" / "mnemosyne-cards", notes_dir=root)
    out = asyncio.run(debrief(svc, Doc(466, "PHYS 207 Exam 01", frozenset({5}), 3, "2026-10-06"), TAGS))

    assert out.created == 2 and out.approved == 0, "graded cards always wait for Aman"
    drafts = {d.fields["Situation"]: d for d in store.batch_drafts(con, out.batch_id or "")}
    assert all(d.fields["SourceLink"] == "http://paperless/documents/466/details" for d in drafts.values())
    assert "nothing written" in (drafts["One component of a point charge's field"].review_reason or "")
    assert "nothing written" not in (drafts["Field between two of three charged sheets"].review_reason or "")

    w = graded.work_for(con, 466) or {}
    assert (w["earned"], w["possible"], w["exam_id"], w["readiness_before"]) == (41, 50, 9, 0.4)
    ms = {m["question"]: m for m in graded.mistakes_for(con, 466)}
    assert set(ms) == {"5", "7", "9"} and ms["9"]["card_ids"] == "[]"
    assert ms["5"]["confirmed"] and not ms["7"]["confirmed"]
    assert json.loads(ms["5"]["card_ids"]) == [drafts["Field between two of three charged sheets"].mnemosyne_id]

    system, content = llm.prompts[0]
    assert "Re-solve it yourself" in system and "src::exam" in system and "graded:" in system
    assert LTAG in content[0]["text"] and "lectures 3" in content[0]["text"]

    text = path.read_text(encoding="utf-8")
    assert "## Debrief: 41/50 (82%)" in text and "readiness the day before: 40%" in text
    assert "unconfirmed" in text and text.index("Debrief") > text.index(obsidian.HELIOS)
    path.write_text(text.replace("- [ ] **Q9", "- [x] **Q9"), encoding="utf-8")
    obsidian.sync_doc(con, root, 466)
    assert {m["question"]: m["status"] for m in graded.mistakes_for(con, 466)} == {"5": "open", "7": "open", "9": "fixed"}

    prof = graded.profile(con, date(2026, 10, 6))
    assert prof["results"][0]["percent"] == 82 and [m["question"] for m in prof["open_mistakes"]] == ["5", "7"]
    assert prof["pattern"] == {"PHYS207": {"conceptual": 2, "careless": 1}}

    assert graded.set_score(con, 466, 41, 50)
    llm.replies.append(reply([]))
    asyncio.run(debrief(svc, Doc(466, "PHYS 207 Exam 01", frozenset({5}), 3, "2026-10-06"), TAGS))
    assert (graded.work_for(con, 466) or {})["earned"] == 41, "a rerun keeps the score Aman gave"

    store.decide_cards(con, out.batch_id or "", [store.CardDecision(
        drafts["One component of a point charge's field"].mnemosyne_id, approve=True)], "review-page")
    assert {m["question"]: m["confirmed"] for m in graded.mistakes_for(con, 466)}["7"], "approving its card confirms it"


class TaggingPaperless(ExamPaperless):
    async def add_tag(self, doc_id: int, tag: int) -> None:
        self.tagged.append((doc_id, tag))

    async def remove_tag(self, doc_id: int, tag: int) -> None:
        self.tagged.append((doc_id, -tag))


def test_a_debrief_written_in_a_session_is_checked_strictly_then_lands_like_the_models(
        con: sqlite3.Connection, schema: Schema) -> None:
    from mnemosyne.pipeline import Rejected, debrief_written, job_attempts, record_job
    con.execute("INSERT INTO exam (id, course, kind, title, starts_at, lectures, confirmed) VALUES "
                "(16, 'PHYS207', 'exam', 'Exam II', '2026-10-21T10:20:00-05:00', '[3]', 1)")
    seed_lecture_cards(con, schema)
    record_job(con, 526, "error", 2, "the reply hit max_tokens 16000", kind="graded")
    paperless = TaggingPaperless()
    svc = Services(con, schema, FakeCore({}), ScriptedLLM(), BagEmbedder(), paperless, ROOT,  # type: ignore[arg-type]
                   ROOT / ".claude" / "skills" / "mnemosyne-cards")
    doc = Doc(526, "PHYS 207 Exam 02", frozenset({5}), 3, "2026-10-27")

    bad = json.loads(reply([miss("2", "conceptual", "key_only", [error_card("x", tags=[LTAG, "type::trap"])])])
                     .strip("`json\n"))
    with pytest.raises(Rejected) as e:
        asyncio.run(debrief_written(svc, doc, TAGS, bad))
    assert "src::exam" in json.dumps(e.value.problems)
    assert graded.work_for(con, 526) is None and paperless.tagged == [], "a rejected debrief writes nothing"

    good = json.loads(reply([miss("2", "conceptual", "key_only", [error_card("An emf in a Kirchhoff loop")]),
                             miss("1", "careless", "key_only", [], concept="circle area")]).strip("`json\n"))
    out = asyncio.run(debrief_written(svc, doc, TAGS, good))
    assert out.created == 1 and out.approved == 0, "held for review, like every graded card"
    assert {m["question"] for m in graded.mistakes_for(con, 526)} == {"1", "2"}
    assert (graded.work_for(con, 526) or {})["exam_id"] == 16
    assert job_attempts(con, 526, "graded")[0] == "done" and (526, TAGS.done) in paperless.tagged
    assert (526, -TAGS.error) in paperless.tagged


def test_fresh_problems_are_kept_right_or_wrong(con: sqlite3.Connection) -> None:
    stamp = now()
    con.execute("INSERT INTO graded_work (doc_id, course, kind, title, earned, possible, created_at, updated_at)"
                " VALUES (466, 'PHYS207', 'exam', 'PHYS 207 Exam 01', 41, 50, ?, ?)", (stamp, stamp))
    mid = int(con.execute("INSERT INTO mistake (doc_id, course, question, kind, concept, evidence, created_at, updated_at)"
                          " VALUES (466, 'PHYS207', '5', 'conceptual', 'field between sheets', 'his_note', ?, ?)"
                          " RETURNING id", (stamp, stamp)).fetchone()[0])
    assert graded.add_attempt(con, mid + 1, graded.Attempt(right=True), "mcp") is None
    graded.add_attempt(con, mid, graded.Attempt(right=False, note="added the outer sheets' fields"), "mcp")
    graded.add_attempt(con, mid, graded.Attempt(right=True, hints=True), "mcp")
    graded.add_attempt(con, mid, graded.Attempt(right=True, problem="four sheets, field in the second gap"), "mcp")

    [m] = graded.profile(con, date.fromisoformat(stamp[:10]))["open_mistakes"]
    assert (m["attempts"]["tried"], m["attempts"]["clean"], m["attempts"]["last"]["right"]) == (3, 1, True)
    assert graded.mistakes_for(con, 466)[0]["status"] == "open", "a try never closes a mistake: his tick does"
    assert "fresh problems: 1 of 3 clean" in "\n".join(graded.stub_lines(con, 466))
    with pytest.raises(ValueError):
        graded.Attempt.model_validate({"right": True, "fixed": True})

def test_practice_exams_and_keys_need_the_tag() -> None:
    assert not is_graded(Doc(5, "PHYS 207 Exam 2 Practice", frozenset({5}), 3, "2026-10-06"), TAGS)
    assert not is_graded(Doc(6, "PHYS 207 Exam 1 KEY", frozenset({5}), 3, "2026-10-06"), TAGS)
    assert is_graded(Doc(7, "PHYS 207 Exam 2 Practice", frozenset({5, 71}), 3, "2026-10-06"), TAGS), "one he took"


def test_scans_render_capped_and_as_jpeg() -> None:
    import pymupdf  # pyright: ignore[reportMissingTypeStubs]

    from mnemosyne.extract import pages
    from mnemosyne.generate.llm import image
    got = pages(make_pdf("a page"), dpi=300, max_px=200, jpeg=True)[0].png or b""
    pix = pymupdf.Pixmap(got)
    assert got[:2] == b"\xff\xd8" and max(pix.width, pix.height) <= 200
    assert image(got)["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_a_streamed_reply_is_put_back_together() -> None:
    from mnemosyne.generate.llm import streamed
    lines = [r'data: {"choices":[{"index":0,"delta":{"content":"```json\n{","role":"assistant"}}]}', "",
             r'data: {"choices":[{"index":0,"delta":{"content":"}\n```"}}]}', "data: [DONE]"]
    assert streamed(lines) == "```json\n{}\n```"

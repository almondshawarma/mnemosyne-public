"""P3 pipeline: extract, verify gate, dedupe, generation parsing/retry, approval policy, and one lecture end to end
with fake Paperless / LLM / anki-core / embedder."""
import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf  # pyright: ignore[reportMissingTypeStubs]
import pytest

from mnemosyne.cards import CardIn, Schema
from mnemosyne.extract import pages, parse_title, slug
from mnemosyne.generate.llm import Content
from mnemosyne.generate.propose import Lecture, generate, golden_reference, parse
from mnemosyne.ingest.paperless import Doc
from mnemosyne.ledger import db, store
from mnemosyne.pipeline import Services, policy, process
from mnemosyne.verify import dedupe
from mnemosyne.verify.quantities import check, verify_card

ROOT = Path(__file__).resolve().parent.parent
ANCHORS = ROOT / "data" / "anchors.yaml"
GOLDEN = ROOT / ".claude" / "skills" / "mnemosyne-cards" / "golden"
TAG = "course::nuen201::l08-nuclear-binding-energy"


# ---------- extract ----------

@pytest.mark.parametrize(("title", "expected"), [
    ("NUEN 201 L05 - Lorentz Transformation and Relativistic Energy", ("NUEN201", 5, "Lorentz Transformation and Relativistic Energy")),
    ("PHYS-207 L6: Potential", ("PHYS207", 6, "Potential")),
    ("MEEN221 L13 Distributed Load (annotated)", ("MEEN221", 13, "Distributed Load (annotated)")),
])
def test_parse_title(title: str, expected: tuple[str, int, str]) -> None:
    ref = parse_title(title)
    assert ref is not None and (ref.course, ref.lecture, ref.title) == expected


def test_parse_title_rejects_other_documents() -> None:
    assert parse_title("Fall 2026 Syllabus") is None


def test_slug() -> None:
    assert slug("Quantum Theory of Light (in-class)") == "quantum-theory-of-light"


def make_pdf(*texts: str) -> bytes:
    doc: Any = pymupdf.open()  # pyright: ignore[reportUnknownMemberType]
    for t in texts:
        page: Any = doc.new_page()
        page.insert_text((72, 72), t)
    data = bytes(doc.tobytes())
    doc.close()
    return data


def test_pages_have_text_and_images() -> None:
    got = pages(make_pdf("binding energy per nucleon", "mass defect"), max_images=1, dpi=40)
    assert [p.number for p in got] == [1, 2]
    assert "binding energy" in got[0].text
    assert got[0].png is not None and got[0].png.startswith(b"\x89PNG") and got[1].png is None


# ---------- verify ----------

def test_every_golden_check_passes() -> None:
    checks = [k for p in GOLDEN.glob("*.json") for c in json.loads(p.read_text(encoding="utf-8"))["cards"]
              for k in c.get("checks", [])]
    assert len(checks) >= 5
    for c in checks:
        assert check(c, ANCHORS).status == "pass", c


@pytest.mark.parametrize(("entry", "status"), [
    ({"quantity": "12 V / 9.6585e-3 ohm", "value": "1242 A", "tolerance": "3%"}, "pass"),
    ({"quantity": "1 W / (200 MeV)", "value": "3.12e10 1/s", "tolerance": "3%"}, "pass"),
    ({"quantity": "Stefan-Boltzmann constant", "value": "5.670e-8 W/(m^2 K^4)", "tolerance": "0.1%"}, "pass"),
    ({"quantity": "1 u c^2", "value": "939.6 MeV", "tolerance": "0.05%"}, "fail"),
    ({"quantity": "m_p c^2", "value": "938.3 kg", "tolerance": "1%"}, "fail"),
    ({"quantity": "the vibe of the lecture", "value": "12 MeV", "tolerance": "1%"}, "skip"),
    ({"quantity": "17.589 MeV * alpha particle mass/(alpha particle mass + neutron mass)", "value": "14.05 MeV",
      "tolerance": "0.1%"}, "pass"),  # a name used twice is substituted both times
])
def test_checks(entry: dict[str, str], status: str) -> None:
    assert check(entry, ANCHORS).status == status


def test_numeric_card_without_checks_is_skipped() -> None:
    assert verify_card("M-Estimate", ["type::anchor"], [], ANCHORS).status == "skip"
    assert verify_card("M-Basic", ["type::definition"], [], ANCHORS).status == "pass"


# ---------- dedupe ----------

class BagEmbedder:
    """Deterministic bag-of-words vectors: same words, same vector; disjoint words, orthogonal-ish."""

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        out: list[list[float]] = []
        for t in texts:
            v = np.zeros(256, dtype=np.float32)
            for w in t.removeprefix("clustering: ").split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
            out.append((v + 1e-6).tolist())
        return out


def test_normalize() -> None:
    assert dedupe.normalize(r"<b>Energy</b> of \(1\) u&nbsp;") == "energy of 1 u"


def test_classify_duplicates_and_within_batch() -> None:
    emb = BagEmbedder()
    notes = {1: "binding energy per nucleon of iron 56"}
    con = db.connect(":memory:")
    db.migrate(con)
    ids, matrix = asyncio.run(dedupe.index(con, notes, emb))
    got = asyncio.run(dedupe.classify(["Binding energy per nucleon of iron 56", "why is fission exothermic",
                                       "why is fission exothermic"], ids, matrix, notes, emb))
    assert [m.kind for m in got] == ["duplicate", "new", "duplicate"]
    assert got[0].note_id == 1 and got[2].note_id is None
    before = emb.calls
    asyncio.run(dedupe.index(con, notes, emb))
    assert emb.calls == before, "unchanged notes aren't re-embedded"


# ---------- generation ----------

def card(front: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"note_type": "M-Basic", "deck": "Now", "mode": "audio",
                            "fields": {"Front": front, "Back": "b", "SpeakFront": front, "SpeakBack": "b",
                                       "Source": "NUEN201 L08 slide 3"},
                            "tags": [TAG, "topic::nuclear::binding", "type::result", "src::slides"]}
    base.update(over)
    return base


class ScriptedLLM:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[tuple[str, Content]] = []

    async def complete(self, route: str, system: str, content: Content) -> str:
        self.prompts.append((system, content))
        return self.replies.pop(0)


def fenced(cards: list[dict[str, Any]], skipped: list[dict[str, str]] | None = None) -> str:
    body = {"source": {"kind": "slides"}, "cards": cards, "skipped": skipped or []}
    return "Here you go.\n```mnemosyne-cards\n" + json.dumps(body) + "\n```"


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


def lecture(**over: Any) -> Lecture:
    base: dict[str, Any] = {"course": "NUEN201", "lecture": 8, "title": "Nuclear Binding Energy", "kind": "slides",
                                "ref": "NUEN 201 L08 - Nuclear Binding Energy", "link": "http://p/documents/9/details",
                                "emphasis": ["exam: binding energy curve"], "existing": ["mass defect definition"],
                                "pages": pages(make_pdf("page one"), dpi=40)}
    base.update(over)
    return Lecture(**base)


def test_parse_takes_the_fenced_block() -> None:
    assert parse(fenced([card("a")]))["cards"][0]["fields"]["Front"] == "a"


def test_generate_retries_once_with_the_problems(schema: Schema) -> None:
    bad = card("no tag", tags=["topic::nuclear::binding", "type::result"])
    llm = ScriptedLLM(fenced([card("good"), bad]), fenced([card("good"), card("fixed")], [{"what": "x", "why": "y"}]))
    got = asyncio.run(generate(llm, "lecture", "CANON", "", lecture(), schema))
    assert got.payload is not None and [c.fields["Front"] for c in got.payload.cards] == ["good", "fixed"]
    assert got.problems == [] and got.skipped == [{"what": "x", "why": "y"}]
    retry_text = llm.prompts[1][1][-1]["text"]
    assert "missing the lecture tag" in retry_text
    first_user = llm.prompts[0][1]
    assert any(part["type"] == "image_url" for part in first_user), "page images go to the model"
    assert "exam: binding energy curve" in first_user[0]["text"] and "mass defect definition" in first_user[0]["text"]
    assert "CANON" in llm.prompts[0][0]


def test_generate_reports_what_stays_invalid(schema: Schema) -> None:
    llm = ScriptedLLM("not json at all", "still not json")
    got = asyncio.run(generate(llm, "lecture", "CANON", "", lecture(), schema))
    assert got.payload is None and got.problems


def test_golden_reference_has_examples_and_anti_examples() -> None:
    ref = golden_reference(GOLDEN, "NUEN201")
    assert "Approved cards from" in ref and "REJECTED (" in ref


# ---------- policy ----------

def test_policy(schema: Schema) -> None:
    c = CardIn.model_validate(card("x"))
    new = dedupe.Match("new", 0.1, None, "")
    approval = {"auto_approve": ["slides"]}
    assert policy(c, "slides", ("pass", ""), new, False, approval).approved
    assert policy(c, "slides", ("pass", ""), new, False, approval).probation
    held = policy(c, "slides", ("fail", "1 u c^2 off"), dedupe.Match("flag", 0.88, 5, "similar"), True, approval)
    assert not held.approved and held.reason is not None
    for word in ("verify_failed", "dedupe_flagged", "calibration"):
        assert word in held.reason
    assert not policy(c, "graded-homework", ("pass", ""), new, False, approval).approved


# ---------- one lecture end to end ----------

class FakePaperless:
    def __init__(self) -> None:
        self.tagged: list[tuple[int, int]] = []

    async def download(self, doc_id: int) -> bytes:
        return make_pdf("Binding energy per nucleon peaks near iron-56")

    async def notes(self, doc_id: int) -> list[str]:
        return ["professor: this curve is on the exam"]

    def link(self, doc_id: int) -> str:
        return f"http://paperless/documents/{doc_id}/details"


class FakeCore:
    """Notes are {id: first field}. `notes` gives some of them a full note (type, fields, tags); the rest are
    M-Basic notes homed in topic::math, the most fundamental home, so they win every merge unless a test says not."""

    def __init__(self, existing: dict[int, str], notes: dict[int, dict[str, Any]] | None = None) -> None:
        self.existing = existing
        self.notes = notes or {}
        self.tagged: list[dict[str, Any]] = []
        self.untagged: list[dict[str, Any]] = []
        self.upserted: list[dict[str, Any]] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "anki_notes_add_tags":
            self.tagged.append(arguments)
            return {"state": "committed"}
        if tool == "anki_notes_remove_tags":
            self.untagged.append(arguments)
            return {"state": "committed"}
        if tool == "anki_notes_get":
            nid = int(arguments["note_id"])
            return {"id": nid, "note_type_name": "M-Basic", "tags": ["topic::math"], "card_ids": [nid * 10],
                    "fields": [{"name": "Front", "value": self.existing.get(nid, "")}], **self.notes.get(nid, {})}
        if tool == "anki_cards_get":
            return {"id": arguments["card_id"], "deck_name": "Core", "review_summary": {}}
        assert tool == "anki_notes_search"
        items = [{"id": k, "first_field": v} for k, v in self.existing.items()]
        if arguments["query"].startswith("tag:"):
            items = []
        return {"items": items, "has_more": False, "total": len(items)}

    async def upsert(self, notes: list[dict[str, Any]], idempotency_key: str) -> dict[str, Any]:
        self.upserted.extend(notes)
        return {"result": "updated"}

    async def status(self) -> dict[str, Any]:
        return {}


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


def services(con: sqlite3.Connection, schema: Schema, llm: ScriptedLLM, existing: dict[int, str]) -> Services:
    svc = Services(con, schema, FakeCore(existing), llm, BagEmbedder(), FakePaperless(), ROOT,  # type: ignore[arg-type]
                   ROOT / ".claude" / "skills" / "mnemosyne-cards")
    return svc


def estimate(front: str, value: str) -> dict[str, Any]:
    return {"note_type": "M-Estimate", "deck": "Core", "mode": "audio",
            "fields": {"Question": front, "Answer": value, "Anchor": "1 u c^2", "SpeakQuestion": front,
                       "SpeakAnswer": value},
            "tags": [TAG, "topic::nuclear::mass-energy", "type::anchor", "src::slides"],
            "checks": [{"quantity": "1 u c^2", "value": value, "tolerance": "0.05%"}]}


def test_lecture_end_to_end(con: sqlite3.Connection, schema: Schema) -> None:
    doc = Doc(9, "NUEN 201 L08 - Nuclear Binding Energy", frozenset({3}), 1, "2026-09-26")
    cards = [card("Where does binding energy per nucleon peak?"), card("Mass defect definition, in words"),
             estimate("Energy of 1 u in MeV", "931.494 MeV"), estimate("Energy of 1 u, again", "939.6 MeV")]
    existing = {77: "mass defect definition, in words"}

    first = asyncio.run(process(services(con, schema, ScriptedLLM(fenced(cards)), existing), doc, {3: "NUEN201"}))
    assert first.duplicates == 1 and first.created == 3
    assert first.approved == 0 and first.reasons.get("calibration") == 3, "first lecture per course: all reviewed"
    drafts = store.batch_drafts(con, first.batch_id or "")
    assert all(d.fields["SourceLink"] == "http://paperless/documents/9/details" for d in drafts)
    assert all("status::probation" not in d.tags for d in drafts)

    doc2 = Doc(10, "NUEN 201 L09 - Fission", frozenset({3}), 1, "2026-09-27")
    tag9 = "course::nuen201::l09-fission"
    later = [{**c, "tags": [tag9, *c["tags"][1:]]} for c in
             [card("Why is fission of U-235 exothermic?"), estimate("1 u in MeV (4 sig figs)", "931.494 MeV"),
              estimate("1 u in MeV, wrong", "939.6 MeV")]]
    second = asyncio.run(process(services(con, schema, ScriptedLLM(fenced(later)), existing), doc2, {3: "NUEN201"}))
    assert (second.approved, second.review) == (2, 1)
    assert second.reasons == {"verify_failed": 1}
    got = {d.fields.get("Front") or d.fields.get("Question"): d for d in store.batch_drafts(con, second.batch_id or "")}
    assert got["1 u in MeV, wrong"].status == "pending"
    assert "status::probation" in got["Why is fission of U-235 exothermic?"].tags


# ---------- intake (iPad Shortcut) ----------

class UploadPaperless(FakePaperless):
    def __init__(self) -> None:
        super().__init__()
        self.uploads: list[dict[str, Any]] = []
        self.ids = {"NUEN-201": 40, "Fall-26": 45, "mnemosyne-done": 90, "mnemosyne-error": 91, "mnemosyne-skip": 92,
                    "mnemosyne": 93, "graded": 94}

    async def tag_id(self, name: str, create: bool = False) -> int | None:
        return self.ids.get(name)

    async def document_type_id(self, name: str) -> int | None:
        return {"Lecture": 1, "Syllabus": 2, "Assignment": 3, "Exam": 4}.get(name)

    async def upload(self, pdf: bytes, filename: str, title: str, tags: list[int], document_type: int | None) -> str:
        self.uploads.append({"title": title, "tags": tags, "type": document_type})
        return "task-1"

    async def documents(self, **query: Any) -> list[Doc]:
        return []


@pytest.mark.parametrize(("kind", "number", "title", "tags", "doc_type"), [
    ("slides", 8, "NUEN 201 L08 - Nuclear Binding Energy", [40, 45], 1),
    ("syllabus", None, "NUEN 201 Syllabus - Fall 2026", [40, 45, 93], 2),
    ("graded-homework", 3, "NUEN 201 HW03 - Decay chains", [40, 45, 93, 94], 3),
])
def test_intake_files_with_pipeline_metadata(con: sqlite3.Connection, schema: Schema, kind: str, number: int | None,
                                             title: str, tags: list[int], doc_type: int) -> None:
    from mnemosyne.pipeline import intake
    svc = services(con, schema, ScriptedLLM(), {})
    svc.paperless = UploadPaperless()  # type: ignore[assignment]
    name = {"slides": "Nuclear Binding Energy", "syllabus": "Fall 2026", "graded-homework": "Decay chains"}[kind]
    out = asyncio.run(intake(svc, b"%PDF-1.7", "x.pdf", "NUEN201", number, name, kind))
    assert out["title"] == title
    upload = svc.paperless.uploads[0]  # type: ignore[attr-defined]
    assert upload == {"title": title, "tags": tags, "type": doc_type}
    if kind == "slides":
        ref = parse_title(title)
        assert ref is not None and (ref.course, ref.lecture) == ("NUEN201", 8), "the poller can read what intake wrote"


@pytest.mark.parametrize(("course", "kind", "number", "title", "expected"), [
    ("PHYS221", "slides", None, "PHYS 221 L02 - Kinetic Theory of Gases", "PHYS 221 L02 - Kinetic Theory of Gases"),
    ("PHYS221", "slides", 2, "PHYS 221 L02 - Kinetic Theory of Gases", "PHYS 221 L02 - Kinetic Theory of Gases"),
    ("MATH309", "slides", 5, "Eigenvalues", "MATH 309 L05 - Eigenvalues"),
    ("MATH309", "slides", None, "MATH309 Lecture 6: Diagonalization", "MATH 309 L06 - Diagonalization"),
    ("NUEN201", "graded-homework", 3, "NUEN-201 HW3 Decay chains", "NUEN 201 HW03 - Decay chains"),
    ("NUEN201", "syllabus", None, "NUEN 201 Syllabus Fall 2026", "NUEN 201 Syllabus - Syllabus Fall 2026"),
])
def test_paperless_title_never_repeats_the_course(course: str, kind: str, number: int | None, title: str,
                                                  expected: str) -> None:
    from mnemosyne.pipeline import paperless_title
    assert paperless_title(course, kind, number, title) == expected


def test_intake_fields_fall_back_to_the_file_name() -> None:
    from mnemosyne.api import intake_fields
    codes = {"MATH309", "PHYS221"}
    assert intake_fields("Course", "Number", "Title", "kind", "MATH 309 L03 - Vector Spaces", codes) == (
        "MATH309", None, "MATH 309 L03 - Vector Spaces", "slides")
    assert intake_fields("PHYS221", "2", "Kinetic Theory", "notes", "whatever", codes) == (
        "PHYS221", 2, "Kinetic Theory", "notes")


def test_a_file_named_for_another_course_is_refused() -> None:
    from mnemosyne.api import course_mismatch
    codes = {"MATH309", "NUEN201", "none"}
    assert course_mismatch("NUEN201", "MATH 309 L07 - Matrices of Transformations", codes) is not None
    assert course_mismatch("MATH309", "MATH 309 L07 - Matrices of Transformations", codes) is None
    assert course_mismatch("NUEN201", "Compton scattering", codes) is None, "a file name without a course is fine"
    assert course_mismatch("NUEN201", "CHEM 107 L02 - Moles", codes) is None, "nor one naming a course not in use"


# ---------- replacing an earlier upload ----------

def test_same_document() -> None:
    from mnemosyne.pipeline import same_document
    assert same_document("MATH 309 L03 - Vector Spaces", "MATH 309 L03 - Vector spaces (annotated)")
    assert not same_document("MATH 309 L03 - Vector Spaces", "MATH 309 L04 - Basis")
    assert not same_document("MATH 309 L03 - Vector Spaces", "PHYS 221 L03 - First Law")
    assert same_document("NUEN 201 Syllabus - Fall 2026", "nuen 201 syllabus - fall 2026")


class ReplacingPaperless(UploadPaperless):
    def __init__(self, task: tuple[str, int | None, str], old_tags: frozenset[int]) -> None:
        super().__init__()
        self.task_result = task
        self.old_tags = old_tags
        self.events: list[tuple[str, int, Any]] = []

    async def task(self, task_id: str) -> tuple[str, int | None, str]:
        return self.task_result

    async def document(self, doc_id: int) -> Doc:
        return Doc(doc_id, "MATH 309 L03 - Vector Spaces", self.old_tags, 1, "2026-09-14")

    async def notes(self, doc_id: int) -> list[str]:
        return ["professor: subspace test is on the exam"] if doc_id == 420 else []

    async def add_note(self, doc_id: int, note: str) -> None:
        self.events.append(("note", doc_id, note))

    async def add_tag(self, doc_id: int, tag: int) -> None:
        self.events.append(("tag", doc_id, tag))

    async def remove_tag(self, doc_id: int, tag: int) -> None:
        self.events.append(("untag", doc_id, tag))

    async def trash(self, doc_id: int) -> None:
        self.events.append(("trash", doc_id, None))


def test_new_version_replaces_the_old_one(con: sqlite3.Connection, schema: Schema) -> None:
    from mnemosyne.pipeline import settle_upload
    svc = services(con, schema, ScriptedLLM(), {})
    fake = ReplacingPaperless(("success", 446, ""), frozenset({40, 45, 90, 7}))   # 90 = mnemosyne-done, 7 = a user tag
    svc.paperless = fake  # type: ignore[assignment]
    out = asyncio.run(settle_upload(svc, "t", [420], wait_s=0))
    assert out == {"document": 446, "replaced": [420], "title": "MATH 309 L03 - Vector Spaces"}
    assert ("note", 446, "professor: subspace test is on the exam") in fake.events
    assert {e[2] for e in fake.events if e[0] == "tag"} == {40, 45, 7}, "pipeline tags stay behind"
    assert fake.events[-1] == ("trash", 420, None), "the old version goes to the trash last"


def test_identical_file_reruns_a_document_that_never_finished(con: sqlite3.Connection, schema: Schema) -> None:
    from mnemosyne.pipeline import record_job, settle_upload
    record_job(con, 420, "error", 2)
    svc = services(con, schema, ScriptedLLM(fenced([card("A new card for L03", tags=[
        "course::math309::l03-vector-spaces", "topic::math::linear-algebra", "type::definition", "src::slides"])])),
        {})
    fake = ReplacingPaperless(("failure", None, "{'error': 'Not consuming x.pdf: It is a duplicate of Vector Spaces (#420)'}"),
                              frozenset({4, 45, 90}))
    fake.ids["MATH-309"] = 4
    svc.paperless = fake  # type: ignore[assignment]
    out = asyncio.run(settle_upload(svc, "t", [420], wait_s=0))
    assert out["duplicate_of"] == 420 and out["outcome"] is not None and out["outcome"].created == 1
    assert ("untag", 420, 91) in fake.events and ("tag", 420, 90) in fake.events, "error tag cleared, then done"


def test_a_card_with_a_question_waits_for_aman() -> None:
    c = CardIn.model_validate({**card("x"), "question": "Is your slide-6 note right?"})
    v = policy(c, "slides", ("pass", ""), dedupe.Match("new", 0.1, None, ""), False, {"auto_approve": ["slides"]})
    assert not v.approved and v.reason == "question: Is your slide-6 note right?"


def test_held_batches_keep_suspected_duplicates_for_aman(con: sqlite3.Connection, schema: Schema) -> None:
    from mnemosyne.cards import Payload
    from mnemosyne.pipeline import check_and_propose

    svc = services(con, schema, ScriptedLLM(""), {7: "Energy equivalent of 1 u"})
    cards = [estimate("Energy equivalent of 1 u", "931.5 MeV"), estimate("Rest energy of a proton", "938.3 MeV")]
    payload = Payload.model_validate({"source": {"kind": "slides", "course": "NUEN201", "lecture": 6}, "cards": cards})
    dropped = asyncio.run(check_and_propose(svc, payload, "claude", "slides", False))
    assert dropped.duplicates == 1 and dropped.dropped, "outside a review, duplicates are dropped"
    both = [TAG, "topic::nuclear::mass-energy"]
    assert dropped.adopted == {7: both}, "…and the note already in Anki takes this course's tag and connections"
    assert svc.core.tagged[0]["note_ids"] == [7] and svc.core.tagged[0]["tags"] == both  # type: ignore[attr-defined]
    con.execute("DELETE FROM card_draft")
    held = asyncio.run(check_and_propose(svc, Payload.model_validate(
        {"source": {"kind": "notes", "course": "NUEN201", "lecture": 6}, "cards": cards}), "review", "notes", False, hold=True))
    assert (held.duplicates, held.created, held.approved, held.dropped, held.adopted) == (0, 2, 0, {}, {})
    reasons = [r["review_reason"] or "" for r in con.execute("SELECT review_reason FROM card_draft ORDER BY id")]
    assert reasons[0].startswith("dedupe_duplicate (")


def test_a_card_waiting_for_review_absorbs_its_duplicate_from_another_course(con: sqlite3.Connection,
                                                                           schema: Schema) -> None:
    """PHYS 207 L07 and PHYS 217 L06 both carded capacitors a day apart (2026-09-28/29): the second must not queue
    the same cards again, but hand its course tag to the waiting ones."""
    from mnemosyne.cards import Payload
    from mnemosyne.pipeline import check_and_propose

    svc = services(con, schema, ScriptedLLM(""), {})
    first = Payload.model_validate({"source": {"kind": "slides", "course": "PHYS207", "lecture": 7},
                                    "cards": [card("Energy stored in a capacitor, given C and V")]})
    made = asyncio.run(check_and_propose(svc, first, "paperless", "slides", True))
    assert made.created == 1
    other = card("Energy stored in a capacitor, given C and V")
    other["tags"] = ["course::phys217::l06-capacitance", "topic::physics::em::capacitance",
                     "principle::energy-storage", "type::result", "src::slides"]
    second = asyncio.run(check_and_propose(svc, Payload.model_validate(
        {"source": {"kind": "slides", "course": "PHYS217", "lecture": 6}, "cards": [other]}), "paperless", "slides",
        True))
    assert (second.created, second.duplicates) == (0, 1)
    [draft_id] = second.adopted
    assert draft_id < 0, "a draft, not an Anki note"
    tags = json.loads(con.execute("SELECT tags_json FROM card_draft").fetchone()["tags_json"])
    assert {"course::phys217::l06-capacitance", "principle::energy-storage"} <= set(tags)
    assert not svc.core.tagged, "nothing goes to Anki for a card that isn't there yet"  # type: ignore[attr-defined]


class TaggingPaperless(FakePaperless):
    async def add_tag(self, doc_id: int, tag: int) -> None:
        self.tagged.append((doc_id, tag))

    async def remove_tag(self, doc_id: int, tag: int) -> None:
        pass


class FailingLLM:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def complete(self, route: str, system: str, content: Content) -> str:
        raise self.error


def test_a_throttled_gateway_doesnt_use_up_a_lectures_tries(con: sqlite3.Connection, schema: Schema) -> None:
    from mnemosyne.generate.llm import LLMError, RateLimited, rate_limited
    from mnemosyne.pipeline import Tags, job_attempts, run_one
    assert rate_limited(400, '{"detail":"🚫 **Rate Limit Reached** ... *Error Code: 429*"}'), "the gateway's 400"
    assert rate_limited(429, "") and not rate_limited(400, "bad request: image too large")
    tags = Tags({5: "MATH309"}, 1, 2, 90, 91, [90, 91], 70)
    doc = Doc(470, "MATH 309 L03 - Vector Spaces", frozenset({5}), 2, "2026-10-07")
    svc = services(con, schema, ScriptedLLM(), {})
    fake = TaggingPaperless()
    svc.paperless = fake  # type: ignore[assignment]
    svc.llm = FailingLLM(RateLimited("lecture via gateway: HTTP 400: Rate Limit Reached"))  # type: ignore[assignment]
    for _ in range(3):
        asyncio.run(run_one(svc, doc, tags))
    assert job_attempts(con, 470) == ("error", 0) and fake.tagged == [], "throttling never counts as a try"
    svc.llm = FailingLLM(LLMError("lecture via gateway: HTTP 400: bad image"))  # type: ignore[assignment]
    asyncio.run(run_one(svc, doc, tags))
    asyncio.run(run_one(svc, doc, tags))
    assert job_attempts(con, 470) == ("error", 2) and fake.tagged == [(470, 91)], "a real failure still does"
    fake.tagged.clear()
    svc.llm = FailingLLM(RateLimited("lecture via gateway: HTTP 400: Rate Limit Reached"))  # type: ignore[assignment]
    asyncio.run(run_one(svc, doc, tags))
    assert job_attempts(con, 470) == ("error", 2) and fake.tagged == [], "out of tries, throttling still doesn't tag"


def test_after_a_lasting_rate_limit_the_router_waits_instead_of_asking(tmp_path: Path) -> None:
    import time

    from mnemosyne.generate.llm import RateLimited, Router
    (tmp_path / "url").write_text("http://gateway.invalid/v1")
    (tmp_path / "key").write_text("k")
    (tmp_path / "llm.yaml").write_text(
        f"providers:\n  tamu: {{kind: openai, base_url_file: '{(tmp_path / 'url').as_posix()}',"
        f" api_key_file: '{(tmp_path / 'key').as_posix()}'}}\n"
        "routes:\n  lecture: {provider: tamu, model: m}\n")
    router = Router(tmp_path / "llm.yaml")
    router.cooldown_until = time.monotonic() + 60
    with pytest.raises(RateLimited):
        asyncio.run(router.complete("lecture", "s", []))   # no network: the cooldown answers first


class LongDeckPaperless(TaggingPaperless):
    async def download(self, doc_id: int) -> bytes:
        return make_pdf(*[f"Slide {i}: Kirchhoff's rules, part {i}" for i in range(1, 31)])

    async def document(self, doc_id: int) -> Doc:
        return Doc(doc_id, "NUEN 201 L08 - Nuclear Binding Energy", frozenset({3}), 2, "2026-10-07")

    async def tag_id(self, name: str, create: bool = False) -> int | None:
        from mnemosyne.ingest.paperless import DONE, ERROR
        return {"NUEN-201": 3, "Fall-26": 1, DONE: 90, ERROR: 91}.get(name, 70)

    async def document_type_id(self, name: str) -> int | None:
        return {"Lecture": 2, "Exam": 4, "Assignment": 5, "Paper": 6}.get(name)


def test_a_long_deck_is_carded_in_parts_then_a_session_can_settle_one(con: sqlite3.Connection, schema: Schema) -> None:
    from mnemosyne.pipeline import PipelineError, job_attempts, record_job, settle, stuck
    doc = Doc(470, "NUEN 201 L08 - Nuclear Binding Energy", frozenset({3}), 2, "2026-10-07")
    llm = ScriptedLLM(fenced([card("Where does binding energy per nucleon peak?")]),
                      fenced([card("Mass defect definition, in words")]))
    svc = services(con, schema, llm, {})
    svc.paperless = LongDeckPaperless()  # type: ignore[assignment]
    out = asyncio.run(process(svc, doc, {3: "NUEN201"}))
    assert len(llm.prompts) == 2 and out.created == 2, "30 slides at 24 a request: two requests, one batch"
    assert "pages 25-30 of 30" in llm.prompts[1][1][0]["text"]  # type: ignore[index]
    assert "binding energy per nucleon" in llm.prompts[1][1][0]["text"], "part 2 sees part 1's cards"  # type: ignore[index]

    # A lecture the gateway couldn't take, carded in a session instead
    record_job(con, 471, "error", 2, "lecture via gateway: HTTP 400: Rate Limit Reached")
    record_job(con, 458, "error", 2, "lecture via gateway: HTTP 524")        # an older copy, since replaced
    con.execute("INSERT INTO doc_version (doc_id, replaces, at) VALUES (471, 458, '2026-10-07')")
    record_job(con, 455, "error", 2, "can't tell course and lecture")        # a review sheet the prep job finished
    record_job(con, 455, "done", 1, kind="prep")
    assert [(s["doc_id"], s["waiting_on_gateway"]) for s in stuck(con)] == [(471, True)]
    with pytest.raises(PipelineError):
        asyncio.run(settle(svc, 471))   # nothing proposed for it yet: settling can't skip a lecture
    con.execute("INSERT INTO card_source (payload_hash, origin, kind, course, lecture, ref, link, received_at)"
                " VALUES ('s', 'mcp', 'slides', 'NUEN201', 8, 'NUEN 201 L08', 'http://paperless/documents/471/details',"
                " '2026-10-07')")
    svc.paperless = LongDeckPaperless()  # type: ignore[assignment]
    got = asyncio.run(settle(svc, 471))
    assert got["settled"] and job_attempts(con, 471) == ("done", 2) and stuck(con) == []
    assert con.execute("SELECT COUNT(*) FROM deck_page WHERE doc_id = 471").fetchone()[0] == 30, "a re-send cards only what's new"
    assert (471, 90) in svc.paperless.tagged  # type: ignore[attr-defined]


class CutOnceLLM(ScriptedLLM):
    """The first reply hits max_tokens (a dense 24-slide part); everything after fits."""

    async def complete(self, route: str, system: str, content: Content) -> str:
        from mnemosyne.generate.llm import Truncated
        if not self.prompts:
            self.prompts.append((system, content))
            raise Truncated("lecture via gateway: the reply hit max_tokens 16000")
        return await super().complete(route, system, content)


def test_a_part_whose_reply_is_cut_off_is_carded_in_halves(con: sqlite3.Connection, schema: Schema) -> None:
    doc = Doc(507, "NUEN 201 L08 - Nuclear Binding Energy", frozenset({3}), 2, "2026-10-20")
    llm = CutOnceLLM(*[fenced([card(f"Card from part {i}")]) for i in range(3)])
    svc = services(con, schema, llm, {})
    svc.paperless = LongDeckPaperless()  # type: ignore[assignment]
    out = asyncio.run(process(svc, doc, {3: "NUEN201"}))
    labels = [next(c["text"] for c in p[1] if c["type"] == "text").split("\n")[0] for p in llm.prompts]
    assert len(llm.prompts) == 4 and out.created == 3, "24 slides cut off: two halves of 12, then the last 6"
    texts = [" ".join(c.get("text", "") for c in p[1] if c["type"] == "text") for p in llm.prompts]
    assert "pages 1-12 of 30" in texts[1] and "pages 13-24 of 30" in texts[2] and "pages 25-30 of 30" in texts[3], labels


def test_a_streamed_reply_cut_by_max_tokens_is_seen() -> None:
    from mnemosyne.generate.llm import stream_reply
    lines = ['data: {"choices": [{"delta": {"content": "1, 2,"}}]}',
             'data: {"choices": [{"delta": {"content": ""}, "finish_reason": "length"}]}',
             'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}', "data: [DONE]"]
    assert stream_reply(lines) == ("1, 2,", {"length", "stop"}), "the gateway closes with stop even after a cut"

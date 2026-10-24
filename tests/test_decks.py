"""A deck sent again, with more notes on it or reused for the next lecture: it replaces its old copy in Paperless,
its cards and questions follow, and only what changed is carded."""
import asyncio
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf  # pyright: ignore[reportMissingTypeStubs]
import pytest

from mnemosyne import questions as qstore
from mnemosyne.cards import Schema
from mnemosyne.extract import fingerprint, parse_title, same_deck, unseen
from mnemosyne.ingest.paperless import Doc
from mnemosyne.ledger import db, decks, store
from mnemosyne.pipeline import Services, process, record_job, same_copy, settle_upload, span_title
from mnemosyne.project import obsidian
from test_pipeline import ROOT, BagEmbedder, FakeCore, ScriptedLLM, UploadPaperless, card, fenced

SLIDES = [
    "Capacitance is the charge stored per volt across two conductors, C equals Q over V",
    "A parallel plate capacitor has C equal to epsilon zero times area over plate separation",
    "Capacitors in parallel share the voltage and their capacitances add directly",
    "Capacitors in series share the charge and their reciprocal capacitances add",
    "The energy stored in a charged capacitor is one half C V squared, held in the field",
]


def deck(slides: list[str], ink: dict[int, str] | None = None, scribble: set[int] | None = None) -> bytes:
    """Slides with printed text; `ink` writes on a page in red (like Aman's pen), `scribble` makes a page with no text
    layer (handwritten notes)."""
    doc: Any = pymupdf.open()  # pyright: ignore[reportUnknownMemberType]
    for i, t in enumerate(slides, start=1):
        page: Any = doc.new_page(width=720, height=405)
        if i in (scribble or set()):
            page.draw_line((50, 50), (600, 300), color=(0, 0, 0), width=3)
            page.draw_circle((300, 200), 60 + 10 * i, color=(0, 0, 0), width=3)
            continue
        page.insert_text((40, 60), t, fontsize=14)
        if ink and i in ink:
            page.draw_line((40, 300), (400, 330), color=(1, 0, 0), width=3)
            page.insert_text((40, 360), ink[i], fontsize=22, color=(1, 0, 0))
    data = bytes(doc.tobytes())
    doc.close()
    return data


# ---------- fingerprints ----------

def test_titles_can_span_lectures() -> None:
    ref = parse_title("PHYS 207 L07-L08 - Capacitors")
    assert ref is not None and (ref.course, ref.lecture, ref.title, list(ref.span)) == ("PHYS207", 8, "Capacitors", [7, 8])
    plain = parse_title("MEEN 221 L15 - 3D Rigid Body Equilibrium")
    assert plain is not None and (plain.lecture, plain.title, list(plain.span)) == (15, "3D Rigid Body Equilibrium", [15])
    assert span_title(["PHYS 207 L08 - Capacitors (annotated)", "PHYS 207 L07 - Capacitors"]) == \
        "PHYS 207 L07-L08 - Capacitors (annotated)"
    assert span_title(["PHYS 207 L09 - Dielectrics", "PHYS 207 L07-L08 - Capacitors"]) == "PHYS 207 L07-L09 - Dielectrics"


def test_only_new_and_newly_marked_pages_are_unseen() -> None:
    v1 = fingerprint(deck(SLIDES))
    assert unseen(fingerprint(deck(SLIDES)), v1) == [], "the same file again: nothing new"
    v2 = fingerprint(deck([*SLIDES, "Dielectrics raise capacitance by the factor kappa, the dielectric constant"],
                          ink={2: "why eps0?"}))
    assert unseen(v2, v1) == [2, 6], "page 2 has new writing, page 6 is new"
    assert unseen(v2, [*v1, *v2]) == [], "once a marked copy is carded, it's seen too"
    assert same_deck(v1, v2)
    other = fingerprint(deck(["Gauss's law says the flux through a closed surface is the enclosed charge over eps0",
                              "A conductor in equilibrium has zero field inside and charge on its surface",
                              "The field just outside a conductor is sigma over epsilon zero, normal to it"]))
    assert not same_deck(v1, other)


def test_handwritten_pages_compare_by_position_with_the_copy_they_replace() -> None:
    notes = ["", "", ""]
    v1 = fingerprint(deck(notes, scribble={1, 2, 3}))
    assert all(not m.key for m in v1)
    assert unseen(v1, [], previous=v1) == []
    v2 = fingerprint(deck([*notes, ""], scribble={1, 2, 3, 4}))
    assert unseen(v2, [], previous=v1) == [4]
    assert unseen(v2, []) == [1, 2, 3, 4], "no earlier copy: all of it"


def test_slides_and_notes_are_never_copies_of_each_other() -> None:
    slides, notes = fingerprint(deck(SLIDES)), fingerprint(deck(["", "", ""], scribble={1, 2, 3}))
    assert not same_copy("PHYS 207 L07 - Capacitors", slides, "PHYS 207 L07 - My notes", notes)
    assert same_copy("PHYS 207 L07 - My notes v2", notes, "PHYS 207 L07 - My notes", notes)
    assert not same_copy("PHYS 207 L08 - My notes", notes, "PHYS 207 L07 - My notes", notes)
    assert same_copy("PHYS 207 L08 - Whatever it was called", slides, "PHYS 207 L07 - Capacitors", slides)


# ---------- the pipeline ----------

TAG7 = "course::phys207::l07-capacitors"
TAG8 = "course::phys207::l08-capacitors"


def cap_card(front: str, tag: str = TAG7) -> dict[str, Any]:
    c = card(front)
    return {**c, "fields": {**c["fields"], "Source": "PHYS207 L07"},
            "tags": [tag, "topic::physics::em::capacitance", "type::definition", "src::slides"]}


class DeckPaperless(UploadPaperless):
    """Paperless with real PDFs per document, and a record of what changed."""

    def __init__(self, docs: dict[int, tuple[str, bytes]]) -> None:
        super().__init__()
        self.ids["PHYS-207"] = 41
        self.docs = docs
        self.events: list[tuple[str, int, Any]] = []
        self.task_result: tuple[str, int | None, str] = ("success", None, "")

    async def download(self, doc_id: int) -> bytes:
        return self.docs[doc_id][1]

    async def document(self, doc_id: int) -> Doc:
        return Doc(doc_id, self.docs[doc_id][0], frozenset({41, 45, 90} if doc_id < 500 else {41, 45}), 1,
                   "2026-09-28")

    async def documents(self, **query: Any) -> list[Doc]:
        return [await self.document(i) for i in sorted(self.docs)]

    async def notes(self, doc_id: int) -> list[str]:
        return ["prof: on the exam"] if doc_id < 500 else []

    async def task(self, task_id: str) -> tuple[str, int | None, str]:
        return self.task_result

    async def add_note(self, doc_id: int, note: str) -> None:
        self.events.append(("note", doc_id, note))

    async def add_tag(self, doc_id: int, tag: int) -> None:
        self.events.append(("tag", doc_id, tag))

    async def remove_tag(self, doc_id: int, tag: int) -> None:
        self.events.append(("untag", doc_id, tag))

    async def set_title(self, doc_id: int, title: str) -> None:
        self.events.append(("title", doc_id, title))
        self.docs[doc_id] = (title, self.docs[doc_id][1])

    async def trash(self, doc_id: int) -> None:
        self.events.append(("trash", doc_id, None))


class RecordingCore(FakeCore):
    def __init__(self) -> None:
        super().__init__({})
        self.updates: list[dict[str, Any]] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "anki_notes_update_fields":
            self.updates.append(arguments)
            return {"state": "committed"}
        return await super().call(tool, arguments)


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


def make(con: sqlite3.Connection, schema: Schema, llm: ScriptedLLM, paperless: DeckPaperless,
         notes_dir: Path | None = None) -> Services:
    return Services(con, schema, RecordingCore(), llm, BagEmbedder(), paperless, ROOT,  # type: ignore[arg-type]
                    ROOT / ".claude" / "skills" / "mnemosyne-cards", notes_dir=notes_dir)


def pages_sent(llm: ScriptedLLM) -> list[int]:
    content = json.dumps(llm.prompts[-1][1], default=str)
    return [n for n in range(1, 20) if f"--- page {n} ---" in content]


def test_a_deck_reused_for_the_next_lecture_replaces_its_copy_and_cards_only_what_changed(
        con: sqlite3.Connection, schema: Schema, tmp_path: Path) -> None:
    v1 = deck(SLIDES, ink={1: "card idea"})
    paperless = DeckPaperless({420: ("PHYS 207 L07 - Capacitors", v1)})
    first = ScriptedLLM(fenced([cap_card("Capacitance, defining relation")]))
    svc = make(con, schema, first, paperless, notes_dir=tmp_path)
    doc = asyncio.run(paperless.document(420))
    out = asyncio.run(process(svc, doc, {41: "PHYS207"}))
    assert out.pages == 5 and pages_sent(first) == [1, 2, 3, 4, 5]
    record_job(con, 420, "done", 1)
    store.mark_written(con, {d.mnemosyne_id: 9001 for d in store.batch_drafts(con, out.batch_id or "")})
    qstore.save_from_lecture(con, 420, "PHYS207", 7, "PHYS 207 L07 - Capacitors", "2026-09-28",
                             [{"asked": "why eps0?", "kind": "clarify", "page": 2}])
    (tmp_path / "PHYS 207 L07 - Capacitors.md").write_text(
        "---\npaperless_id: 420\n---\n# top\n\n<!-- HELIOS:below-is-yours — kept -->\n\n## Notes\n- \n", encoding="utf-8")
    obsidian.sync_doc(con, tmp_path, 420)
    assert "why eps0?" in (tmp_path / "PHYS 207 L07 - Capacitors.md").read_text(encoding="utf-8")

    # The same deck, now with writing on slide 4 and a new slide, sent as L08
    v2 = deck([*SLIDES, "Dielectrics raise capacitance by the factor kappa, the dielectric constant"],
              ink={1: "card idea", 4: "why reciprocal?"})
    paperless.docs[501] = ("PHYS 207 L08 - Capacitors", v2)
    paperless.task_result = ("success", 501, "")
    settled = asyncio.run(settle_upload(svc, "t", [], pdf=v2, course="PHYS207", title="PHYS 207 L08 - Capacitors",
                                        wait_s=0))
    assert settled == {"document": 501, "replaced": [420], "title": "PHYS 207 L07-L08 - Capacitors"}
    assert ("title", 501, "PHYS 207 L07-L08 - Capacitors") in paperless.events
    assert ("note", 501, "prof: on the exam") in paperless.events
    assert paperless.events[-1] == ("trash", 420, None)
    core: RecordingCore = svc.core  # type: ignore[assignment]
    assert core.updates == [{"note_id": 9001, "fields": {"SourceLink": "http://paperless/documents/501/details"},
                             "idempotency_key": "mn-relink-9001-501"}], "the written card follows the new copy"
    moved = qstore.for_doc(con, 501)
    assert [q["asked"] for q in moved] == ["why eps0?"] and moved[0]["lecture_title"] == "PHYS 207 L07-L08 - Capacitors"
    old_stub = (tmp_path / "PHYS 207 L07 - Capacitors.md").read_text(encoding="utf-8")
    assert "mnemosyne:start" not in old_stub and "why eps0?" not in old_stub, "the old copy's stub lets go of them"

    second = ScriptedLLM(fenced([cap_card("Series capacitors: why reciprocals add", TAG8)]))
    svc.llm = second  # type: ignore[assignment]
    out2 = asyncio.run(process(svc, asyncio.run(paperless.document(501)), {41: "PHYS207"}))
    assert pages_sent(second) == [4, 6], "only the newly marked slide and the new one"
    assert "carded before, from an earlier copy" in json.dumps(second.prompts[-1][1], default=str)
    assert out2.pages == 2 and out2.created == 1
    assert decks.predecessor(con, 501) == 420


def test_an_identical_copy_adds_nothing(con: sqlite3.Connection, schema: Schema) -> None:
    v1 = deck(SLIDES)
    paperless = DeckPaperless({420: ("PHYS 207 L07 - Capacitors", v1), 501: ("PHYS 207 L08 - Capacitors", v1)})
    svc = make(con, schema, ScriptedLLM(fenced([cap_card("Capacitance, defining relation")])), paperless)
    asyncio.run(process(svc, asyncio.run(paperless.document(420)), {41: "PHYS207"}))
    record_job(con, 420, "done", 1)
    svc.llm = ScriptedLLM()  # type: ignore[assignment]   # no replies: a call would fail the test
    out = asyncio.run(process(svc, asyncio.run(paperless.document(501)), {41: "PHYS207"}))
    assert out.unchanged and out.created == 0

    # Paperless refuses a byte-identical file: if it was carded, the title just records the later lecture
    paperless.task_result = ("failure", None, "Not consuming x.pdf: It is a duplicate of Capacitors (#420)")
    got = asyncio.run(settle_upload(svc, "t", [], pdf=v1, course="PHYS207", title="PHYS 207 L09 - Capacitors",
                                    wait_s=0))
    assert got == {"duplicate_of": 420, "unchanged": True}
    assert ("title", 420, "PHYS 207 L07-L09 - Capacitors") in paperless.events


# ---------- refiling a lecture sent under the wrong course ----------

MATH_TAG = "course::math309::l07-matrices"
WRONG_TAG = "course::nuen201::l09-math-309-l07-matrices"


class RefilePaperless(DeckPaperless):
    """Paperless whose documents keep their own tags, and whose trash empties the listing."""

    def __init__(self, docs: dict[int, tuple[str, bytes]], doc_tags: dict[int, set[int]]) -> None:
        super().__init__(docs)
        self.ids.update({"MATH-309": 4})
        self.doc_tags = doc_tags

    async def document(self, doc_id: int) -> Doc:
        return Doc(doc_id, self.docs[doc_id][0], frozenset(self.doc_tags[doc_id]), 1, "2026-10-07")

    async def add_tag(self, doc_id: int, tag: int) -> None:
        await super().add_tag(doc_id, tag)
        self.doc_tags[doc_id].add(tag)

    async def remove_tag(self, doc_id: int, tag: int) -> None:
        await super().remove_tag(doc_id, tag)
        self.doc_tags[doc_id].discard(tag)

    async def trash(self, doc_id: int) -> None:
        await super().trash(doc_id)
        del self.docs[doc_id]


class RefileCore(RecordingCore):
    def __init__(self, tagged: list[int]) -> None:
        super().__init__()
        self.with_wrong_tag = tagged
        self.tag_calls: list[tuple[str, list[int], list[str]]] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "anki_notes_search" and arguments["query"] == f"tag:{WRONG_TAG}":
            return {"items": [{"id": n, "first_field": ""} for n in self.with_wrong_tag], "has_more": False}
        if tool in ("anki_notes_add_tags", "anki_notes_remove_tags"):
            self.tag_calls.append((tool, arguments["note_ids"], arguments["tags"]))
            return {"state": "committed"}
        return await super().call(tool, arguments)


def math_card(front: str, tag: str, source: str) -> dict[str, Any]:
    c = card(front)
    return {**c, "fields": {**c["fields"], "Source": source},
            "tags": [tag, "topic::math::linear-algebra::change-of-basis", "type::definition", "src::slides"]}


def test_a_lecture_sent_under_the_wrong_course_moves_to_the_right_one(
        con: sqlite3.Connection, schema: Schema, tmp_path: Path) -> None:
    from mnemosyne.pipeline import refile
    courses = {4: "MATH309", 40: "NUEN201"}
    v1 = deck(SLIDES)
    paperless = RefilePaperless({420: ("MATH 309 L07 - Matrices", v1)}, {420: {4, 45, 90}})
    svc = make(con, schema, ScriptedLLM(fenced([math_card("Similar matrices", MATH_TAG, "MATH309 L07 p1")])),
               paperless, notes_dir=tmp_path)
    out = asyncio.run(process(svc, asyncio.run(paperless.document(420)), courses))
    record_job(con, 420, "done", 1)
    store.mark_written(con, {d.mnemosyne_id: 9001 for d in store.batch_drafts(con, out.batch_id or "")})

    # The annotated copy, sent with NUEN 201 and lecture 9 picked: Paperless tags it with both courses
    v2 = deck(SLIDES, ink={4: "why U inverse?"})
    paperless.docs[501] = ("NUEN 201 L09 - MATH 309 L07 - Matrices", v2)
    paperless.doc_tags[501] = {40, 4, 45, 90}
    svc.llm = ScriptedLLM(fenced([math_card("B = U^-1 A U", WRONG_TAG, "NUEN201 L09 p4")]))  # type: ignore[assignment]
    out2 = asyncio.run(process(svc, asyncio.run(paperless.document(501)), courses))
    record_job(con, 501, "done", 1)
    store.mark_written(con, {d.mnemosyne_id: 9002 for d in store.batch_drafts(con, out2.batch_id or "")})
    qstore.save_from_lecture(con, 501, "NUEN201", 9, "NUEN 201 L09 - MATH 309 L07 - Matrices", "2026-10-09",
                             [{"asked": "why U inverse?", "kind": "clarify", "page": 4}])
    core = RefileCore([9002, 9001])   # 9001 was adopted as a duplicate, so it carries the wrong tag too
    svc.core = core  # type: ignore[assignment]

    got = asyncio.run(refile(svc, 501, "MATH309"))
    assert got["title"] == "MATH 309 L07 - Matrices" and got["replaced"] == [420]
    assert ("title", 501, "MATH 309 L07 - Matrices") in paperless.events
    assert paperless.doc_tags[501] == {4, 45, 90}, "the wrong course's tag is gone"
    assert ("trash", 420, None) in paperless.events, "the earlier copy is replaced, like any re-share"
    drafts = store.batch_drafts(con, out2.batch_id or "")
    assert [(d.tags[0], d.fields["Source"]) for d in drafts] == [(MATH_TAG, "MATH309 L07 p4")]
    assert {"note_id": 9002, "fields": {"Source": "MATH309 L07 p4"},
            "idempotency_key": "mn-refile-src-9002-MATH309 L07"} in core.updates
    assert ("anki_notes_add_tags", [9001], [MATH_TAG]) in core.tag_calls
    assert ("anki_notes_remove_tags", [9002], [WRONG_TAG]) in core.tag_calls
    assert ("anki_notes_remove_tags", [9001], [WRONG_TAG]) in core.tag_calls
    assert any(u.get("fields") == {"SourceLink": "http://paperless/documents/501/details"} and u["note_id"] == 9001
               for u in core.updates), "the earlier copy's card follows it"
    q = qstore.for_doc(con, 501)
    assert [(x["course"], x["lecture"], x["lecture_title"]) for x in q] == [("MATH309", 7, "MATH 309 L07 - Matrices")]
    assert {r[0] for r in con.execute("SELECT course FROM deck_page WHERE doc_id = 501")} == {"MATH309"}
    assert {(r[0], r[1]) for r in con.execute("SELECT course, lecture FROM card_source")} == {("MATH309", 7)}

    events = len(paperless.events)
    again = asyncio.run(refile(svc, 501, "MATH309"))
    assert again["replaced"] == [] and again["anki_retagged"] == [] and len(paperless.events) == events

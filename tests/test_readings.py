"""The reading path: a textbook section from the ebook in Calibre, or from a scan of Aman's copy in Paperless."""
import asyncio
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf  # pyright: ignore[reportMissingTypeStubs]
import pytest
from fastapi.testclient import TestClient

from mnemosyne.api import create_app
from mnemosyne.cards import Schema
from mnemosyne.config import Settings
from mnemosyne.ingest import books
from mnemosyne.ingest.paperless import Doc
from mnemosyne.ledger import db, store
from mnemosyne.pipeline import Services, job_attempts, poll, record_job, run_reading
from test_pipeline import ROOT, BagEmbedder, FakeCore, ScriptedLLM, UploadPaperless, card, fenced
from test_service import APPROVE, bearer

TOC = [[1, "Chapter 1: Introduction", 1], [2, "1.1 Occurrence of Plasmas in Nature", 1],
       [2, "1.2 Definition of Plasma", 2], [1, "Chapter 2: Single-Particle Motions", 4], [2, "2.1 Introduction", 4],
       [2, "2.2 Uniform E and B Fields", 4], [3, "2.2.1 E=0", 5], [2, "2.3 Nonuniform B Field", 6],
       [3, "2.3.1 : Grad-B Drift", 6], [2, "2.4 Nonuniform E Field", 8], [1, "Chapter 3: Plasmas as Fluids", 9]]


def textbook(pages: int = 10) -> bytes:
    doc: Any = pymupdf.open()  # pyright: ignore[reportUnknownMemberType]
    for n in range(1, pages + 1):
        doc.new_page().insert_text((72, 72), f"Printed page {n}: the guiding center drifts across B")
    doc.set_toc(TOC)
    data = bytes(doc.tobytes())
    doc.close()
    return data


@pytest.fixture
def library(tmp_path: Path) -> Path:
    folder = tmp_path / "calibre" / "Francis F. Chen" / "Introduction to Plasma Physics and Controlled Fusion (28)"
    folder.mkdir(parents=True)
    (folder / "Introduction to Plasma Physics - Francis F. Chen.pdf").write_bytes(textbook())
    return tmp_path / "calibre"


# ---------- finding a reading ----------

CATALOG = books.load(ROOT / "config" / "books.yaml")


@pytest.mark.parametrize(("title", "expected"), [
    ("Chen 2.3", ("chen", "2.3", "")),
    ("Chen 2.3 - Nonuniform B Field", ("chen", "2.3", "Nonuniform B Field")),
    ("chen ch 2", ("chen", "ch 2", "")),
    ("Chen p 39-47 grad-B drift", ("chen", "p 39-47", "grad-B drift")),
    ("plasma 1.4: Debye shielding", ("chen", "1.4", "Debye shielding")),
    ("Griffiths-QM 2.3 harmonic oscillator", ("griffiths-qm", "2.3", "harmonic oscillator")),
    ("tm-cm ch 6", ("susskind-cm", "ch 6", "")),
])
def test_scan_titles_name_a_book_and_where(title: str, expected: tuple[str, str, str]) -> None:
    found = books.parse_title(title, CATALOG)
    assert found is not None and (found[0].key, found[1], found[2]) == expected


def test_other_documents_are_not_readings() -> None:
    for title in ("NUEN 201 HW03 - Decay chains", "Receipt 2026-10-01", "Chen's letter", "PHYS 207 L07 - Capacitors"):
        assert books.parse_title(title, CATALOG) is None, title


def test_where_a_reading_is(library: Path) -> None:
    pdf = books.pdf_path(library, 28)
    assert pdf is not None and books.pdf_path(library, 99) is None
    toc, count = books.outline(pdf)
    got = {w: books.locate(w, toc, count) for w in ("2.3", "2.2", "2.3.1", "ch 2", "1", "p 3-4")}
    assert (got["2.3"].first, got["2.3"].last, got["2.3"].tag, got["2.3"].label) == (6, 8, "s2-3", "2.3 Nonuniform B Field")
    assert (got["2.2"].first, got["2.2"].last) == (4, 6), "2.2 doesn't swallow 2.2.1, and ends where 2.3 starts"
    assert (got["2.3.1"].first, got["2.3.1"].last, got["2.3.1"].label) == (6, 8, "2.3.1: Grad-B Drift")
    assert (got["ch 2"].first, got["ch 2"].last, got["ch 2"].tag) == (4, 9, "ch2")
    assert (got["1"].first, got["1"].last) == (1, 4), "a bare number is a chapter"
    assert (got["p 3-4"].first, got["p 3-4"].last, got["p 3-4"].tag) == (3, 4, "p3-4")
    for bad in ("2.9", "p 9-30", "chapter two"):
        with pytest.raises(ValueError):
            books.locate(bad, toc, count)
    with pytest.raises(ValueError, match="no outline"):
        books.locate("2.3", [], count)
    assert [s["where"] for s in books.sections(toc)][:4] == ["ch 1", "1.1", "1.2", "ch 2"]


def test_bookmarks_a_page_early_are_shifted(library: Path) -> None:
    pdf = books.pdf_path(library, 28)
    assert pdf is not None
    toc, count = books.outline(pdf, (4, 1))
    assert [p for _, _, p in toc][:4] == [1, 1, 2, 5], "bookmarks before page 4 stay where they are"
    w = books.locate("2.3", toc, count)
    assert (w.first, w.last) == (7, 9), "the section keeps the page where the next one really starts"
    assert books.outline(pdf, (9, 1))[0][-1][2] == 10 and books.outline(pdf, (1, 5))[0][-1][2] == 10, "never past the end"


def test_printed_pages_map_into_the_pdf(tmp_path: Path) -> None:
    labeled: Any = pymupdf.open()  # pyright: ignore[reportUnknownMemberType]
    for _ in range(6):
        labeled.new_page()
    labeled.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                             {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1}])   # ii pages of front matter
    labeled.save(tmp_path / "labeled.pdf")
    plain: Any = pymupdf.open()  # pyright: ignore[reportUnknownMemberType]
    for _ in range(30):
        plain.new_page()
    plain.save(tmp_path / "plain.pdf")
    assert books.to_pdf(tmp_path / "labeled.pdf", 1, 15) == 3, "page labels beat the offset"
    assert books.to_pdf(tmp_path / "plain.pdf", 5, 15) == 20
    assert books.to_pdf(tmp_path / "plain.pdf", 5, None) == 5
    w = books.locate("p 4-6", [], 30, lambda n: n + 15)
    assert (w.first, w.last, w.label, w.tag) == (19, 21, "pp. 4-6", "p4-6"), "the label and tag keep the printed numbers"


def test_zero_padded_chapters_from_a_split_pdf() -> None:
    toc = [(1, "Front Matter.pdf", 2), (1, "Chapter 01.pdf", 16), (1, "Chapter 02.pdf", 67), (1, "Chapter 03.pdf", 107)]
    w = books.locate("ch 2", toc, 556)
    assert (w.first, w.last) == (67, 107)
    assert [s["where"] for s in books.sections(toc)] == ["ch 1", "ch 2", "ch 3"]


def test_unnumbered_chapters_take_their_number_from_their_first_section() -> None:
    toc = [(1, "Preface", 15), (1, "Vector Analysis", 25), (2, "1.1 Vector Algebra", 25), (3, "1.1.1 Vector Operations", 25),
           (2, "1.2 Differential Calculus", 36), (2, "More Problems on Chapter 1", 50), (1, "Electrostatics", 54),
           (2, "2.1 The Electric Field", 54), (1, "Potentials", 90)]          # Griffiths, Electrodynamics
    w = books.locate("ch 2", toc, 628)
    assert (w.first, w.last, w.label, w.tag) == (54, 90, "Chapter 2: Electrostatics", "ch2")
    assert books.locate("ch 1", toc, 628).first == 25, "1.1 over 1.1.1 isn't a chapter"
    assert [s["where"] for s in books.sections(toc)] == ["ch 1", "1.1", "1.2", "ch 2", "2.1"]


def test_a_part_heading_is_not_its_first_chapter() -> None:
    toc = [(1, "Introduction to Quantum Mechanics - Griffiths (2018).pdf", 1), (2, "Contents", 8), (2, "1 THEORY", 15),
           (2, "1 The Wave Function", 16), (3, "1.1 The Schrodinger Equation", 16), (4, "1.3.1 Discrete Variables", 20),
           (2, "2 Time-Independent Schrodinger Equation", 37), (3, "2.1 Stationary States", 37), (2, "Index", 498)]
    w = books.locate("ch 1", toc, 528)
    assert (w.first, w.last, w.label) == (16, 37, "1 The Wave Function")
    assert [(s["where"], s["level"]) for s in books.sections(toc)] == [
        ("ch 1", 1), ("1.1", 2), ("ch 2", 1), ("2.1", 2)], "levels count from the chapters, under the file-name entry"


def test_lectures_are_chapters() -> None:
    toc = [(1, "Preface", 10), (1, "Lecture 1: The Nature of Classical Physics", 14),
           (2, "Interlude 1: Spaces, Trigonometry, and Vectors", 28), (1, "Lecture 2: Motion", 42), (1, "Index", 242)]
    w = books.locate("ch 1", toc, 251)
    assert (w.first, w.last, w.label) == (14, 42, "Lecture 1: The Nature of Classical Physics")
    assert [s["where"] for s in books.sections(toc)] == ["ch 1", "ch 2"]


def test_unnumbered_sections_get_the_books_numbers() -> None:
    toc = [(1, "DETAILED CONTENTS", 24), (1, "Chapter 3 Motion in Two or Three Dimensions", 96),
           (2, "POSITION AND VELOCITY VECTORS", 96), (2, "RELATIVE VELOCITY", 114),
           (2, "FORCE AND INTERACTIONS", 130),                     # 4.1, filed under chapter 3 by the PDF
           (1, "Chapter 4 Newton’s Laws of Motion", 130), (2, "NEWTON’S FIRST LAW", 133),
           (1, "Chapter 5 Applying Newton’s Laws", 159), (1, "APPENDIX A", 1555), (1, "Copyright", 1600), (1, "Chapter 1: ‘I’m thinking’", 1612)]
    numbered = books.number_sections(toc)
    assert [t for _, t, _ in numbered][1:6] == [
        "Chapter 3 Motion in Two or Three Dimensions", "3.1 Position and Velocity Vectors", "3.2 Relative Velocity",
        "Chapter 4 Newton’s Laws of Motion", "4.1 Force and Interactions"]
    assert books.locate("4.2", numbered, 1599).label == "4.2 Newton’s First Law"
    assert books.locate("3.2", numbered, 1599).last == 130, "3.2 runs to where 4.1 starts"
    w = books.locate("p 104-105", numbered, 1599, lambda n: n + 30)
    assert w.chapter == 4, "pages know their chapter"
    assert books.locate("4.2", numbered, 1599).chapter == 4


def test_a_shared_textbook_picks_the_course_by_chapter(tmp_path: Path) -> None:
    (tmp_path / "books.yaml").write_text(
        "books:\n  young:\n    name: Young\n    calibre: 34\n    last_page: 1599\n    number_sections: true\n"
        "    course: {PHYS207: [21-32], PHYS221: [17-20, 33-36]}\n", encoding="utf-8")
    young = books.load(tmp_path / "books.yaml")["young"]
    assert (young.course, young.last_page, young.number_sections) == (None, 1599, True)
    assert [young.course_at(c) for c in (21, 32, 18, 34, 5, None)] == [
        "PHYS207", "PHYS207", "PHYS221", "PHYS221", None, None], "mechanics chapters are self-study"
    assert CATALOG["beiser"].course_at(None) == "NUEN201", "a one-course book ignores chapters"


def test_one_pdf_holding_two_books_numbered_from_1(tmp_path: Path) -> None:
    doc: Any = pymupdf.open()  # pyright: ignore[reportUnknownMemberType]
    for _ in range(12):
        doc.new_page()
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                         {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1},    # Statics, pp. 1-5
                         {"startpage": 7, "prefix": "", "style": "D", "firstpagenum": 1}])   # Dynamics, pp. 1-5
    doc.set_toc([[1, "1 General Principles", 3], [2, "1.1 Mechanics", 3], [1, "12 Kinematics of a Particle", 8],
                 [2, "12.1 Introduction", 8]])
    doc.save(tmp_path / "hibbeler.pdf")
    pdf = tmp_path / "hibbeler.pdf"
    (tmp_path / "books.yaml").write_text(
        "books:\n  statics:\n    calibre: 35\n    last_page: 7\n  dynamics:\n    calibre: 35\n    first_page: 8\n",
        encoding="utf-8")
    cat = books.load(tmp_path / "books.yaml")
    statics, dynamics = cat["statics"], cat["dynamics"]
    assert statics.pdf_page(pdf)(2) == 4 and dynamics.pdf_page(pdf)(2) == 9, "p. 2 of each, by its own labels"
    with pytest.raises(ValueError, match="isn't in this book"):
        statics.pdf_page(pdf)(9)
    toc, n = books.book_outline(pdf, dynamics)
    assert [t for _, t, _ in toc] == ["12 Kinematics of a Particle", "12.1 Introduction"] and n == 12
    assert [t for _, t, _ in books.book_outline(pdf, statics)[0]] == ["1 General Principles", "1.1 Mechanics"]
    w = books.locate("p 2-3", toc, n, dynamics.pdf_page(pdf))
    assert (w.first, w.last, w.chapter) == (9, 10, 12)


def test_the_book_ends_at_its_last_page(library: Path) -> None:
    pdf = books.pdf_path(library, 28)
    assert pdf is not None
    toc, count = books.outline(pdf, last_page=8)
    assert count == 8 and all(p <= 8 for _, _, p in toc), "chapter 3 (page 9) is past the book"
    with pytest.raises(ValueError):
        books.locate("p 9-10", toc, count)


def test_a_long_reading_is_refused() -> None:
    toc = [(1, "Chapter 4: Waves in Plasmas", 80), (1, "Chapter 5: Diffusion", 160)]
    with pytest.raises(ValueError, match="section by section"):
        books.locate("ch 4", toc, 500)


# ---------- carding it ----------

def chen_card(front: str, where: str = "s2-3") -> dict[str, Any]:
    c = card(front)
    return {**c, "deck": "Core", "fields": {**c["fields"], "Source": "Chen 2.3"},
            "tags": [f"src::book::chen::{where}", "topic::physics::plasma::fundamentals", "type::result"]}


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


class ScanPaperless(UploadPaperless):
    """A scan of Aman's copy, tagged `mnemosyne`; and the lecture/syllabus types, which readings never touch."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.scan = Doc(512, title, frozenset({93}), None, "2026-10-04")
        self.events: list[tuple[str, int, Any]] = []

    async def documents(self, **query: Any) -> list[Doc]:
        return [self.scan]

    async def candidates(self, *args: Any) -> list[Doc]:
        return [self.scan]

    async def document(self, doc_id: int) -> Doc:
        return self.scan

    async def download(self, doc_id: int) -> bytes:
        doc: Any = pymupdf.open()  # pyright: ignore[reportUnknownMemberType]
        for _ in range(3):
            page: Any = doc.new_page()
            page.draw_line((40, 300), (400, 330), color=(1, 0, 0), width=3)   # his pen; scans have no text layer
        data = bytes(doc.tobytes())
        doc.close()
        return data

    async def notes(self, doc_id: int) -> list[str]:
        return []

    async def add_tag(self, doc_id: int, tag: int) -> None:
        self.events.append(("tag", doc_id, tag))


def services(con: sqlite3.Connection, schema: Schema, llm: ScriptedLLM, library: Path,
             paperless: Any = None) -> Services:
    return Services(con, schema, FakeCore({}), llm, BagEmbedder(), paperless or ScanPaperless("x"),  # type: ignore[arg-type]
                    ROOT, ROOT / ".claude" / "skills" / "mnemosyne-cards", calibre_dir=library,
                    calibre_url="http://obelisk:8083")


def prompt(llm: ScriptedLLM) -> str:
    return json.dumps(llm.prompts[-1][1], default=str)


def test_a_section_from_the_ebook_waits_for_aman(con: sqlite3.Connection, schema: Schema, library: Path) -> None:
    book = CATALOG["chen"]
    llm = ScriptedLLM(fenced([chen_card("Why does a grad-B drift separate ions from electrons?")]))
    svc = services(con, schema, llm, library)
    out = asyncio.run(run_reading(svc, book, "2.3"))
    assert out is not None and out.created == 1 and out.approved == 0 and out.pages == 3
    sent = prompt(llm)
    assert "Printed page 6" in sent and "Printed page 8" in sent and "Printed page 5" not in sent
    assert "src::book::chen::s2-3" in sent and "deck Core" in sent and "Card 2.3 Nonuniform B Field only" in sent
    draft = store.batch_drafts(con, out.batch_id or "")[0]
    assert draft.status == "pending" and draft.fields["SourceLink"] == "http://obelisk:8083/book/28"
    assert job_attempts(con, "chen:s2-3", "reading")[0] == "done"
    assert asyncio.run(run_reading(svc, book, "2.3")) is None, "carded once; again only when asked"


class DownEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")


def test_cards_wait_for_aman_when_the_similarity_check_is_down(con: sqlite3.Connection, schema: Schema,
                                                                library: Path) -> None:
    twin = chen_card("Why does a grad-B drift separate ions from electrons?")
    twin["fields"] = {**twin["fields"], "Back": "worded differently"}
    llm = ScriptedLLM(fenced([chen_card("Why does a grad-B drift separate ions from electrons?"), twin,
                              chen_card("Direction of the curvature drift")]))
    svc = services(con, schema, llm, library)
    svc.embedder = DownEmbedder()  # type: ignore[assignment]
    out = asyncio.run(run_reading(svc, CATALOG["chen"], "2.3"))
    assert out is not None and out.created == 2 and out.duplicates == 1, "an exact repeat is still caught"
    drafts = store.batch_drafts(con, out.batch_id or "")
    assert all(d.status == "pending" and "similarity check unavailable" in (d.review_reason or "") for d in drafts)


def test_a_book_missing_from_calibre_needs_a_scan(con: sqlite3.Connection, schema: Schema, library: Path) -> None:
    svc = services(con, schema, ScriptedLLM(), library)
    assert asyncio.run(run_reading(svc, CATALOG["beiser"], "3.1")) is None
    status = con.execute("SELECT status, error FROM ingest_job WHERE ref = 'beiser:s3-1'").fetchone()
    assert status["status"] == "error" and "scan the pages" in status["error"]


def test_a_tagged_scan_titled_with_a_section_is_carded_by_the_poller(con: sqlite3.Connection, schema: Schema,
                                                                     library: Path) -> None:
    llm = ScriptedLLM(fenced([chen_card("What sets the direction of the grad-B drift?")]))
    paperless = ScanPaperless("Chen 2.3")
    svc = services(con, schema, llm, library, paperless)
    out = asyncio.run(poll(svc))
    assert len(out) == 1 and out[0].created == 1 and out[0].pages == 3
    sent = prompt(llm)
    assert "a scan of Aman's own copy of Chen" in sent and "strongest priority signals" in sent
    assert "the ebook's printed text" in sent and "Printed page 6" in sent, "the ebook's text rides along"
    draft = store.batch_drafts(con, out[0].batch_id or "")[0]
    assert draft.fields["SourceLink"] == "http://paperless/documents/512/details"
    assert ("tag", 512, 90) in paperless.events, "tagged done in Paperless"
    assert job_attempts(con, "paperless:512", "reading")[0] == "done"


def test_an_untitled_scan_waits_for_a_book_and_section(con: sqlite3.Connection, schema: Schema, library: Path) -> None:
    from mnemosyne.pipeline import waiting_scans
    llm = ScriptedLLM()
    svc = services(con, schema, llm, library, ScanPaperless("Scan 2026-10-04 14.02"))
    assert asyncio.run(poll(svc)) == [] and llm.prompts == []
    waiting = asyncio.run(waiting_scans(svc))
    assert [(w["id"], w["book"]) for w in waiting] == [(512, None)]


# ---------- the API ----------

@pytest.fixture
def client(tmp_path: Path, library: Path) -> tuple[TestClient, sqlite3.Connection]:
    (tmp_path / "approve").write_text(APPROVE)
    (tmp_path / "paperless").write_text("unused")
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, approve_token_file=tmp_path / "approve",
                        paperless_token_file=tmp_path / "paperless", calibre_dir=library)
    c = db.connect(settings.ledger)
    return TestClient(create_app(settings, core=FakeCore({}), con=c, schedule=False)), c  # type: ignore[arg-type]


def test_readings_over_the_api(client: tuple[TestClient, sqlite3.Connection]) -> None:
    http, c = client
    outline = http.get("/readings/books/chen/outline", headers=bearer(APPROVE)).json()
    assert {"where": "2.3", "title": "2.3 Nonuniform B Field", "page": 6, "level": 2} in outline
    assert http.get("/readings/books/beiser/outline", headers=bearer(APPROVE)).status_code == 404
    assert http.post("/readings", json={"book": "nobody", "where": "2.3"}, headers=bearer(APPROVE)).status_code == 404
    bad = http.post("/readings", json={"book": "chen", "where": "2.9"}, headers=bearer(APPROVE))
    assert bad.status_code == 400 and "outline" in bad.json()["detail"]
    assert http.post("/readings", json={"book": "beiser", "where": "2.1"}, headers=bearer(APPROVE)).status_code == 400
    record_job(c, "chen:s2-3", "done", 1, kind="reading")
    again = http.post("/readings", json={"book": "chen", "where": "2.3"}, headers=bearer(APPROVE)).json()
    assert again == {"already": True, "book": "Chen", "label": "2.3 Nonuniform B Field"}
    assert http.post("/readings", json={"book": "chen", "where": "2.3"}).status_code == 401

"""Research papers: a Paper in Paperless tagged `mnemosyne` is carded by the canon's paper rules, held for review,
and its prerequisites are checked against Aman's cards and books."""
import asyncio
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf  # pyright: ignore[reportMissingTypeStubs]
import pytest

from mnemosyne import learner, library, papers
from mnemosyne.cards import Schema
from mnemosyne.ingest import books
from mnemosyne.ingest.paperless import Doc
from mnemosyne.ledger import db, store
from mnemosyne.pipeline import job_attempts, poll
from mnemosyne.project import obsidian
from test_pipeline import ROOT, ScriptedLLM, card
from test_readings import CATALOG, ScanPaperless, prompt, services, textbook

TITLE = "Smith 2025 - Bohm diffusion in stellarators"
TAG = "src::paper::smith-2025-bohm-diffusion-in-stellarators"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


@pytest.fixture
def shelf(tmp_path: Path) -> Path:
    folder = tmp_path / "calibre" / "Francis F. Chen" / "Introduction to Plasma Physics and Controlled Fusion (28)"
    folder.mkdir(parents=True)
    (folder / "Chen.pdf").write_bytes(textbook())
    return tmp_path / "calibre"


def test_a_papers_tag_comes_from_its_title_and_year() -> None:
    assert papers.slug_for(TITLE, "2026-10-06") == "smith-2025-bohm-diffusion-in-stellarators"
    assert papers.slug_for("Bohm diffusion revisited", "2026-10-06") == "bohm-diffusion-revisited-2026", "year filed"
    assert papers.year_of("2501.01234v2", "2026-10-06") == 2026
    assert papers.tag_for("x-2025") == "src::paper::x-2025"


def test_prerequisites_are_cleaned_and_capped() -> None:
    raw: list[Any] = [{"concept": " Bohm diffusion ", "why": "the baseline"}, "junk", {"concept": ""},
                      *({"concept": f"c{i}", "search": f"s{i}"} for i in range(10))]
    got = papers.clean(raw)
    assert got[0] == {"concept": "Bohm diffusion", "why": "the baseline", "search": "Bohm diffusion"}
    assert len(got) == papers.MAX_PREREQUISITES


def test_prerequisites_are_checked_against_his_cards_and_books(shelf: Path, tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    async def search(core: Any, text: str, limit: int = 20) -> dict[str, Any]:
        held = {"Larmor radius": (4, 0, 0.95), "guiding center drift": (3, 2, 0.7)}.get(text, (0, 0, None))
        return {"summary": {"seen": held[0], "new": 0, "weak": held[1], "mean_retrievability": held[2]}}
    monkeypatch.setattr(learner, "search", search)
    pdf = books.pdf_path(shelf, 28)
    assert pdf is not None
    index = library.Index(tmp_path / "cache" / "books.db")
    index.ensure(pdf, CATALOG["chen"])
    items = [{"concept": "Larmor radius", "why": "", "search": "Larmor radius"},
             {"concept": "Guiding-center drift", "why": "the transport", "search": "guiding center drift"},
             {"concept": "Neoclassical transport", "why": "", "search": "neoclassical transport"}]
    found = asyncio.run(papers.check(object(), index, {"chen": "Chen"}, items))  # type: ignore[arg-type]
    assert [(p["cards"], p["weak"]) for p in found] == [(4, 0), (3, 2), (0, 0)]
    assert found[0]["book"] is None, "a held prerequisite needs no pointer"
    assert found[1]["book"]["name"] == "Chen", "a weak one points to his book"
    assert papers.held(found[0]) == "held (4 cards, mean recall 95%)"
    assert papers.held(found[2]) == "no cards"


def test_before_you_read_lands_in_the_papers_stub(con: sqlite3.Connection, tmp_path: Path) -> None:
    papers.save(con, 700, "smith-2025", TITLE, 2025, None, [
        {"concept": "Larmor radius", "why": "", "cards": 4, "weak": 0, "mean_r": 0.95, "book": None},
        {"concept": "Neoclassical transport", "why": "what it beats", "cards": 0, "weak": 0, "mean_r": None,
         "book": {"book": "chen", "name": "Chen", "page": "190", "section": "5.10 Bohm Diffusion"}}])
    stubs = tmp_path / "Paperless"
    stubs.mkdir()
    (stubs / "Smith 2025.md").write_text("---\npaperless_id: 700\n---\n# Smith\n<!-- HELIOS:below-is-yours -->\nmine\n",
                                         encoding="utf-8")
    assert obsidian.sync_doc(con, stubs, 700) == "Smith 2025.md"
    text = (stubs / "Smith 2025.md").read_text(encoding="utf-8")
    assert "## Before you read" in text and "✅ **Larmor radius**" in text
    assert "⚠️ **Neoclassical transport**: what it beats _no cards_ → Chen, p. 190 (5.10 Bohm Diffusion)" in text
    assert text.endswith("mine\n"), "everything below the marker stays his"


class PaperPaperless(ScanPaperless):
    """A paper, type Paper, tagged `mnemosyne`, with a text layer like a born-digital PDF."""

    def __init__(self, title: str = TITLE, doc_id: int = 700) -> None:
        super().__init__(title)
        self.scan = Doc(doc_id, title, frozenset({93}), 5, "2026-10-06")

    async def document_type_id(self, name: str) -> int | None:
        return {"Lecture": 1, "Syllabus": 2, "Assignment": 3, "Exam": 4, "Paper": 5}.get(name)

    async def download(self, doc_id: int) -> bytes:
        doc: Any = pymupdf.open()  # pyright: ignore[reportUnknownMemberType]
        for n in range(1, 4):
            doc.new_page().insert_text((72, 72), f"Page {n} of the paper: we measure diffusion scaling as 1/B")
        data = bytes(doc.tobytes())
        doc.close()
        return data

    async def remove_tag(self, doc_id: int, tag: int) -> None:
        self.events.append(("untag", doc_id, tag))


def paper_reply() -> str:
    c = card("Per Smith et al. (2025), how does stellarator diffusion scale with B?",
             tags=[TAG, "topic::plasma::magnetic-confinement", "type::claim"])
    c["deck"] = "Core"
    c["fields"] = {**c["fields"], "Source": "Smith et al. 2025, Fig. 3"}
    body = {"source": {"kind": "paper"}, "cards": [c], "skipped": [], "questions": [],
            "prerequisites": [{"concept": "Bohm diffusion", "search": "Bohm diffusion", "why": "the baseline"}]}
    return "```mnemosyne-cards\n" + json.dumps(body) + "\n```"


def test_a_tagged_paper_is_carded_held_and_its_prerequisites_kept(con: sqlite3.Connection, schema: Schema,
                                                                  shelf: Path) -> None:
    llm = ScriptedLLM(paper_reply())
    paperless = PaperPaperless()
    svc = services(con, schema, llm, shelf, paperless)
    out = asyncio.run(poll(svc))
    assert len(out) == 1 and out[0].created == 1 and out[0].approved == 0 and out[0].pages == 3
    sent = prompt(llm)
    assert "Research papers" in sent and TAG in sent and "deck Core" in sent and "prerequisites" in sent
    draft = store.batch_drafts(con, out[0].batch_id or "")[0]
    assert draft.status == "pending", "papers always wait for Aman"
    assert draft.fields["SourceLink"] == "http://paperless/documents/700/details"
    assert job_attempts(con, 700, "paper")[0] == "done" and ("tag", 700, 90) in paperless.events
    row = papers.get(con, 700)
    assert row is not None and row["slug"] == "smith-2025-bohm-diffusion-in-stellarators" and row["year"] == 2025
    assert row["prerequisites"][0]["concept"] == "Bohm diffusion"


def test_a_paper_filed_again_unchanged_isnt_carded_twice(con: sqlite3.Connection, schema: Schema, shelf: Path) -> None:
    asyncio.run(poll(services(con, schema, ScriptedLLM(paper_reply()), shelf, PaperPaperless())))
    llm = ScriptedLLM(paper_reply())
    out = asyncio.run(poll(services(con, schema, llm, shelf, PaperPaperless(doc_id=701))))
    assert len(out) == 1 and out[0].unchanged and llm.prompts == [], "same pages, no new marks: nothing to card"


def test_the_shortcut_files_a_paper_with_or_without_a_course(con: sqlite3.Connection, schema: Schema,
                                                              shelf: Path) -> None:
    from mnemosyne.pipeline import PipelineError, intake
    paperless = PaperPaperless()
    svc = services(con, schema, ScriptedLLM(), shelf, paperless)
    got = asyncio.run(intake(svc, b"%PDF", "smith.pdf", "NONE", None, TITLE, "paper"))
    assert got["title"] == TITLE and got["document_type"] == "Paper"
    assert paperless.uploads[-1] == {"title": TITLE, "tags": [93], "type": 5}, "tagged mnemosyne, no course"
    asyncio.run(intake(svc, b"%PDF", "assigned.pdf", "NUEN201", None, "", "paper"))
    assert paperless.uploads[-1] == {"title": "assigned", "tags": [93, 40], "type": 5}, "a course's paper keeps its tag"
    with pytest.raises(PipelineError, match="unknown course"):
        asyncio.run(intake(svc, b"%PDF", "x.pdf", "PHYS999", None, "x", "paper"))


def test_an_untagged_paper_is_left_alone(con: sqlite3.Connection, schema: Schema, shelf: Path) -> None:
    paperless = PaperPaperless()
    paperless.scan = Doc(700, TITLE, frozenset(), 5, "2026-10-06")
    llm = ScriptedLLM()
    assert asyncio.run(poll(services(con, schema, llm, shelf, paperless))) == [] and llm.prompts == []

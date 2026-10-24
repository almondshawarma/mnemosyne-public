"""Aman's books for the tutor and sessions: status (how to treat a PDF), reading with citations, search, problems."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemosyne import library
from mnemosyne.api import create_app
from mnemosyne.config import Settings
from mnemosyne.ingest import books
from test_pipeline import ROOT, FakeCore
from test_readings import CATALOG, textbook
from test_service import PROPOSE, bearer


@pytest.fixture
def shelf(tmp_path: Path) -> Path:
    folder = tmp_path / "calibre" / "Francis F. Chen" / "Introduction to Plasma Physics and Controlled Fusion (28)"
    folder.mkdir(parents=True)
    (folder / "Chen.pdf").write_bytes(textbook())
    return tmp_path / "calibre"


# ---------- status ----------

@pytest.mark.parametrize(("args", "expected"), [
    ((0, 0, 0, 0, 40), ("born-digital", "clean", "text")),
    ((0, 0, 3, 0, 40), ("born-digital", "clean", "text")),             # a stray lost glyph on a few pages is noise
    ((0, 0, 12, 0, 40), ("born-digital", "math-garbled", "images-for-math")),   # TeX symbols lost all over
    ((0, 0, 0, 5, 40), ("born-digital", "math-garbled", "images-for-math")),    # Mathematica's zHtL
    ((40, 0, 0, 0, 40), ("scanned", "ocr", "images-for-math")),
    ((40, 39, 0, 0, 40), ("scanned", "none", "images")),
])
def test_how_a_pdf_is_judged(args: tuple[int, int, int, int, int], expected: tuple[str, str, str]) -> None:
    assert library.judge(*args) == expected


@pytest.mark.parametrize(("producer", "scan"), [
    ("Adobe Acrobat 10.11 Paper Capture Plug-in with ClearScan", True),
    ("Adobe Acrobat Pro Extended 9.0.0", False),
    ("", False),
])
def test_a_clearscan_pdf_is_a_scan(producer: str, scan: bool) -> None:
    assert library.ocr_producer(producer) is scan


def test_books_yaml_overrides_a_misjudged_text_layer() -> None:
    assert library.judge(0, 0, 0, 0, 40, override="math-garbled")[1] == "math-garbled"
    st = library.Status("scanned", "ocr", "images-for-math", "chapters", 473)
    assert st.tags() == ["ai:scanned", "ai:text-ocr", "ai:read-images-for-math", "ai:outline-chapters"]


def test_every_calibre_pdf_gets_tags(shelf: Path) -> None:
    other = shelf / "Unknown" / "Shred Donuts (19)"
    other.mkdir(parents=True)
    (other / "Shred Donuts.pdf").write_bytes(textbook())
    tags = library.calibre_tags(shelf, CATALOG)
    assert set(tags) == {28, 19}
    assert "mnemosyne:chen" in tags[28] and "ai:outline-sections" in tags[28] and "ai:text-clean" in tags[28]
    assert not any(t.startswith("mnemosyne:") for t in tags[19]), "only books.yaml's books are mnemosyne's"


def test_one_file_holding_two_books_gets_both_keys(shelf: Path) -> None:
    from dataclasses import replace
    two = {"statics": replace(CATALOG["chen"], key="statics"), "dynamics": replace(CATALOG["chen"], key="dynamics")}
    tags = library.calibre_tags(shelf, two)
    assert "mnemosyne:statics" in tags[28] and "mnemosyne:dynamics" in tags[28]


# ---------- reading ----------

def test_a_reading_comes_with_its_citation(shelf: Path) -> None:
    pdf = books.pdf_path(shelf, 28)
    assert pdf is not None
    r = library.read(pdf, CATALOG["chen"], "2.3", images="no")
    assert r["cite"] == "Chen, 2.3 Nonuniform B Field, pp. 6–8" and r["chapter"] == 2
    assert [p["page"] for p in r["pages"]] == ["6", "7", "8"] and "guiding center" in r["pages"][0]["text"]
    assert "images" not in r and r["status"]["read"] == "text"
    assert library.read(pdf, CATALOG["chen"], "p 2", images="yes")["images"][0]["pdf_page"] == 2


# ---------- problems ----------

def lines(*text: str) -> str:
    return "\n".join(text)


def test_problems_in_each_books_style() -> None:
    griffiths = lines("2.3 The Harmonic Oscillator", "∗", "Problem 2.8 A particle of mass m in the infinite",
                           "square well starts out in the state", "∗∗", "Problem 2.9 For the wave function in",
                           "(see Problem 2.12). Not a problem")
    young = lines("EXERCISES", "Section 21.3  Coulomb’s Law", "21.1 •  Excess electrons are placed on a small",
                       "lead sphere.", "21.2 ••  Lightning occurs when", "Section 21.4  Electric Field",
                       "21.3 •••  A proton is placed")
    chen = lines("Problems", "2.1. Compute rL for the following cases", "(a) A 10-keV electron",
                      "2.2. In the TFTR the plasma was heated", "3.5 Examples and Applications 1111", "2.2",
                      "Uniform E and B Fields", "2.3 not a problem: the sequence skipped nothing, but no block")
    g = library.find_problems([(52, "39", griffiths)])
    assert [(p.number, p.difficulty, p.page) for p in g] == [("2.8", 1, "39"), ("2.9", 2, "39")]
    assert g[0].text == "A particle of mass m in the infinite square well starts out in the state"
    y = library.find_problems([(709, "679", young)])
    assert [(p.number, p.difficulty, p.section) for p in y] == [("21.1", 1, "21.3"), ("21.2", 2, "21.3"),
                                                                 ("21.3", 3, "21.4")]
    c = library.find_problems([(38, "25", chen)])
    assert [p.number for p in c] == ["2.1", "2.2"], "running heads and section numbers aren't problems"


def test_hibbelers_hyphenated_problems() -> None:
    text = lines("PROBLEMS", "12-47. The s-t graph for a train has been determined", "experimentally.",
                 "*12-48. The v-t graph of a car is shown.", "F12-3. Fundamental: find the velocity.",
                 "R12-1. Review: the particle travels", "Fig. 12-17a shows the car.", "17-17b. For convenience",
                 "12-49.", "A jet plane just after landing on a runway")
    found = library.find_problems([(700, "27", text)])
    numbers = sorted(p.number for p in found)
    assert numbers == ["12-47", "12-48", "12-49", "F12-3", "R12-1"], "a figure or a wrapped label isn't a problem"
    assert next(p for p in found if p.number == "12-49").text == "A jet plane just after landing on a runway"
    assert next(p for p in found if p.number == "12-47").text.endswith("determined experimentally.")
    assert library.parts("F12-3") == (12, 3)


def test_strangs_problem_sets() -> None:
    text = lines("Problem Set 1 . 1", "Problems 1-4 are about T-1.", "1", "Find the inverse of T3.",
                 "2 The determinant of K is", "n + 1.", "4", "A column u times a row is rank one:", "12", "5", "6 4 2",
                 "4 3 2 1", "5", "Write K3 in this form.", "10", "1.1 Four Special Matrices", "6",
                 "(Challenge problem) Find a formula.", "1.2 Differences, Derivatives, Boundary Conditions", "7",
                 "Not a problem: the next section began.")
    found = library.find_problems([(21, "10", text)])
    assert [p.number for p in found] == ["1.1.1", "1.1.2", "1.1.4", "1.1.5", "1.1.6"], \
        "OCR lost problem 3; a matrix entry, the page number, and the next section aren't problems"
    assert {p.section for p in found} == {"1.1"}
    assert found[1].text == "The determinant of K is n + 1."


def test_a_sentence_naming_a_problem_loses_to_the_problem() -> None:
    body = "the conservation law\nProblem 3.12. Mind you, this doesn't guarantee it"
    block = "Problems\n3.1 Solve Equation 3.1\n3.2 Derive Equation 3.4\n3.3 How do volumes transform?"
    found = library.find_problems([(98, "80", body), (109, "91", block + "\n" + "\n".join(
        f"3.{k} problem {k}" for k in range(4, 13)))])
    assert [p.number for p in found][:3] == ["3.1", "3.2", "3.3"]
    twelve = next(p for p in found if p.number == "3.12")
    assert twelve.page == "91" and twelve.text == "problem 12"


# ---------- search and the API ----------

def test_search_finds_and_cites_a_page(shelf: Path, tmp_path: Path) -> None:
    pdf = books.pdf_path(shelf, 28)
    assert pdf is not None
    index = library.Index(tmp_path / "cache" / "books.db")
    st = index.ensure(pdf, CATALOG["chen"])
    assert st.outline == "sections" and index.status_of(pdf, CATALOG["chen"]) == st
    hits = index.search("guiding centers drift", ["chen"], 3)
    assert len(hits) == 3 and hits[0]["book"] == "chen" and "«" in hits[0]["snippet"]
    assert index.search('"; DROP TABLE page; --', ["chen"]) == [], "queries are only ever terms"


def test_books_over_the_api_with_the_propose_token(shelf: Path, tmp_path: Path) -> None:
    (tmp_path / "propose").write_text(PROPOSE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        calibre_dir=shelf, books_index=tmp_path / "cache" / "books.db")
    client = TestClient(create_app(settings, core=FakeCore({}), schedule=False))  # type: ignore[arg-type]
    h = bearer(PROPOSE)
    assert client.get("/books").status_code == 401
    listed = client.get("/books", headers=h).json()
    assert [b["key"] for b in listed["books"]] == ["chen"] and listed["books"][0]["status"]["text"] == "clean"
    one = client.get("/books", params={"book": "plasma"}, headers=h).json()
    assert one["key"] == "chen" and one["outline"][0]["where"] == "ch 1"
    hits = client.get("/books/search", params={"q": "guiding center", "limit": 2}, headers=h).json()["hits"]
    assert hits[0]["cite"].startswith("Chen, p. ")
    read = client.get("/books/chen/read", params={"where": "2.3", "images": "no"}, headers=h).json()
    assert read["cite"] == "Chen, 2.3 Nonuniform B Field, pp. 6–8"
    assert client.get("/books/chen/read", params={"where": "9.9"}, headers=h).status_code == 422
    assert client.get("/books/serway/read", params={"where": "1.1"}, headers=h).status_code == 404
    assert client.get("/books/chen/problems", params={"where": "ch 2"}, headers=h).json()["total"] == 0

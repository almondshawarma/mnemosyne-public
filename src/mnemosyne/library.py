"""Aman's books, for whoever is reading them with him: the tutor, Claude in a session, or a card job.

- `status`: how to treat a PDF without trial and error (born digital or scanned; whether its text layer can be quoted;
  what its outline offers). It becomes `ai:*` tags in Calibre (scripts/calibre_tags.py) and rides along in every tool.
- `read`: a section's pages with printed page numbers and a citation, as text, plus page images when the text
  can't carry the math.
- `search`: full text across the books (SQLite FTS5), so a passage can be found and cited.
- `problems`: a chapter's or section's practice problems, for review sessions.

The search index is a cache built from the PDFs (MNEMOSYNE_BOOKS_INDEX), never state: delete it and it rebuilds.
"""

import base64
import json
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pymupdf  # pyright: ignore[reportMissingTypeStubs]

from mnemosyne.ingest import books
from mnemosyne.ingest.books import Book

READ_PAGES = 15          # a read returns at most this many pages of text
IMAGE_PAGES = 4          # and at most this many page images
IMAGE_PX = 1400          # long edge of a page image
SAMPLE = 40              # pages sampled to judge a PDF

# ---------- status ----------

TEXTS = ("clean", "math-garbled", "ocr", "none")
LOST_GLYPH = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f-]")   # math glyphs with no Unicode (TeX fonts)
MATHEMATICA = re.compile(r"\b[a-z]{1,2}H[0-9a-z]{1,3}L")                    # Mathematica exports: z(t) reads "zHtL"


@dataclass(frozen=True)
class Status:
    origin: str          # born-digital | scanned
    text: str            # clean | math-garbled | ocr | none: whether the text layer can be quoted as is
    read: str            # text | images-for-math | images: what to read
    outline: str         # sections | chapters | unnumbered | none: what `where` can name
    pages: int

    def tags(self) -> list[str]:
        return [f"ai:{self.origin}", f"ai:text-{self.text}", f"ai:read-{self.read}", f"ai:outline-{self.outline}"]


def judge(full_page_images: int, near_empty: int, lost_glyph_pages: int, mathematica: int, sampled: int,
          override: str | None = None) -> tuple[str, str, str]:
    """(origin, text, read) from a sample of pages. A few lost glyphs are noise (a stray symbol on a page or two); on
    one page in eight the math can't be trusted. `override` is books.yaml's `text:` where the sample misjudges."""
    origin = "scanned" if full_page_images * 2 >= sampled else "born-digital"
    if override in TEXTS:
        text = override
    elif near_empty * 2 >= sampled:
        text = "none"
    elif origin == "scanned":
        text = "ocr"
    else:
        garbled = mathematica >= 3 or lost_glyph_pages >= max(4, sampled // 8)
        text = "math-garbled" if garbled else "clean"
    read = {"clean": "text", "none": "images"}.get(text, "images-for-math")
    return origin, text, read


def outline_kind(toc: list[tuple[int, str, int]]) -> str:
    if not toc:
        return "none"
    picker = books.sections(toc)
    if any(not s["where"].startswith("ch") for s in picker):
        return "sections"
    return "chapters" if picker else "unnumbered"


def ocr_producer(producer: str) -> bool:
    """A scan whose page images were swapped for OCR'd glyphs: Acrobat's Paper Capture with ClearScan (Strang). It has
    no full-page images, so it looks born digital, but its text is OCR and its math is OCR's guess ('u(O)')."""
    return "paper capture" in producer.lower() or "clearscan" in producer.lower()


def status(pdf: Path, book: Book | None = None) -> Status:
    toc, n = books.book_outline(pdf, book) if book else books.outline(pdf)
    doc: Any = pymupdf.open(pdf)  # pyright: ignore[reportUnknownMemberType]
    try:
        meta: dict[str, Any] = doc.metadata or {}
        clearscan = ocr_producer(str(meta.get("producer") or ""))
        sample = sorted({int(i) for i in range(0, n, max(1, n // SAMPLE))})
        images = empty = lost = mathematica = 0
        for i in sample:
            page: Any = doc[i]
            text = str(page.get_text("text"))
            empty += len(text.strip()) < 50
            lost += len(LOST_GLYPH.findall(text)) >= 2
            mathematica += len(MATHEMATICA.findall(text))
            area = float(page.rect.width) * float(page.rect.height)
            images += any((b[2] - b[0]) * (b[3] - b[1]) > 0.8 * area for b in (x["bbox"] for x in page.get_image_info()))
    finally:
        doc.close()
    if clearscan:
        images = len(sample)
    origin, text, read = judge(images, empty, lost, mathematica, len(sample), book.text if book else None)
    return Status(origin, text, read, outline_kind(toc), n)


def calibre_books(root: Path) -> dict[int, Path]:
    """Every PDF in the Calibre library by book id ('<author>/<title> (<id>)/<file>.pdf')."""
    out: dict[int, Path] = {}
    for pdf in sorted(root.glob("*/* (*)/*.pdf")):
        if m := re.search(r"\((\d+)\)$", pdf.parent.name):
            out.setdefault(int(m[1]), pdf)
    return out


def calibre_tags(root: Path, catalog: dict[str, Book]) -> dict[int, list[str]]:
    """The `ai:*` tags each Calibre book should carry, plus `mnemosyne:<key>` for each books.yaml entry it holds
    (one file can hold two: Hibbeler's Statics and Dynamics)."""
    keyed: dict[int, list[Book]] = {}
    for b in catalog.values():
        if b.calibre is not None:
            keyed.setdefault(b.calibre, []).append(b)
    out: dict[int, list[str]] = {}
    for cid, pdf in calibre_books(root).items():
        held = keyed.get(cid, [])
        out[cid] = status(pdf, held[0] if held else None).tags() + [f"mnemosyne:{b.key}" for b in held]
    return out


# ---------- pages ----------

def printed(doc: Any, pdf_page: int, book: Book) -> str:
    """A PDF page's number as printed in the book: its page label, else the PDF page less the book's offset."""
    if doc.get_page_labels():
        return str(doc[pdf_page - 1].get_label()) or str(pdf_page)
    return str(pdf_page - (book.offset or 0)) if pdf_page > (book.offset or 0) else f"pdf {pdf_page}"


def cite(book: Book, label: str, first: str, last: str) -> str:
    pages = f"p. {first}" if first == last else f"pp. {first}–{last}"
    section = "" if label.startswith("p") else f", {label}"
    return f"{book.name}{section}, {pages}"


def page_images(pdf: Path, numbers: list[int]) -> list[dict[str, Any]]:
    from mnemosyne.extract import pages

    out: list[dict[str, Any]] = []
    for p in pages(pdf, IMAGE_PAGES, dpi=150, only=set(numbers[:IMAGE_PAGES]), max_px=IMAGE_PX, jpeg=True):
        if p.png:
            out.append({"pdf_page": p.number, "jpeg_base64": base64.b64encode(p.png).decode()})
    return out


def wants_images(images: str, st: Status) -> bool:
    return images == "yes" or (images == "auto" and st.read != "text")


def read(pdf: Path, book: Book, where: str, images: str = "auto") -> dict[str, Any]:
    """A section, chapter, or pages ('2.3', 'ch 2', 'p 39-47') as printed text with a citation. `images`: auto (when
    the text can't carry the math), yes, or no."""
    toc, n = books.book_outline(pdf, book)
    try:
        found = books.locate(where, toc, n, book.pdf_page(pdf))
    except ValueError as e:
        if "section by section" not in str(e):
            raise
        found = books.locate(where, toc, n, book.pdf_page(pdf), max_pages=10**6)
    st = status(pdf, book)
    last = min(found.last, found.first + READ_PAGES - 1)
    doc: Any = pymupdf.open(pdf)  # pyright: ignore[reportUnknownMemberType]
    try:
        numbers = list(range(found.first, last + 1))
        labels = [printed(doc, p, book) for p in numbers]
        pages = [{"pdf_page": p, "page": label, "text": str(doc[p - 1].get_text("text")).strip()}
                 for p, label in zip(numbers, labels, strict=True)]
        # where it continues, ending on a numbered page: a part's title page ('Part II', Grady) can't be asked for
        end = next((lab for p in range(found.last, last, -1) if (lab := printed(doc, p, book)).isdigit()),
                   printed(doc, found.last, book))
        more = f"p {printed(doc, last + 1, book)}-{end}" if last < found.last else None
    finally:
        doc.close()
    out: dict[str, Any] = {
        "book": book.key, "title": book.title, "where": found.label, "chapter": found.chapter,
        "cite": cite(book, found.label, labels[0], labels[-1]), "status": asdict(st),
        "note": NOTES[st.text], "pages": pages, "more": more}
    if wants_images(images, st):
        out["images"] = page_images(pdf, numbers)
    return out


NOTES = {
    "clean": "The text layer is faithful: quote it as is. Displayed equations can come apart across lines (a "
             "fraction's numerator and denominator on separate lines): check a page image before quoting one.",
    "math-garbled": "Prose is fine, but equations and symbols in the text layer are mangled: take math from the page "
                    "images, never from the text.",
    "ocr": "A scanned book: the text is OCR. Prose is mostly right; equations, symbols, and tables are not. Take math "
           "from the page images.",
    "none": "No usable text layer: read the page images.",
}


# ---------- problems ----------

STARRED = re.compile(r"^[∗*•\s]+$")
PROBLEM = re.compile(r"^(?P<stars>[∗*]*)\s*(?:Problem|Exercise)\s+(?P<num>\d+\.\d+)\b(?![)\],;])[.:]?\s*(?P<rest>.*)$")
BULLETED = re.compile(r"^(?P<num>\d+\.\d+)\s+(?P<dots>[•·]{1,3}|\.{1,3}(?=\s))\s*(?P<rest>.*)$")   # University Physics
PLAIN = re.compile(r"^(?P<num>\d+\.\d+)\.?\s+(?P<rest>\S.*)$")    # '2.1. Compute' (Chen), '3.3 (a) How' (Particles)
# Hibbeler: '12-48.', '*12-48.' (no answer in the back), 'F12-3.' (fundamental), 'R12-1.' (review)
HYPHENATED = re.compile(r"^\*?\s*(?P<num>[FR]?\d{1,2}\s?[-–—]\s?\d{1,3})\.(?:\s+(?P<rest>\S.*))?$")   # OCR often
# leaves the number alone on its line, with the text below
# Strang: 'Problem Set 1.1', then each problem's number alone ('7', its text below) or in front ('1 The Newton code')
PROBLEM_SET = re.compile(r"^Problem Set\s*\*?\s*(\d+)\s*\.\s*(\d+)\s*$", re.IGNORECASE)
SET_NUMBER = re.compile(r"^(\d{1,2})(?:\s+(\S.*))?$")
WORDS = re.compile(r"^(?:\(|[\"“]?(?:[A-Za-z]{2,}|A|I)\b)")   # a problem opens with words, not a matrix's numbers
RUNNING = re.compile(r"^(\d+\.\d+)\s+[A-Z]")                  # a section's heading or running head ('1.2 Differences')


def parts(number: str) -> tuple[int, ...]:
    """A problem number's numbers: '2.8' → (2, 8), 'F12-3' → (12, 3)."""
    return tuple(int(x) for x in re.findall(r"\d+", number))
SECTION_HEAD = re.compile(r"^Section\s+(\d+\.\d+)\s")
BLOCK = re.compile(r"^(?:Problems|PROBLEMS|Exercises|EXERCISES|CHALLENGE PROBLEMS|Further Problems.*|More Problems.*)\s*$")
STOP = re.compile(r"^(?:MCAT-STYLE PASSAGE PROBLEMS|PASSAGE PROBLEMS|DISCUSSION QUESTIONS|ANSWERS|References)\b")


@dataclass
class Problem:
    number: str
    page: str            # as printed
    pdf_page: int
    difficulty: int      # 0 unknown, else 1-3 (the book's stars or dots)
    section: str | None  # the "Section 21.3" heading it sits under, when the book groups them so
    text: str


def find_problems(pages: list[tuple[int, str, str]]) -> list[Problem]:
    """Problems in pages of text [(pdf page, printed page, text)]: 'Problem 2.8' / 'Exercise 4.1' (Griffiths,
    Susskind; stars on the line before or in front mark difficulty), '21.7 ••' (University Physics' dots), and under
    a 'Problems' heading, bare numbers (Chen, Griffiths Particles). A bare number counts only as the next in its
    chapter's sequence, so section headings and running heads ('3.5 Examples and Applications 1111') don't.
    Under 'Problem Set 1.1' (Strang), problems are numbered from 1 and become '1.1.7'; a number counts when it's next
    (or one past, where OCR lost one), isn't the page number, and words follow it, so a matrix's entries don't."""
    out: list[Problem] = []
    current: Problem | None = None
    section: str | None = None
    stars, in_block = 0, False
    problem_set: str | None = None
    set_last = 0
    last: dict[int, int] = {}
    seen: dict[tuple[str, str | None], tuple[int, bool]] = {}
    for pdf_page, page, text in pages:
        rows = [r.strip() for r in text.splitlines() if r.strip()]
        for k, line in enumerate(rows):
            if m := PROBLEM_SET.match(line):
                problem_set = section = f"{m[1]}.{m[2]}"
                set_last, current = 0, None
                continue
            if problem_set is not None:
                if (h := RUNNING.match(line)) and h[1] != problem_set:
                    problem_set = None
                elif ((m := SET_NUMBER.match(line)) and line != page and int(m[1]) in (set_last + 1, set_last + 2)
                      and WORDS.match(m[2] or (rows[k + 1] if k + 1 < len(rows) else ""))):
                    set_last = int(m[1])
                    current = Problem(f"{problem_set}.{m[1]}", page, pdf_page, 0, section, m[2] or "")
                    out.append(current)
                    continue
            if m := SECTION_HEAD.match(line):
                section, current = m[1], None
                continue
            if BLOCK.match(line):
                in_block, current = True, None
                continue
            if re.fullmatch(r"\d+(?:\.\d+)+", line):         # the next section's heading ("2.2" over its title)
                in_block, current = False, None
                continue
            m1, m2, m4 = PROBLEM.match(line), BULLETED.match(line), HYPHENATED.match(line)
            m3 = PLAIN.match(line) if in_block and not (m1 or m2 or m4) else None
            if m3:
                major, minor = (int(x) for x in m3["num"].split("."))
                if minor != last.get(major, 0) + 1:
                    m3 = None
            if m1 or m2 or m3 or m4:
                if m4:
                    level, num, rest = 0, re.sub(r"\s*[-–—]\s*", "-", m4["num"]), m4["rest"] or ""
                elif m1:
                    level, num, rest = len(m1["stars"]) or stars, m1["num"], m1["rest"]
                elif m2:
                    level, num, rest = len(m2["dots"].replace(" ", "")), m2["num"], m2["rest"]
                else:
                    assert m3 is not None
                    level, num, rest = stars, m3["num"], m3["rest"]
                if in_block and not m4:
                    major, minor = (int(x) for x in num.split("."))
                    last[major] = max(last.get(major, 0), minor)
                current = Problem(num, page, pdf_page, min(level, 3), section, rest)
                key = (num, section)
                if key not in seen:
                    seen[key] = (len(out), in_block)
                    out.append(current)
                elif in_block and not seen[key][1]:       # a sentence that begins "Problem 3.12." lost to the real one
                    out[seen[key][0]] = current
                    seen[key] = (seen[key][0], True)
                else:
                    current = None
                stars = 0
                continue
            if STARRED.match(line):
                stars, current = line.count("∗") + line.count("*"), None
                continue
            if STOP.match(line):
                in_block, current = False, None
                continue
            stars = 0
            if current is not None and len(current.text) < 1500:
                current.text = f"{current.text} {line}".strip()
    return sorted(out, key=lambda p: (parts(p.number), p.number))


def problems(pdf: Path, book: Book, where: str, difficulty: int = 0, limit: int = 8,
             offset: int = 0, images: str = "auto") -> dict[str, Any]:
    """Practice problems for a chapter or section. A section takes the problems on its own pages, or, in books that
    group end-of-chapter problems under 'Section 21.3' headings, the ones under its heading."""
    toc, n = books.book_outline(pdf, book)
    chapter = books.chapter_in(where)
    whole: books.Where | None = None
    if chapter is not None:
        try:
            whole = books.locate(f"ch {chapter}", toc, n, max_pages=10**6)
        except ValueError:
            whole = None
    try:
        span = books.locate(where, toc, n, book.pdf_page(pdf), max_pages=10**6)
    except ValueError:
        if whole is None:
            raise
        span = whole            # a section the outline doesn't list (Susskind numbers sections only on the page)
    scan = whole or span
    doc: Any = pymupdf.open(pdf)  # pyright: ignore[reportUnknownMemberType]
    try:
        texts = [(p, printed(doc, p, book), str(doc[p - 1].get_text("text"))) for p in range(scan.first, scan.last + 1)]
    finally:
        doc.close()
    found = find_problems(texts)
    if chapter is not None:
        found = [p for p in found if parts(p.number)[:1] == (chapter,)]
    if books.numbers(where)[0] == "section":
        num = re.sub(r"[^\d.]", "", where)
        grouped = [p for p in found if p.section is not None]
        if grouped:
            found = [p for p in grouped if p.section == num]
        elif span is not whole:
            found = [p for p in found if span.first <= p.pdf_page <= span.last]
        else:
            found = [p for p in found if p.number.startswith(f"{num}.") or p.number == num] or found
    if difficulty:
        found = [p for p in found if p.difficulty in (0, difficulty)]
    st = status(pdf, book)
    chosen = found[offset:offset + limit]
    out: dict[str, Any] = {
        "book": book.key, "where": span.label, "total": len(found), "offset": offset, "status": asdict(st),
        "note": NOTES[st.text], "problems": [asdict(p) | {"cite": f"{book.name}, Problem {p.number}, p. {p.page}"}
                                             for p in chosen]}
    if chosen and wants_images(images, st):
        out["images"] = page_images(pdf, sorted({p.pdf_page for p in chosen}))
    return out


# ---------- search ----------

class Index:
    """Full text of the books, one row per PDF page (SQLite FTS5). Rebuilt for a book when its PDF changes."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.con.executescript(
                "CREATE TABLE IF NOT EXISTS indexed (book TEXT PRIMARY KEY, stamp TEXT NOT NULL, status TEXT NOT NULL);"
                "CREATE VIRTUAL TABLE IF NOT EXISTS page USING fts5(book UNINDEXED, pdf_page UNINDEXED, "
                "printed UNINDEXED, section UNINDEXED, text, tokenize='porter unicode61');")

    @staticmethod
    def stamp(pdf: Path, book: Book) -> str:
        s = pdf.stat()
        return (f"{s.st_size}:{int(s.st_mtime)}:{book.offset}:{book.outline_shift}:{book.first_page}:"
                f"{book.last_page}:{book.text}")

    def status_of(self, pdf: Path, book: Book) -> Status:
        """The book's status as of its last indexing, when the PDF hasn't changed since; else judged now."""
        with self.lock:
            row = self.con.execute("SELECT stamp, status FROM indexed WHERE book = ?", (book.key,)).fetchone()
        return Status(**json.loads(row[1])) if row and row[0] == self.stamp(pdf, book) else status(pdf, book)

    def ensure(self, pdf: Path, book: Book) -> Status:
        stamp = self.stamp(pdf, book)
        with self.lock:
            row = self.con.execute("SELECT stamp, status FROM indexed WHERE book = ?", (book.key,)).fetchone()
        if row and row[0] == stamp:
            return Status(**json.loads(row[1]))
        st = status(pdf, book)
        toc, n = books.book_outline(pdf, book)
        doc: Any = pymupdf.open(pdf)  # pyright: ignore[reportUnknownMemberType]
        try:
            rows = [(book.key, p, printed(doc, p, book), section_at(toc, p), str(doc[p - 1].get_text("text")))
                    for p in range(book.first_page, n + 1)]
        finally:
            doc.close()
        with self.lock, self.con:
            self.con.execute("DELETE FROM page WHERE book = ?", (book.key,))
            self.con.executemany("INSERT INTO page (book, pdf_page, printed, section, text) VALUES (?, ?, ?, ?, ?)", rows)
            self.con.execute("INSERT OR REPLACE INTO indexed VALUES (?, ?, ?)", (book.key, stamp, json.dumps(asdict(st))))
        return st

    def search(self, query: str, keys: list[str], limit: int = 10) -> list[dict[str, Any]]:
        terms = [t for t in re.findall(r"[\w'’-]+", query) if t]
        if not terms or not keys:
            return []
        match = " ".join('"' + t.replace('"', "") + '"' for t in terms)
        marks = ",".join("?" * len(keys))
        with self.lock:
            rows = self.con.execute(
                f"SELECT book, pdf_page, printed, section, snippet(page, 4, '«', '»', ' … ', 24) FROM page "
                f"WHERE page MATCH ? AND book IN ({marks}) ORDER BY bm25(page) LIMIT ?",
                (match, *keys, max(1, min(limit, 50)))).fetchall()
        return [{"book": b, "pdf_page": int(p), "page": pr, "section": s, "snippet": " ".join(sn.split())}
                for b, p, pr, s, sn in rows]


def section_at(toc: list[tuple[int, str, int]], pdf_page: int) -> str | None:
    """The deepest outline entry a page falls under, for citing a search hit."""
    best: tuple[int, int, str] | None = None
    for lvl, title, page in toc:
        if page <= pdf_page and (best is None or page > best[1] or (page == best[1] and lvl >= best[0])):
            best = (lvl, page, title)
    return best[2] if best else None


def main() -> None:
    """`python -m mnemosyne.library tags`: every Calibre book's ai:* tags as JSON, for scripts/calibre_tags.py."""
    import os
    import sys

    from mnemosyne.config import Settings

    if sys.argv[1:] != ["tags"]:
        sys.exit("usage: python -m mnemosyne.library tags")
    settings = Settings()
    root = settings.calibre_dir or Path(os.environ.get("CALIBRE_DIR", "/calibre"))
    print(json.dumps(calibre_tags(root, books.load(settings.config_dir / "books.yaml"))))


if __name__ == "__main__":
    main()

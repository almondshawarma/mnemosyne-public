"""Textbooks for the reading path: config/books.yaml and the Calibre library, mounted read-only.

A reading is a book plus where in it: a section from the book's outline ("2.3"), a chapter ("ch 2"), or pages as
printed in the book ("p 39-47", what Aman reads off his own copy). Cards come from the ebook's pages, or from a scan of Aman's own copy filed in Paperless and titled
"Chen 2.3", where his marks are what count.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf  # pyright: ignore[reportMissingTypeStubs]
import yaml

MAX_PAGES = 45     # a reading longer than this is better carded section by section


@dataclass(frozen=True)
class Book:
    key: str
    name: str            # "Chen": the Source field and Paperless titles
    title: str
    calibre: int | None
    slug: str
    course: str | None
    aliases: tuple[str, ...]
    offset: int | None = None    # PDF page = printed page + offset, for PDFs without page labels
    outline_shift: tuple[int, int] | None = None   # (from, by): bookmarks at PDF page `from` or later point `by` early
    chapter_courses: tuple[tuple[int, int, str], ...] = ()   # (first, last chapter, course): one book, several courses
    last_page: int | None = None     # the book ends here; what the PDF carries after it is never read
    number_sections: bool = False    # sections are bookmarked without numbers: number them by page within chapters
    text: str | None = None          # the text layer's quality where library.status misjudges it (clean|math-garbled|ocr|none)
    first_page: int = 1              # the book starts here: one PDF holding two (Hibbeler's Statics, then its Dynamics)

    def pdf_page(self, pdf: Path) -> Callable[[int], int]:
        """Printed page → PDF page, within this book's pages of the PDF."""
        return lambda printed: to_pdf(pdf, printed, self.offset, (self.first_page, self.last_page or 10**9))

    def course_at(self, chapter: int | None) -> str | None:
        """The course a reading belongs to. A book shared by courses decides by chapter; a chapter none of them
        covers is self-study."""
        if not self.chapter_courses:
            return self.course
        return next((c for lo, hi, c in self.chapter_courses if chapter is not None and lo <= chapter <= hi), None)


@dataclass(frozen=True)
class Where:
    label: str           # "2.3 Nonuniform B Field", "Chapter 2: Single-Particle Motions", "pp. 39-47"
    tag: str             # s2-3 | ch2 | p39-47: the last part of src::book::<slug>::<tag>
    first: int           # PDF pages, 1-based, inclusive; 0 when only a scan is known
    last: int
    chapter: int | None = None    # the chapter it's in, when that's known


def chapter_ranges(raw: dict[str, Any]) -> tuple[tuple[int, int, str], ...]:
    """{PHYS207: ["21-32"], PHYS221: ["17-20", "33-36"]} → ((21, 32, "PHYS207"), ...)."""
    out: list[tuple[int, int, str]] = []
    for course, spans in raw.items():
        listed: list[Any] = spans if isinstance(spans, list) else [spans]  # pyright: ignore[reportUnknownVariableType]
        for span in listed:
            lo, _, hi = str(span).partition("-")
            out.append((int(lo), int(hi or lo), str(course).upper()))
    return tuple(sorted(out))


def load(path: Path) -> dict[str, Book]:
    if not path.exists():
        return {}
    doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw: dict[str, dict[str, Any]] = doc.get("books") or {}
    out: dict[str, Book] = {}
    for key, b in raw.items():
        aliases: list[Any] = b.get("aliases") or [key]
        course: Any = b.get("course")
        out[key] = Book(key, str(b.get("name") or key.title()), str(b.get("title") or ""),
                        int(b["calibre"]) if b.get("calibre") is not None else None, str(b.get("slug") or key),
                        str(course).upper() if course and not isinstance(course, dict) else None,
                        tuple(str(a).lower() for a in aliases),
                        int(b["offset"]) if b.get("offset") is not None else None,
                        (int(b["outline_shift"]["from"]), int(b["outline_shift"]["by"])) if b.get("outline_shift") else None,
                        chapter_ranges(course) if isinstance(course, dict) else (),  # pyright: ignore[reportUnknownArgumentType]
                        int(b["last_page"]) if b.get("last_page") else None, bool(b.get("number_sections")),
                        str(b["text"]) if b.get("text") else None, int(b.get("first_page") or 1))
    return out


def find(books: dict[str, Book], name: str) -> Book | None:
    n = name.strip().lower()
    return books.get(n) or next((b for b in books.values() if n in b.aliases or n == b.name.lower()), None)


SECTION = r"§?\s*\d+(?:\.\d+)+"
CHAPTER = r"ch(?:apter)?\.?\s*\d+"
PAGES = r"pp?\.?\s*\d+(?:\s*[-–]\s*\d+)?"
WHERE = re.compile(rf"^\s*(?:(?P<section>{SECTION})|(?P<chapter>{CHAPTER})|(?P<pages>{PAGES})|(?P<bare>\d+))\s*$", re.IGNORECASE)
TITLE = re.compile(rf"^\s*(?P<book>[A-Za-z][\w-]*)\s+(?P<where>{SECTION}|{CHAPTER}|{PAGES}|\d+)(?=$|[\s,:|–-])"
                   r"\s*[-–:|,]?\s*(?P<rest>.*)$", re.IGNORECASE)


def parse_title(title: str, books: dict[str, Book]) -> tuple[Book, str, str] | None:
    """A scan's Paperless title: 'Chen 2.3', 'Chen 2.3 - Nonuniform B', 'chen ch 2', 'Chen p 39-47'.
    Returns the book, where, and whatever follows."""
    m = TITLE.match(title)
    if not m or (book := find(books, m["book"])) is None:
        return None
    return book, m["where"].strip(), m["rest"].strip()


def numbers(where: str) -> tuple[str, int, int]:
    """('section'|'chapter'|'pages', a, b) with section numbers kept as text in the label instead."""
    m = WHERE.match(where)
    if not m:
        raise ValueError(f"can't read {where!r}: give a section (2.3), a chapter (ch 2), or pages (p 39-47)")
    if m["pages"]:
        a, *b = [int(x) for x in re.findall(r"\d+", m["pages"])]
        return "pages", a, (b[0] if b else a)
    if m["chapter"] or m["bare"]:
        n = int(re.findall(r"\d+", m["chapter"] or m["bare"])[0])
        return "chapter", n, n
    return "section", 0, 0


def tag_for(where: str) -> str:
    kind, a, b = numbers(where)
    if kind == "pages":
        return f"p{a}" if a == b else f"p{min(a, b)}-{max(a, b)}"
    if kind == "chapter":
        return f"ch{a}"
    return "s" + re.sub(r"[^\d.]", "", where).replace(".", "-")


def pdf_path(root: Path, calibre_id: int) -> Path | None:
    """Calibre keeps each book in '<author>/<title> (<id>)/'."""
    hits = sorted(root.glob(f"*/* ({calibre_id})/*.pdf"))
    return hits[0] if hits else None


def outline(pdf: Path, shift: tuple[int, int] | None = None, last_page: int | None = None,
            first_page: int = 1) -> tuple[list[tuple[int, str, int]], int]:
    """The PDF's bookmarks as (level, title, PDF page) and its page count, corrected by the book's `outline_shift`
    when they're off, and cut to its pages (`first_page`, `last_page`) when the PDF carries something besides it."""
    doc: Any = pymupdf.open(pdf)  # pyright: ignore[reportUnknownMemberType]
    try:
        toc: list[list[Any]] = doc.get_toc()
        count = int(doc.page_count)
        n = min(count, last_page or count)

        def moved(p: int) -> int:
            return min(p + shift[1], count) if shift and p >= shift[0] else p
        out = [(int(lvl), " ".join(str(t).split()), moved(int(p))) for lvl, t, p, *_ in toc]
        return [e for e in out if first_page <= e[2] <= n], n
    finally:
        doc.close()


def book_outline(pdf: Path, book: Book) -> tuple[list[tuple[int, str, int]], int]:
    """A book's outline with everything books.yaml corrects in it."""
    toc, n = outline(pdf, book.outline_shift, book.last_page, book.first_page)
    return (number_sections(toc) if book.number_sections else toc), n


def to_pdf(pdf: Path, printed: int, offset: int | None, window: tuple[int, int] | None = None) -> int:
    """A page number as printed in the book → the PDF's page: by the PDF's own page labels when it has them, else by
    the book's offset in books.yaml (none: the numbers are taken as PDF pages). `window` is the book's PDF pages, for
    a PDF holding two books whose page numbers both start at 1."""
    lo, hi = window or (1, 10**9)
    doc: Any = pymupdf.open(pdf)  # pyright: ignore[reportUnknownMemberType]
    try:
        hits: list[int] = [h + 1 for h in doc.get_page_numbers(str(printed))] if doc.get_page_labels() else []
    finally:
        doc.close()
    inside = [h for h in hits if lo <= h <= hi]
    page = inside[0] if inside else (hits[0] if hits and window is None else printed + (offset or 0))
    if window is not None and not lo <= page <= hi:
        raise ValueError(f"p. {printed} isn't in this book's pages")
    return page


CHAPTER_TITLE = re.compile(r"^(?:chapter|ch\.?|lecture)\s*0*(\d+)\b|^0*(\d+)(?:\s*[:.]\s+|\s+)(?!\d)", re.IGNORECASE)


def chapter_of(toc: list[tuple[int, str, int]], i: int) -> int | None:
    """The chapter an outline entry opens: numbered in its title ('Chapter 2', '2. Quantum States', 'Lecture 2: Motion'),
    or unnumbered over a numbered first section ('Electrostatics' over '2.1 The Electric Field', Griffiths)."""
    level, title, _ = toc[i]
    if m := CHAPTER_TITLE.match(title):
        return int(m[1] or m[2])
    if i + 1 < len(toc) and toc[i + 1][0] > level and (m := re.match(r"^§?\s*(\d+)\.1(?!\.?\d)", toc[i + 1][1])):
        return int(m[1])
    return None


def chapter_at(toc: list[tuple[int, str, int]], n: int) -> int | None:
    """The outline entry for chapter n: the top-level one, and of those, one with sections under it, so a part
    heading numbered like a chapter ('1 THEORY' just above '1 The Wave Function') isn't taken for it."""
    hits = [i for i in range(len(toc)) if chapter_of(toc, i) == n]
    if not hits:
        return None
    top = min(toc[i][0] for i in hits)
    hits = [i for i in hits if toc[i][0] == top]
    return next((i for i in hits if i + 1 < len(toc) and toc[i + 1][0] > top), hits[0])


def chapter_starts(toc: list[tuple[int, str, int]]) -> dict[int, int]:
    """Outline index → chapter number, one entry per chapter."""
    numbers = {n for i in range(len(toc)) if (n := chapter_of(toc, i)) is not None}
    return {i: n for n in numbers if (i := chapter_at(toc, n)) is not None}


def chapter_holding(toc: list[tuple[int, str, int]], page: int) -> int | None:
    """The chapter a PDF page is in: the last one to start at or before it."""
    starts = sorted((toc[i][2], n) for i, n in chapter_starts(toc).items())
    return next((n for p, n in reversed(starts) if p <= page), None)


SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"}


def number_sections(toc: list[tuple[int, str, int]]) -> list[tuple[int, str, int]]:
    """For books that bookmark sections without numbers (University Physics: 'COULOMB'S LAW'): section k of chapter N
    becomes 'N.k Coulomb's Law', in page order, the numbers printed in the book. Each section goes under the chapter
    whose pages hold it, even where the PDF files a chapter's first section under the one before."""
    chapters = chapter_starts(toc)
    if not chapters:
        return toc
    level = min(toc[i][0] for i in chapters)
    spans: dict[int, tuple[int, int]] = {}
    for i in chapters:
        end = next((p for lvl, _, p in toc[i + 1:] if lvl <= level), 10**6)
        spans[i] = (toc[i][2], end)
    owned: dict[int, list[int]] = {i: [] for i in chapters}
    for j, (lvl, title, page) in enumerate(toc):
        if lvl == level + 1 and not re.match(r"^§?\s*\d", title):
            owner = next((i for i, (lo, hi) in spans.items() if lo <= page < hi), None)
            if owner is not None:
                owned[owner].append(j)
    taken = {j for js in owned.values() for j in js}

    def tidy(title: str) -> str:
        if not title.isupper():
            return title
        words = title.lower().split()
        return " ".join(w if k and w in SMALL_WORDS else w[:1].upper() + w[1:] for k, w in enumerate(words))

    out: list[tuple[int, str, int]] = []
    for j, entry in enumerate(toc):
        if j in taken:
            continue
        out.append(entry)
        if j in chapters:
            ordered = sorted(owned[j], key=lambda k: (toc[k][2], k))
            out += [(level + 1, f"{chapters[j]}.{k} {tidy(toc[s][1])}", toc[s][2]) for k, s in enumerate(ordered, 1)]
    return out


def chapter_in(where: str) -> int | None:
    """The chapter a section or chapter reading names ('21.3', 'ch 21'); None for pages."""
    kind, a, _ = numbers(where)
    if kind == "chapter":
        return a
    return int(re.findall(r"\d+", where)[0]) if kind == "section" else None


def locate(where: str, toc: list[tuple[int, str, int]], page_count: int,
           pdf_page: Callable[[int], int] = lambda n: n, max_pages: int = MAX_PAGES) -> Where:
    """Where a reading is in the PDF. A section runs to the page where the next one at its level starts (shared, so
    the model is told to card only its own section). Pages are as printed; `pdf_page` maps them into the PDF."""
    kind, a, b = numbers(where)
    if kind == "pages":
        lo, hi = min(a, b), max(a, b)
        first, last = pdf_page(lo), pdf_page(hi)
        if not 1 <= first <= last <= page_count:
            raise ValueError(f"pages {lo}-{hi} aren't in this PDF")
        found = Where(f"pp. {lo}-{hi}" if lo != hi else f"p. {lo}", tag_for(where), first, last,
                      chapter_holding(toc, first))
    else:
        if not toc:
            raise ValueError("this PDF has no outline: give pages instead (p 39-47)")
        if kind == "chapter":
            hit = chapter_at(toc, a)
        else:
            num = re.sub(r"[^\d.]", "", where)
            pat = re.compile(rf"^(?:§\s*)?{re.escape(num)}(?!\.?\d)")
            hit = next((i for i, (_, t, _) in enumerate(toc) if pat.match(t)), None)
        if hit is None:
            raise ValueError(f"no {kind} {where!r} in the book's outline")
        level, title, first = toc[hit]
        after = next((p for lvl, _, p in toc[hit + 1:] if lvl <= level), page_count)
        label = re.sub(r"\.pdf$", "", title.replace(" : ", ": "), flags=re.IGNORECASE)   # split-PDF outlines
        if kind == "chapter" and not CHAPTER_TITLE.match(title):
            label = f"Chapter {a}: {label}"
        found = Where(label, tag_for(where), first, max(first, min(after, page_count)), chapter_in(where))
    if found.last - found.first + 1 > max_pages:
        raise ValueError(f"{found.label} is {found.last - found.first + 1} pages; card it section by section "
                         f"(at most {max_pages} pages at a time)")
    return found


def text_of(pdf: Path, where: Where, limit: int = 60000) -> str:
    """The ebook's printed text for a reading: a clean reference next to a scan (its math may be garbled)."""
    doc: Any = pymupdf.open(pdf)  # pyright: ignore[reportUnknownMemberType]
    try:
        parts = [f"--- ebook page {n} ---\n{str(doc[n - 1].get_text('text')).strip()}"
                 for n in range(where.first, where.last + 1)]
    finally:
        doc.close()
    return "\n".join(parts)[:limit]


def sections(toc: list[tuple[int, str, int]], depth: int = 2) -> list[dict[str, Any]]:
    """The outline's chapters and their sections `depth - 1` levels down, for the review page's picker. Levels count
    from the chapters', so an outline wrapped in a file-name entry (Griffiths QM) still lists its sections."""
    chapters = chapter_starts(toc)
    base = min((toc[i][0] for i in chapters), default=1)
    out: list[dict[str, Any]] = []
    for i, (lvl, title, page) in enumerate(toc):
        if i in chapters:
            out.append({"where": f"ch {chapters[i]}", "title": title, "page": page, "level": 1})
        elif base <= lvl < base + depth and (m := re.match(r"^§?\s*(\d+(?:\.\d+)+)\b", title)):
            out.append({"where": m[1], "title": title, "page": page, "level": lvl - base + 1})
    return out

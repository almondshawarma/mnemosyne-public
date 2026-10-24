"""PDF → pages: the text layer plus a rendered image of each page, since handwriting only shows in the image.
Also each page's fingerprint, so a deck sent again (more notes on it, or reused for the next lecture) is carded only
where it changed."""

import hashlib
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf  # pyright: ignore[reportMissingTypeStubs]

# "L07", or "L07-L09" for a deck that spans lectures
TITLE = re.compile(r"^(?P<course>[A-Z]{3,5})[\s-]?(?P<num>\d{3})\s+L(?P<first>\d{1,2})(?:[-–]L?(?P<last>\d{1,2}))?\b"
                   r"\s*[-–:|]?\s*(?P<title>.*)$")


@dataclass(frozen=True)
class Page:
    number: int
    text: str
    png: bytes | None


@dataclass(frozen=True)
class LectureRef:
    course: str        # NUEN201
    lecture: int       # the latest lecture the document covers: new cards get this one's tag
    title: str
    first: int | None = None   # where a deck that spans lectures starts

    @property
    def span(self) -> range:
        return range(min(self.first or self.lecture, self.lecture), self.lecture + 1)


def parse_title(title: str) -> LectureRef | None:
    """'NUEN 201 L05 - Lorentz Transformation' → NUEN201, 5, 'Lorentz Transformation'.
    'PHYS 207 L07-L08 - Capacitors' → PHYS207, 8, first 7."""
    m = TITLE.match(title.strip())
    if not m:
        return None
    first = int(m["first"])
    last = int(m["last"]) if m["last"] else first
    return LectureRef(m["course"] + m["num"], max(first, last), m["title"].strip(), min(first, last))


def lecture_label(span: range) -> str:
    return f"L{span[0]:02d}" if len(span) == 1 else f"L{span[0]:02d}-L{span[-1]:02d}"


def slug(title: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", re.sub(r"\([^)]*\)", "", title.lower())).split()
    return "-".join(words[:6]) or "lecture"


def pages(pdf: bytes | Path, max_images: int = 60, dpi: int = 110, only: set[int] | None = None,
          max_px: int | None = None, jpeg: bool = False) -> list[Page]:
    """Every page, or just the page numbers in `only`. A path is opened in place (textbooks run to hundreds of MB).
    `max_px` caps a render's long edge whatever the page size (phone scans come in as 26-inch pages), and `jpeg`
    suits photographed paper, where PNG runs to megabytes a page."""
    out: list[Page] = []
    doc: Any = (pymupdf.open(pdf) if isinstance(pdf, Path)  # pyright: ignore[reportUnknownMemberType]
                else pymupdf.open(stream=pdf, filetype="pdf"))  # pyright: ignore[reportUnknownMemberType]
    try:
        for i in range(int(doc.page_count)):
            if only is not None and i + 1 not in only:
                continue
            page: Any = doc[i]
            png: bytes | None = None
            if len(out) < max_images:
                zoom = dpi / 72
                if max_px is not None:
                    zoom = min(zoom, max_px / max(float(page.rect.width), float(page.rect.height)))
                pix: Any = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                png = bytes(pix.tobytes("jpeg", jpg_quality=80) if jpeg else pix.tobytes("png"))
            out.append(Page(i + 1, str(page.get_text("text")).strip(), png))
    finally:
        doc.close()
    return out


# ---------- page fingerprints ----------

THUMB_WIDTH = 160          # px; a handwritten word or a star still moves a few dozen pixels at this size
INK_LEVEL = 40             # a pixel counts as changed when its gray level moves by more than this
INK_PIXELS = 4             # and a page counts as written on when more than this many pixels do
MIN_TEXT = 40              # pages with less printed text than this (title slides, "Questions?") don't identify a deck


@dataclass(frozen=True)
class PageMark:
    number: int
    key: str               # hash of the printed text ("" when there's too little): the same slide in any copy
    thumb: bytes           # a small grayscale render, zlib-compressed: what changes when Aman writes on it


def text_key(text: str) -> str:
    norm = " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())
    return hashlib.sha1(norm.encode()).hexdigest()[:16] if len(norm) >= MIN_TEXT else ""


def fingerprint(pdf: bytes) -> list[PageMark]:
    out: list[PageMark] = []
    doc: Any = pymupdf.open(stream=pdf, filetype="pdf")  # pyright: ignore[reportUnknownMemberType]
    try:
        for i in range(int(doc.page_count)):
            page: Any = doc[i]
            scale = THUMB_WIDTH / float(page.rect.width)
            pix: Any = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY)
            out.append(PageMark(i + 1, text_key(str(page.get_text("text"))), zlib.compress(bytes(pix.samples), 6)))
    finally:
        doc.close()
    return out


def inked(a: bytes, b: bytes) -> bool:
    """Whether two thumbnails of the same page differ by more than rendering noise, i.e. something was written."""
    pa, pb = zlib.decompress(a), zlib.decompress(b)
    if len(pa) != len(pb):
        return True
    return sum(1 for x, y in zip(pa, pb, strict=True) if abs(x - y) > INK_LEVEL) > INK_PIXELS


def unseen(marks: list[PageMark], seen: list[PageMark], previous: list[PageMark] | None = None) -> list[int]:
    """Page numbers that still need carding: printed text no carded copy has, or a slide carded before that now
    carries new writing. Pages without a text layer (handwritten notes) are compared by position with the version
    they replace, if any."""
    by_key: dict[str, list[bytes]] = {}
    for m in seen:
        if m.key:
            by_key.setdefault(m.key, []).append(m.thumb)
    by_position = {m.number: m.thumb for m in previous or [] if not m.key}
    out: list[int] = []
    for m in marks:
        if m.key:
            if all(inked(m.thumb, t) for t in by_key.get(m.key, [])):
                out.append(m.number)
        elif (old := by_position.get(m.number)) is None or inked(m.thumb, old):
            out.append(m.number)
    return out


def same_deck(a: list[PageMark], b: list[PageMark]) -> bool:
    """Two PDFs are copies of one deck when they share at least half the slides of the smaller one (and at least 3)."""
    ka, kb = {m.key for m in a if m.key}, {m.key for m in b if m.key}
    shared = len(ka & kb)
    return shared >= 3 and shared >= 0.5 * min(len(ka), len(kb))

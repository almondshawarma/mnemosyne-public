"""Research papers: Aman files one in Paperless (type Paper) and tags it `mnemosyne`, and it's carded by the canon's
"Research papers" rules (attributed, dated claims; the paper's core, not its details), always held for his review.

What a paper assumes he knows comes back from the model as `prerequisites`. Each is checked against his cards
(learner.search) and his textbooks (the books index), and the stub's "Before you read" lists them: held, weak, or
missing with the section of his book that covers it. The tutor reads the same rows.
"""

import json
import re
import sqlite3
from typing import Any

from mnemosyne import learner
from mnemosyne.anki.core_client import Core
from mnemosyne.extract import slug
from mnemosyne.ledger.db import now
from mnemosyne.library import Index

YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")
MAX_PREREQUISITES = 8


def year_of(title: str, added: str) -> int | None:
    if m := YEAR.search(title):
        return int(m[1])
    return int(added[:4]) if added[:4].isdigit() else None


def slug_for(title: str, added: str) -> str:
    """The paper's tag slug from its Paperless title, with its year: "Smith 2025 - Bohm diffusion in stellarators"
    → smith-2025-bohm-diffusion-in-stellarators. Every copy of the paper filed under the same title shares it."""
    base = slug(title)
    year = year_of(title, added)
    return base if year is None or str(year) in base else f"{base}-{year}"


def tag_for(paper_slug: str) -> str:
    return f"src::paper::{paper_slug}"


def clean(items: list[Any]) -> list[dict[str, str]]:
    """The model's `prerequisites`, kept to the fields that matter and at most eight."""
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        d: dict[str, Any] = dict(item)  # pyright: ignore[reportUnknownArgumentType]
        concept = str(d.get("concept") or "").strip()
        if concept:
            out.append({"concept": concept, "why": str(d.get("why") or "").strip(),
                        "search": str(d.get("search") or concept).strip()})
    return out[:MAX_PREREQUISITES]


async def check(core: Core | None, index: Index | None, book_names: dict[str, str],
                items: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Each prerequisite with what Aman holds of it (cards seen, weak, mean retrievability) and, when he holds
    nothing or holds it weakly, where his books cover it."""
    out: list[dict[str, Any]] = []
    for p in items:
        row: dict[str, Any] = {**p, "cards": 0, "weak": 0, "mean_r": None, "book": None}
        if core is not None:
            try:
                found = await learner.search(core, p["search"], 10)
                s = found["summary"]
                row.update(cards=int(s["seen"]) + int(s["new"]), weak=int(s["weak"]), mean_r=s["mean_retrievability"])
            except Exception:  # noqa: BLE001 -- a failed lookup reads as "unknown", never as a failed paper
                row["cards"] = None
        if index is not None and book_names and (not row["cards"] or row["weak"]):
            hits = index.search(p["search"], list(book_names), 1)
            if hits:
                h = hits[0]
                row["book"] = {"book": h["book"], "name": book_names[h["book"]], "page": h["page"],
                               "section": h["section"]}
        out.append(row)
    return out


def save(con: sqlite3.Connection, doc_id: int, paper_slug: str, title: str, year: int | None, course: str | None,
         prerequisites: list[dict[str, Any]]) -> None:
    con.execute(
        "INSERT INTO paper (doc_id, slug, title, year, course, prerequisites, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (doc_id) DO UPDATE SET slug = excluded.slug,"
        " title = excluded.title, year = excluded.year, course = excluded.course,"
        " prerequisites = excluded.prerequisites, updated_at = excluded.updated_at",
        (doc_id, paper_slug, title, year, course, json.dumps(prerequisites, ensure_ascii=False), now(), now()))


def get(con: sqlite3.Connection, doc_id: int) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM paper WHERE doc_id = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["prerequisites"] = json.loads(out["prerequisites"] or "[]")
    return out


def recent(con: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = con.execute("SELECT doc_id FROM paper ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [p for r in rows if (p := get(con, int(r["doc_id"]))) is not None]


def held(p: dict[str, Any]) -> str:
    """How Aman stands on one prerequisite, in words."""
    if p.get("cards") is None:
        return "couldn't check"
    if not p["cards"]:
        return "no cards"
    r = f", mean recall {round(100 * p['mean_r'])}%" if p.get("mean_r") is not None else ""
    return f"{p['cards']} card{'s' if p['cards'] != 1 else ''}, {p['weak']} weak{r}" if p["weak"] else f"held ({p['cards']} cards{r})"


def stub_lines(con: sqlite3.Connection, doc_id: int) -> list[str]:
    """'Before you read' for the paper's stub: what it assumes, how he holds each, and where his books cover gaps."""
    paper = get(con, doc_id)
    if paper is None or not paper["prerequisites"]:
        return []
    lines = ["## Before you read", "", "What this paper assumes you know:", ""]
    for p in paper["prerequisites"]:
        ok = bool(p.get("cards")) and not p.get("weak")
        where = ""
        if (b := p.get("book")) and not ok:
            where = f" → {b.get('name') or b['book']}, p. {b['page']}" + (f" ({b['section']})" if b.get("section") else "")
        why = f": {p['why']}" if p.get("why") else ""
        lines.append(f"- {'✅' if ok else '⚠️'} **{p['concept']}**{why} _{held(p)}_{where}")
    return lines

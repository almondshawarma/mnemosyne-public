"""Aman's questions: kept, answered, sharpened, and never lost.

They come from two places: the lecture pipeline (questions he wrote on the slides, in the question color or with the
question mark from config/annotations.yaml) and sessions with Claude (`POST /questions`). Each has a short answer and
a sharper version of the question, which is how he gets better at asking. Each also has a kind, so the Sunday report
can show the mix shift from "what does this mean" toward "why", "what if", and "how does this connect". He closes a
question on the review page, or by ticking it in the lecture's note in Obsidian.
"""

import json
import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from mnemosyne.cards import CardIn, Payload, Schema, Source
from mnemosyne.generate.propose import KINDS
from mnemosyne.inbox import Verdict, propose
from mnemosyne.ledger import store
from mnemosyne.ledger.db import now
from mnemosyne.verify.quantities import verify_card

STATUSES = ("open", "answered", "carded", "dropped")
KIND_LABEL = {"clarify": "clarifying", "mechanism": "why / how", "predict": "what if", "connect": "connection",
              "apply": "application", "verify": "how do we know"}


class QuestionIn(BaseModel):
    """A question from a session (the tutor, or Aman typing one in)."""
    asked: str = Field(min_length=1, max_length=2000)
    course: str | None = None
    lecture: int | None = None
    about: str | None = None
    kind: Literal["clarify", "mechanism", "predict", "connect", "apply", "verify"] = "clarify"
    answer: str | None = None
    sharper: str | None = None
    beyond_course: bool = False
    card: dict[str, Any] | None = None
    no_card: str | None = Field(default=None, max_length=300)


class QuestionDecision(BaseModel):
    action: Literal["answered", "dropped", "reopen", "card", "hyperion"]
    note: str = Field(default="", max_length=2000)


def words(asked: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", asked.lower()).split())


def same_question(a: str, b: str) -> bool:
    """The same handwritten question read twice: a re-filed copy of the deck, transcribed a little differently. Equal
    once normalized, or the same leading question (up to its "?") when that's at least three words."""
    if words(a) == words(b):
        return True
    lead_a, lead_b = words(a.split("?")[0]), words(b.split("?")[0])
    return lead_a == lead_b and len(lead_a.split()) >= 3


def earlier(con: sqlite3.Connection, course: str, lecture: int, page: int | None, asked: str) -> sqlite3.Row | None:
    rows = con.execute("SELECT * FROM question WHERE course = ? AND lecture = ? AND page IS ? ORDER BY id",
                       (course, lecture, page))
    return next((r for r in rows if same_question(str(r["asked"]), asked)), None)


def save_from_lecture(con: sqlite3.Connection, doc_id: int, course: str, lecture: int, title: str, day: str,
                      items: list[dict[str, Any]]) -> list[int]:
    """Store the questions the model read off one lecture. Reprocessing the lecture, or a re-filed copy of it, adds
    nothing twice; a card the second reading wrote fills in one the first left out."""
    stamp = now()
    ids: list[int] = []
    for q in items:
        card = json.dumps(q["card"], ensure_ascii=False) if q.get("card") else None
        seen = earlier(con, course, lecture, q.get("page"), str(q["asked"]))
        if seen is not None:
            if seen["card_json"] is None and (card or q.get("no_card")):
                set_card(con, int(seen["id"]), q.get("card"), q.get("no_card"))
            continue
        cur = con.execute(
            "INSERT OR IGNORE INTO question (origin, doc_id, course, lecture, lecture_title, lecture_date, page, asked,"
            " about, kind, answer, sharper, beyond_course, card_json, no_card, status, created_at, updated_at)"
            " VALUES ('lecture', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (doc_id, course, lecture, title, day, q.get("page"), q["asked"], q.get("about"),
             q.get("kind") if q.get("kind") in KINDS else "clarify", q.get("answer"), q.get("sharper"),
             int(bool(q.get("beyond_course"))), card, None if card else q.get("no_card"), stamp, stamp))
        if cur.rowcount:
            ids.append(int(cur.lastrowid or 0))
    return ids


def set_card(con: sqlite3.Connection, qid: int, card: dict[str, Any] | None, no_card: str | None) -> None:
    """A question's card, or why it has none."""
    con.execute("UPDATE question SET card_json = ?, no_card = ?, updated_at = ? WHERE id = ?",
                (json.dumps(card, ensure_ascii=False) if card else None, None if card else no_card, now(), qid))


def without_cards(con: sqlite3.Connection, status: str = "open") -> list[dict[str, Any]]:
    """Questions still waiting for their card: none written, and no reason given for leaving it out."""
    return [row(r) for r in con.execute("SELECT * FROM question WHERE status = ? AND card_json IS NULL"
                                        " AND no_card IS NULL ORDER BY id", (status,))]


def move_doc(con: sqlite3.Connection, old: int, new: int, title: str) -> int:
    """A lecture's new copy replaced the old one in Paperless: its questions follow, under the new title."""
    cur = con.execute("UPDATE OR IGNORE question SET doc_id = ?, lecture_title = ?, updated_at = ? WHERE doc_id = ?",
                      (new, title, now(), old))
    return cur.rowcount


def refile_doc(con: sqlite3.Connection, doc_id: int, course: str, lecture: int, title: str) -> int:
    """A lecture filed under the wrong course moved to the right one: its questions follow."""
    cur = con.execute("UPDATE question SET course = ?, lecture = ?, lecture_title = ?, updated_at = ? WHERE doc_id = ?",
                      (course, lecture, title, now(), doc_id))
    return cur.rowcount


def add(con: sqlite3.Connection, q: QuestionIn) -> int:
    stamp = now()
    cur = con.execute(
        "INSERT INTO question (origin, course, lecture, asked, about, kind, answer, sharper, beyond_course, card_json,"
        " no_card, status, created_at, updated_at) VALUES ('session', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
        (q.course.upper() if q.course else None, q.lecture, q.asked.strip(), q.about, q.kind, q.answer, q.sharper,
         int(q.beyond_course), json.dumps(q.card, ensure_ascii=False) if q.card else None,
         None if q.card else q.no_card, stamp, stamp))
    return int(cur.lastrowid or 0)


def row(r: sqlite3.Row) -> dict[str, Any]:
    out = dict(r)
    out["card"] = json.loads(out.pop("card_json")) if out.get("card_json") else None
    out["beyond_course"] = bool(out["beyond_course"])
    return out


def get(con: sqlite3.Connection, qid: int) -> dict[str, Any] | None:
    r = con.execute("SELECT * FROM question WHERE id = ?", (qid,)).fetchone()
    return row(r) if r else None


def listing(con: sqlite3.Connection, status: str = "open", limit: int = 200) -> list[dict[str, Any]]:
    rows = con.execute("SELECT * FROM question WHERE status = ? ORDER BY lecture_date DESC, doc_id DESC, page, id"
                       " LIMIT ?", (status, limit))
    return [row(r) for r in rows]


def for_doc(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    return [row(r) for r in con.execute("SELECT * FROM question WHERE doc_id = ? ORDER BY page, id", (doc_id,))]


def set_status(con: sqlite3.Connection, qid: int, status: str, note: str = "", draft_id: str | None = None) -> bool:
    cur = con.execute("UPDATE question SET status = ?, note = COALESCE(NULLIF(?, ''), note),"
                      " draft_id = COALESCE(?, draft_id), updated_at = ? WHERE id = ?",
                      (status, note, draft_id, now(), qid))
    return bool(cur.rowcount)


def set_hyperion(con: sqlite3.Connection, qid: int, investigation: str) -> None:
    con.execute("UPDATE question SET hyperion_id = ?, updated_at = ? WHERE id = ?", (investigation, now(), qid))


def card_it(con: sqlite3.Connection, schema: Schema, anchors: Path, q: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Aman chose to keep the answer: its card becomes an approved draft (his press is the approval), once it passes
    the same schema and number checks as any card. Returns the MnemosyneID, or the problems."""
    if not q.get("card"):
        return None, ["this question has no card yet; press Write a card first"]
    try:
        card = CardIn.model_validate(q["card"])
    except ValueError as e:
        return None, [str(e)[:300]]
    problems = schema.problems(card)
    v = verify_card(card.note_type, card.tags, [c.model_dump() for c in card.checks], anchors)
    if v.status == "fail":
        problems.append(f"number check failed: {v.detail[:200]}")
    if problems:
        return None, problems
    source = Source(kind="question", course=q.get("course"), lecture=q.get("lecture"),
                    ref=f"question {q['id']}: {str(q['asked'])[:80]}")
    prop = propose(con, schema, Payload(source=source, cards=[card]), "question", lambda _i, _c: Verdict(approved=True))
    if prop.batch_id is None or not (drafts := store.batch_drafts(con, prop.batch_id)):
        return None, ["that card is already in the Ledger"]
    return drafts[0].mnemosyne_id, []


def stats(con: sqlite3.Connection, since: date) -> dict[str, Any]:
    """This week's questions: how many, which kinds, how many still open, and one worth showing off."""
    rows = [row(r) for r in con.execute("SELECT * FROM question WHERE created_at >= ?", (since.isoformat(),))]
    kinds = Counter(str(r["kind"]) for r in rows)
    deep = [r for r in rows if r["kind"] in ("mechanism", "predict", "connect", "verify") and r.get("sharper")]
    best = deep[0] if deep else next((r for r in rows if r.get("sharper")), None)
    open_now = int(con.execute("SELECT COUNT(*) FROM question WHERE status = 'open'").fetchone()[0])
    return {"asked": len(rows), "kinds": dict(kinds), "open": open_now,
            "deep_share": round(len([r for r in rows if r["kind"] in ("mechanism", "predict", "connect", "verify")])
                                / len(rows), 2) if rows else None,
            "example": {"asked": best["asked"], "sharper": best["sharper"], "kind": best["kind"]} if best else None}

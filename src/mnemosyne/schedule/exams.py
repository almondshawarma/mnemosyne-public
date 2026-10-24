"""Exams from the calendar, lecture coverage from the syllabus (PLAN §4.5).

Dates come from the Ledger's calendar_event rows (the Exams/Assignments calendar and the Canvas feed); the
same event appears once per Google account, so rows are deduplicated by course and title. The syllabus
only supplies which lectures each exam covers, and Aman confirms that map in Telegram.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from mnemosyne.extract import Page
from mnemosyne.generate.llm import LLM, Content, image, text
from mnemosyne.generate.propose import parse
from mnemosyne.ledger.db import now

CALENDARS = ("Exams/Assignments", "Aman Sharma Calendar (Canvas)")
CHICAGO = ZoneInfo("America/Chicago")
COURSE = re.compile(r"\b([A-Z]{4})[- ](\d{3})\b")
EXAM = re.compile(r"\b(exam|midterm|final)\b", re.IGNORECASE)
QUIZ = re.compile(r"\bquiz\b", re.IGNORECASE)


ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}


def title_key(title: str) -> str:
    """'Exam II', 'exam 2', 'EXAM-2' all compare equal; syllabi and calendars number exams differently."""
    words: list[str] = re.findall(r"[a-z]+|\d+", title.lower())
    return " ".join(ROMAN.get(w) or w for w in words if w != "midterm") or title.lower()


@dataclass(frozen=True)
class Exam:
    course: str
    kind: str          # exam | quiz
    title: str
    starts_at: str
    key: str           # course|title, stable across a moved date


def classify(title: str, courses: set[str]) -> tuple[str, str, str] | None:
    """'PHYS-207-210 Exam II' → ('PHYS207', 'exam', 'Exam II'). None if it isn't a known course's exam or quiz."""
    kind = "exam" if EXAM.search(title) else "quiz" if QUIZ.search(title) else None
    if kind is None:
        return None
    found = [a + b for a, b in COURSE.findall(title) if a + b in courses]
    if len(set(found)) != 1:
        return None
    clean = re.sub(r"\[.*?\]", "", title)
    clean = COURSE.sub("", clean)
    clean = re.sub(r"^[\s\d-]*", "", clean).strip()
    return found[0], kind, re.sub(r"\s+", " ", clean)


def from_calendar(con: sqlite3.Connection, courses: set[str], since: str) -> list[Exam]:
    marks = ",".join("?" * len(CALENDARS))
    rows = con.execute(f"SELECT title, start FROM calendar_event WHERE calendar IN ({marks}) AND start >= ?"
                       " ORDER BY start", (*CALENDARS, since)).fetchall()
    seen: dict[str, Exam] = {}
    for row in rows:
        got = classify(str(row["title"] or ""), courses)
        if got is None:
            continue
        course, kind, title = got
        key = f"{course}|{title.lower()}"
        seen.setdefault(key, Exam(course, kind, title, str(row["start"]), key))
    return list(seen.values())


def upsert(con: sqlite3.Connection, exams: list[Exam]) -> dict[str, int]:
    """New events are added; a moved date updates in place (and keeps the confirmed lecture map). A date Aman set
    by hand (set_date) stays until the calendar agrees with it: `kept` counts those."""
    counts = {"added": 0, "moved": 0, "unchanged": 0, "kept": 0}
    con.execute("BEGIN IMMEDIATE")
    try:
        for e in exams:
            row = con.execute("SELECT starts_at, date_set_by FROM exam WHERE calendar_source_id = ?", (e.key,)).fetchone()
            if row is None:
                con.execute("INSERT INTO exam (course, kind, title, starts_at, calendar_source_id, calendar_starts_at)"
                            " VALUES (?, ?, ?, ?, ?, ?)", (e.course, e.kind, e.title, e.starts_at, e.key, e.starts_at))
                counts["added"] += 1
                continue
            con.execute("UPDATE exam SET calendar_starts_at = ? WHERE calendar_source_id = ?", (e.starts_at, e.key))
            if row["date_set_by"] is not None:
                if same_moment(row["starts_at"], e.starts_at):   # the calendar caught up: it leads again
                    con.execute("UPDATE exam SET date_set_by = NULL WHERE calendar_source_id = ?", (e.key,))
                    counts["unchanged"] += 1
                else:
                    counts["kept"] += 1
            elif row["starts_at"] != e.starts_at:
                con.execute("UPDATE exam SET starts_at = ? WHERE calendar_source_id = ?", (e.starts_at, e.key))
                counts["moved"] += 1
            else:
                counts["unchanged"] += 1
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return counts


def parse_when(iso: str) -> datetime:
    """A calendar time ('2026-10-29T15:55:00-05:00', '...Z', or a bare date) as an aware datetime."""
    if len(iso) == 10:
        return datetime.combine(date.fromisoformat(iso), time(12), CHICAGO)
    got = datetime.fromisoformat(iso)
    return got if got.tzinfo else got.replace(tzinfo=CHICAGO)


def same_moment(a: str, b: str) -> bool:
    try:
        return parse_when(a) == parse_when(b)
    except ValueError:
        return a == b


def moved_to(old: str, day: date, at: time | None = None) -> str:
    """The exam on another day, at `at` or else its old time on the Chicago clock. The offset comes from that day,
    so a 3:55 pm exam moved across the end of daylight saving stays at 3:55 pm."""
    local = parse_when(old).astimezone(CHICAGO)
    clock = at or local.timetz().replace(tzinfo=None)
    return datetime.combine(day, clock, CHICAGO).isoformat()


def set_date(con: sqlite3.Connection, exam_id: int, day: date, at: time | None, by: str) -> str | None:
    """Aman moves an exam by hand. The calendar sync leaves this date alone until the calendar agrees.
    Returns the new starts_at, or None when there's no such exam."""
    row = con.execute("SELECT starts_at FROM exam WHERE id = ?", (exam_id,)).fetchone()
    if row is None:
        return None
    starts_at = moved_to(str(row["starts_at"]), day, at)
    con.execute("UPDATE exam SET starts_at = ?, date_set_by = ? WHERE id = ?", (starts_at, by, exam_id))
    return starts_at


def course_exams(con: sqlite3.Connection, course: str) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM exam WHERE course = ? AND kind = 'exam' ORDER BY starts_at", (course,)).fetchall()


SYSTEM = """You read a university course syllabus and map each exam to the lectures it covers.
Reply with one fenced json block: {"exams": [{"title": "<exam title exactly as given>", "lectures": [<lecture numbers>],
"topics": "<short list of topics>"}], "notes": "<anything ambiguous>"}.
Use the syllabus's schedule (weeks, dates, chapters, lecture numbers). If exams are cumulative, say so in notes and
list every lecture up to that exam. If the syllabus can't tell you, give an empty list and explain in notes. Never guess."""


async def propose_map(llm: LLM, course: str, exams: list[sqlite3.Row], pages: list[Page]) -> dict[str, Any]:
    listing = "\n".join(f"- {e['title']} on {e['starts_at'][:10]}" for e in exams)
    content: Content = [text(f"Course {course}. Exams on the calendar:\n{listing}\n\nThe syllabus follows.")]
    for page in pages:
        content.append(text(f"--- page {page.number} ---\n{page.text or '(no text layer)'}"))
        if page.png:
            content.append(image(page.png))
    doc = parse(await llm.complete("syllabus", SYSTEM, content))
    titles = {title_key(str(e["title"])): e for e in exams}
    detail: list[dict[str, Any]] = []
    raw: list[dict[str, Any]] = doc.get("exams") or []
    for item in raw:
        match = titles.get(title_key(str(item.get("title", ""))))
        if match is None:
            continue
        numbers: list[Any] = item.get("lectures") or []
        lectures = sorted({int(n) for n in numbers if str(n).isdigit()})
        detail.append({"title": match["title"], "starts_at": match["starts_at"], "lectures": lectures,
                       "topics": str(item.get("topics", ""))[:300]})
    return {"exams": detail, "notes": str(doc.get("notes", ""))[:600]}


def save_map(con: sqlite3.Connection, course: str, doc_id: int | None, proposal: dict[str, Any]) -> None:
    con.execute("INSERT INTO exam_map (course, doc_id, status, detail_json, created_at) VALUES (?, ?, 'pending', ?, ?)"
                " ON CONFLICT (course) DO UPDATE SET doc_id = excluded.doc_id, status = 'pending',"
                " detail_json = excluded.detail_json, notified_at = NULL, message_ref = NULL,"
                " created_at = excluded.created_at, decided_at = NULL",
                (course, doc_id, json.dumps(proposal, ensure_ascii=False), now()))


def decide_map(con: sqlite3.Connection, course: str, confirm: bool,
               lectures: dict[str, list[int]] | None = None) -> bool:
    """Confirm or reject a pending map. `lectures` (exam title → lecture numbers) corrects the proposal first,
    e.g. when the syllabus maps exams by chapter and the proposal came back empty."""
    row = con.execute("SELECT status, detail_json FROM exam_map WHERE course = ?", (course,)).fetchone()
    if row is None or row["status"] != "pending":
        return False
    detail = json.loads(row["detail_json"])
    for e in detail["exams"]:
        if lectures and e["title"] in lectures:
            e["lectures"] = sorted(set(lectures[e["title"]]))
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("UPDATE exam_map SET status = ?, decided_at = ?, detail_json = ? WHERE course = ?",
                    ("confirmed" if confirm else "rejected", now(), json.dumps(detail, ensure_ascii=False), course))
        if confirm:
            for e in detail["exams"]:
                con.execute("UPDATE exam SET lectures = ?, confirmed = 1 WHERE course = ? AND title = ? AND kind = 'exam'",
                            (json.dumps(e["lectures"]), course, e["title"]))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return True


def set_lectures(con: sqlite3.Connection, exam_id: int, lectures: list[int]) -> bool:
    """Aman sets one exam's coverage directly; that counts as confirmed."""
    cur = con.execute("UPDATE exam SET lectures = ?, confirmed = 1 WHERE id = ?", (json.dumps(sorted(set(lectures))), exam_id))
    return bool(cur.rowcount)


def upcoming(con: sqlite3.Connection, since: str) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM exam WHERE starts_at >= ? ORDER BY starts_at", (since,)).fetchall()


def pending_maps(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM exam_map WHERE status = 'pending' ORDER BY course").fetchall()


def announcement(course: str, proposal: dict[str, Any]) -> str:
    lines = [f"🗓 Exam map for {course[:-3]} {course[-3:]} (from the syllabus). Confirm it so exam decks know what to cover:"]
    for e in proposal["exams"]:
        lec = e["lectures"]
        span = (f"L{lec[0]:02d}–L{lec[-1]:02d}" if lec == list(range(lec[0], lec[-1] + 1)) else
                ", ".join(f"L{n:02d}" for n in lec)) if lec else "unknown"
        lines.append(f"• {e['title']} ({e['starts_at'][:10]}): {span}. {e['topics']}")
    if proposal.get("notes"):
        lines.append(f"Note: {proposal['notes']}")
    return "\n".join(lines)


def unnotified_maps(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM exam_map WHERE status = 'pending' AND notified_at IS NULL").fetchall()


def mark_map_notified(con: sqlite3.Connection, course: str, message_ref: str) -> None:
    con.execute("UPDATE exam_map SET notified_at = ?, message_ref = ? WHERE course = ?", (now(), message_ref, course))

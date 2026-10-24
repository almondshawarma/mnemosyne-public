"""Graded work (PLAN §4.6, built 2026-10-06): a returned exam, quiz, or homework → a debrief of every point lost.

Returns come three ways, and all of them come in the same way, as a PDF in Paperless (type Exam, or Assignment
tagged `graded`):
- paper Aman marked himself against the answer key (PHYS 207): his red ink is the diagnosis;
- Gradescope's "Download Graded Copy": the rubric items applied, with comments only sometimes;
- Canvas: the submission with its rubric, saved or screenshotted.

For each question that lost points, the model (the `graded` route, the strongest model on the gateway) re-solves it,
finds the wrong move, and sorts the miss. Conceptual and fact misses become cards (M-Error, M-Basic), held for review
like every graded card. Careless, procedure, time, and misread misses are only logged, since a card can't fix them,
and patterns across exams show up in the learner profile.

A diagnosis is confirmed only when it rests on something written: Aman's note, a grader's comment, or a rubric item.
One read off the key alone waits for him (its cards carry the question), and so does one whose re-solve disagrees
with the key.

The mistakes go to the Ledger for the tutor (the learner profile), and the debrief to the document's stub.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mnemosyne.cards import CardIn, Schema
from mnemosyne.extract import Page
from mnemosyne.generate.llm import LLM, Content, image, text
from mnemosyne.generate.propose import Context, parse, uncomputable
from mnemosyne.ledger.db import now
from mnemosyne.schedule import exam_decks

KINDS = ("conceptual", "fact", "procedure", "careless", "time", "misread")
CARDED = frozenset({"conceptual", "fact"})          # misses a card can fix
EVIDENCE = ("his_note", "grader_comment", "rubric", "key_only", "none")
WRITTEN = frozenset({"his_note", "grader_comment", "rubric"})
FEEDBACK = ("key", "rubric", "comments", "none")
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}
WORK = re.compile(r"\b(?P<kind>exam|midterm|final|quiz|hw|homework|assignment)\s*0*(?P<num>\d{1,2}|[IVX]{1,4})?\b",
                  re.IGNORECASE)
LECTURE_TAG = re.compile(r"^course::([a-z]+\d{3})::l(\d{2})-")


# ---------- what the model returns ----------

class Miss(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str
    qid: str | None = None
    possible: float | None = None
    earned: float | None = None
    his_answer: str | None = None
    correct_answer: str | None = None
    his_words: str | None = None
    kind: str = "conceptual"
    concept: str = Field(min_length=1)
    lecture: int | None = None
    topic: str | None = None
    trap: str | None = None
    fix: str | None = None
    why: str | None = None
    evidence: str = "none"
    solved: str | None = None
    agrees_with_key: bool | None = None
    cards: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])

    @field_validator("question", "qid", mode="before")
    @classmethod
    def as_text(cls, v: Any) -> Any:
        return None if v is None else str(v).strip()

    @field_validator("kind")
    @classmethod
    def known_kind(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        return v

    @field_validator("evidence")
    @classmethod
    def known_evidence(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in EVIDENCE else "none"

    @property
    def confirmed(self) -> bool:
        return self.evidence in WRITTEN and self.agrees_with_key is not False

    def doubt(self) -> str | None:
        """Why the diagnosis waits for Aman, or None when it rests on something written."""
        if self.agrees_with_key is False:
            return (f"Q{self.question}: the model's own solution ({self.solved or '?'}) doesn't match the key "
                    f"({self.correct_answer or '?'}). Check the diagnosis before keeping the card.")
        if self.evidence not in WRITTEN:
            return (f"Q{self.question}: nothing written says why it was wrong, so this diagnosis is the model's "
                    "reading of your work against the key. Is it what went wrong?")
        return None


class Debrief(BaseModel):
    model_config = ConfigDict(extra="ignore")
    earned: float | None = None
    possible: float | None = None
    feedback: str = "none"
    summary: str = ""
    misses: list[Miss] = Field(default_factory=list[Miss])

    @field_validator("feedback")
    @classmethod
    def known_feedback(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in FEEDBACK else "none"


@dataclass(frozen=True)
class Work:
    """One returned piece of graded work, as the pipeline found it."""
    doc_id: int
    course: str
    kind: str                       # exam | quiz | homework
    number: int | None
    title: str
    link: str | None
    exam: dict[str, Any] | None = None   # the calendar exam it was
    lecture_tags: tuple[str, ...] = ()   # every lecture tag the course's cards use
    existing: tuple[str, ...] = ()       # cards he already has for the exam's lectures
    notes: tuple[str, ...] = ()          # Paperless notes: his words, for returns graded online

    @property
    def src_tag(self) -> str:
        return "src::homework" if self.kind == "homework" else "src::exam"

    @property
    def card_kind(self) -> str:
        """The approval policy's kind (config/approval.yaml): graded work always waits for review."""
        return "graded-homework" if self.kind == "homework" else "graded-exam"


def work_ref(title: str) -> tuple[str, int | None] | None:
    """'PHYS 207 Exam 01' → ('exam', 1); 'MATH 309 HW04' → ('homework', 4); 'Quiz 3' → ('quiz', 3)."""
    m = WORK.search(title)
    if not m:
        return None
    word = m["kind"].lower()
    kind = "homework" if word in ("hw", "homework", "assignment") else "quiz" if word == "quiz" else "exam"
    raw = (m["num"] or "").upper()
    number = int(raw) if raw.isdigit() else ROMAN.get(raw)
    return kind, number


def match_exam(con: sqlite3.Connection, course: str, number: int | None, before: str) -> dict[str, Any] | None:
    """The calendar exam a returned exam was: the one with its number ('Exam I' for 'Exam 01'), else the latest one
    held before the document was filed."""
    rows = [dict(r) for r in con.execute("SELECT * FROM exam WHERE course = ? AND kind = 'exam' ORDER BY starts_at",
                                         (course,))]
    if number is not None:
        for r in rows:
            ref = work_ref(str(r["title"]))
            if ref is not None and ref[1] == number:
                return r
    held = [r for r in rows if str(r["starts_at"])[:10] <= before[:10]]
    return held[-1] if held else None


def readiness_before(con: sqlite3.Connection, course: str, title: str, day: str) -> float | None:
    """The exam deck's readiness on the last day before the exam that the metrics recorded."""
    key = exam_decks.deck_name(course, title).removeprefix(exam_decks.PREFIX)
    for r in con.execute("SELECT metrics_json FROM metric_day WHERE day < ? ORDER BY day DESC LIMIT 14", (day,)):
        metrics: dict[str, Any] = json.loads(r["metrics_json"])
        ready: dict[str, Any] = metrics.get("exam_readiness") or {}
        if ready.get(key) is not None:
            return float(ready[key])
    return None


def lecture_tags(con: sqlite3.Connection, course: str) -> list[str]:
    """Every course::<code>::lNN-… tag the course's cards use, one per lecture (the most common spelling)."""
    counts: dict[str, int] = {}
    for r in con.execute("SELECT tags_json FROM card_draft WHERE tags_json LIKE ?", (f'%course::{course.lower()}::l%',)):
        for t in json.loads(r["tags_json"]):
            if (m := LECTURE_TAG.match(t)) and m[1] == course.lower():
                counts[t] = counts.get(t, 0) + 1
    best: dict[str, str] = {}
    for t, _n in sorted(counts.items(), key=lambda kv: -kv[1]):
        best.setdefault(t[:t.index("-", len(f"course::{course.lower()}::l"))], t)
    return sorted(best.values())


# ---------- the prompt ----------

DEBRIEF = """
## This document: graded work

It's Aman's graded {kind}, {title}, returned with marks. Write a debrief of every question that lost points, and
cards for the misses a card can fix. Read every page image closely: the grading lives in the handwriting.

The grading reaches you in any mix of:
- Paper he marked himself against the answer key (see `graded` in "How Aman marks slides" above).
- Gradescope's graded copy: the rubric items applied to each question, with their deductions, sometimes comments.
- Canvas: rubric criteria with the points given, sometimes comments.
- His Paperless notes, below: what he says went wrong, in his words.

For each question that lost points (partial credit counts; full credit is left out):
1. Re-solve it yourself, fully, before you read his work. Put your final answer in `solved`, and in `agrees_with_key`
   whether it matches the key's (null when nothing on the page gives the right answer).
2. Find the move where his work goes wrong. Quote what he or the grader wrote about it in `his_words`, verbatim.
3. `kind`: conceptual (a wrong idea, or a wrong model of the situation) | fact (didn't know something) | procedure
   (right idea, wrong method or order of steps) | careless (arithmetic, sign, copying, units) | time (ran out) |
   misread (answered a different question). Between conceptual and careless, ask whether the same move would go
   wrong again on a fresh problem.
4. `evidence` for the diagnosis: his_note (he wrote why) | grader_comment | rubric (an applied rubric item names it) |
   key_only (only the right answer is marked; the why is your inference) | none.
5. `trap`, `fix`, `why`: a sentence each. The trap is the wrong move he made, the fix the move to make instead, the
   why the physics or math that makes the trap wrong, at the level that carries to the next problem, not this one's
   numbers.
6. `lecture` (its number) and `topic` (a topic:: tag from the vocabulary): where the idea was taught.
7. `cards`, only for conceptual and fact misses: an M-Error card per distinct trap (canon 11), its Situation general
   enough to recognize on a new problem; an M-Basic only for a fact he didn't know. Every card carries {src},
   type::trap on error cards, the lecture's tag from the list below (or course::{course} when no lecture fits), and
   its topic:: tags; deck Now. If a card he has already holds the fix, card the trap only when that card tests recall
   and the exam tested applying it, and say so in `why`.
   Careless, procedure, time, and misread misses get no cards: list them only.

If the scan is missing pages or a question can't be read, say so in `summary`.

Reply with exactly one fenced json block and no prose outside it:
{{"earned": 41, "possible": 50, "feedback": "key | rubric | comments | none", "summary": "two sentences",
  "misses": [{{"question": "5", "qid": "7", "possible": 6, "earned": 1, "his_answer": "C, 14/ε₀",
    "correct_answer": "G, 11/ε₀", "his_words": "...", "kind": "conceptual", "concept": "a few words",
    "lecture": 3, "topic": "topic::...", "trap": "...", "fix": "...", "why": "...", "evidence": "his_note",
    "solved": "11/ε₀, to the left", "agrees_with_key": true, "cards": [ cards in the canon's format ]}}]}}
"""


def system_prompt(skill_md: str, ctx: Context, work: Work) -> str:
    return (
        "You debrief Aman's graded work and write Anki cards with the mnemosyne canon below. Follow the canon exactly: "
        "it is the single source of truth for how cards are written.\n\n" + skill_md +
        ("\n\n## Topic vocabulary (config/topics.yaml, canon 22)\n```yaml\n" + ctx.topics + "```" if ctx.topics else "") +
        ("\n\n## How Aman marks slides and graded work (config/annotations.yaml)\n```yaml\n" + ctx.annotations + "```"
         if ctx.annotations else "") +
        DEBRIEF.format(kind=work.kind, title=work.title, src=work.src_tag, course=work.course.lower())
    )


def user_content(work: Work, pages: list[Page]) -> Content:
    header = [f"Source: {work.title} (graded {work.kind}). Course {work.course}."]
    if work.exam and work.exam.get("lectures"):
        lectures = json.loads(work.exam["lectures"]) if isinstance(work.exam["lectures"], str) else work.exam["lectures"]
        header.append(f"The exam covered lectures {', '.join(str(n) for n in lectures)}.")
    if work.lecture_tags:
        header.append("Lecture tags (use the one for the lecture that taught the idea):\n"
                      + "\n".join(f"- {t}" for t in work.lecture_tags))
    if work.notes:
        header.append("Aman's notes on this document:\n" + "\n".join(f"- {n}" for n in work.notes))
    if work.existing:
        header.append("Cards he already has for these lectures:\n" + "\n".join(f"- {e}" for e in work.existing[:120]))
    content: Content = [text("\n\n".join(header))]
    for page in pages:
        content.append(text(f"--- page {page.number} ---\n{page.text or '(no text layer)'}"))
        if page.png:
            content.append(image(page.png))
    return content


# ---------- checking it ----------

def card_problems(raw: dict[str, Any], work: Work, schema: Schema, anchors: Path | None) -> list[str]:
    try:
        card = CardIn.model_validate(raw)
    except ValidationError as e:
        return [str(err["msg"]) for err in e.errors()][:4]
    found = schema.problems(card) + uncomputable(card, anchors)
    if work.src_tag not in card.tags:
        found.append(f"missing the tag {work.src_tag}")
    if not any(t.startswith(f"course::{work.course.lower()}") for t in card.tags):
        found.append(f"missing a course::{work.course.lower()} tag (the lecture's, from the list)")
    return found


def check(doc: dict[str, Any], work: Work, schema: Schema,
          anchors: Path | None) -> tuple[Debrief | None, list[dict[str, Any]]]:
    """The debrief and a problem list: misses that don't parse, and cards that break the canon or the tags."""
    problems: list[dict[str, Any]] = []
    misses: list[Miss] = []
    raw_misses: list[Any] = doc.get("misses") or []
    for i, raw in enumerate(raw_misses):
        try:
            miss = Miss.model_validate(raw)
        except ValidationError as e:
            problems.append({"miss": i, "problems": [str(err["msg"]) for err in e.errors()][:4]})
            continue
        if miss.kind not in CARDED and miss.cards:
            problems.append({"miss": i, "problems": [f"a {miss.kind} miss gets no cards"]})
        for j, card in enumerate(miss.cards):
            if found := card_problems(card, work, schema, anchors):
                problems.append({"miss": i, "card": j, "problems": found})
        misses.append(miss)
    try:
        debrief = Debrief.model_validate({**{k: v for k, v in doc.items() if k != "misses"}, "misses": []})
    except ValidationError as e:
        return None, [{"problems": [str(err["msg"]) for err in e.errors()][:4]}]
    debrief.misses = misses
    return debrief, problems


async def diagnose(llm: LLM, skill_md: str, ctx: Context, work: Work, pages: list[Page],
                   schema: Schema) -> tuple[Debrief | None, list[dict[str, Any]], str]:
    """One call, and one retry with the problems listed. What's still wrong comes back with the debrief: a bad card is
    dropped from its miss (the miss itself is kept), and the problems are logged."""
    system, content = system_prompt(skill_md, ctx, work), user_content(work, pages)
    raw = await llm.complete("graded", system, content)
    try:
        debrief, problems = check(parse(raw), work, schema, ctx.anchors)
    except (ValueError, TypeError) as e:
        debrief, problems = None, [{"problems": [f"unparseable output: {e}"]}]
    if problems:
        fix = ("Your previous reply had these problems. Reply again with the complete corrected debrief (every miss, "
               "not just the fixed ones):\n" + json.dumps(problems, ensure_ascii=False) + "\n\nPrevious reply:\n"
               + raw[:60000])
        raw = await llm.complete("graded", system, [*content, text(fix)])
        try:
            debrief, problems = check(parse(raw), work, schema, ctx.anchors)
        except (ValueError, TypeError) as e:
            problems = [{"problems": [f"unparseable output after retry: {e}"]}]
    if debrief is not None:
        for miss in debrief.misses:
            miss.cards = [c for c in miss.cards if miss.kind in CARDED and not card_problems(c, work, schema, ctx.anchors)]
    return debrief, problems, raw


def cards_of(miss: Miss, link: str | None) -> list[CardIn]:
    """A miss's cards, ready for the gate: linked to the document, and carrying the doubt when there is one."""
    out: list[CardIn] = []
    for raw in miss.cards:
        card = CardIn.model_validate(raw)
        card.fields["SourceLink"] = link or ""
        if (doubt := miss.doubt()) and not card.question:
            card.question = doubt
        out.append(card)
    return out


# ---------- the Ledger ----------

def save(con: sqlite3.Connection, work: Work, debrief: Debrief, carded: dict[str, list[str]]) -> list[int]:
    """Upsert the work and its mistakes (keyed by document and question, so a rerun replaces them). `carded`: question →
    the MnemosyneIDs carded from it."""
    stamp = now()
    taken = str(work.exam["starts_at"])[:10] if work.exam else None
    ready = readiness_before(con, work.course, str(work.exam["title"]), taken) if work.exam and taken else None
    con.execute(
        "INSERT INTO graded_work (doc_id, course, kind, title, exam_id, taken_on, earned, possible, readiness_before,"
        " feedback, summary, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (doc_id) DO UPDATE SET course = excluded.course, kind = excluded.kind, title = excluded.title,"
        " exam_id = excluded.exam_id, taken_on = excluded.taken_on,"
        " earned = CASE WHEN graded_work.score_set THEN graded_work.earned ELSE excluded.earned END,"
        " possible = CASE WHEN graded_work.score_set THEN graded_work.possible ELSE excluded.possible END,"
        " readiness_before = excluded.readiness_before, feedback = excluded.feedback,"
        " summary = excluded.summary, updated_at = excluded.updated_at",
        (work.doc_id, work.course, work.kind, work.title, work.exam["id"] if work.exam else None, taken, debrief.earned,
         debrief.possible, ready, debrief.feedback, debrief.summary, stamp, stamp))
    ids: list[int] = []
    for m in debrief.misses:
        row = con.execute(
            "INSERT INTO mistake (doc_id, course, question, qid, possible, earned, kind, concept, lecture, topic,"
            " his_answer, correct_answer, his_words, trap, fix, why, evidence, confirmed, card_ids, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (doc_id, question) DO UPDATE SET qid = excluded.qid, possible = excluded.possible,"
            " earned = excluded.earned, kind = excluded.kind, concept = excluded.concept, lecture = excluded.lecture,"
            " topic = excluded.topic, his_answer = excluded.his_answer, correct_answer = excluded.correct_answer,"
            " his_words = excluded.his_words, trap = excluded.trap, fix = excluded.fix, why = excluded.why,"
            " evidence = excluded.evidence, confirmed = MAX(mistake.confirmed, excluded.confirmed),"
            " card_ids = excluded.card_ids, updated_at = excluded.updated_at RETURNING id",
            (work.doc_id, work.course, m.question, m.qid, m.possible, m.earned, m.kind, m.concept, m.lecture, m.topic,
             m.his_answer, m.correct_answer, m.his_words, m.trap, m.fix, m.why, m.evidence, int(m.confirmed),
             json.dumps(carded.get(m.question, [])), stamp, stamp)).fetchone()
        ids.append(int(row[0]))
    return ids


def mistakes_for(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    return [with_attempts(con, with_cards(con, dict(r))) for r in
            con.execute("SELECT * FROM mistake WHERE doc_id = ? ORDER BY CAST(question AS INTEGER), question", (doc_id,))]


class Attempt(BaseModel):
    """One fresh problem on a mistake's trap (the tutor's "same trap, new surface"), right or wrong."""
    model_config = ConfigDict(extra="forbid")
    right: bool
    hints: bool = False                              # he needed a hint to get there
    problem: str = ""                                # the fresh problem in a sentence, or the book problem it adapts
    note: str = ""                                   # what went wrong, or what he did differently this time


def add_attempt(con: sqlite3.Connection, mistake_id: int, a: Attempt, origin: str) -> int | None:
    """Only adds: whether the mistake is fixed stays Aman's call (his tick in the stub)."""
    if con.execute("SELECT 1 FROM mistake WHERE id = ?", (mistake_id,)).fetchone() is None:
        return None
    cur = con.execute("INSERT INTO mistake_attempt (mistake_id, right, hints, problem, note, origin, created_at)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?)", (mistake_id, int(a.right), int(a.hints), a.problem.strip() or None,
                                                       a.note.strip() or None, origin, now()))
    return cur.lastrowid


def with_attempts(con: sqlite3.Connection, m: dict[str, Any]) -> dict[str, Any]:
    """tried, clean (right with no hint), and the latest try."""
    rows = con.execute("SELECT right, hints, problem, note, created_at FROM mistake_attempt WHERE mistake_id = ?"
                       " ORDER BY created_at, id", (m["id"],)).fetchall()
    last = rows[-1] if rows else None
    m["attempts"] = {"tried": len(rows), "clean": sum(1 for r in rows if r["right"] and not r["hints"]),
                     "last": None if last is None else {"at": str(last["created_at"])[:10], "right": bool(last["right"]),
                                                        "hints": bool(last["hints"]), "note": last["note"]}}
    return m


def with_cards(con: sqlite3.Connection, m: dict[str, Any]) -> dict[str, Any]:
    """A mistake with its cards' review state. A card Aman approved confirms the diagnosis it came from."""
    ids: list[str] = json.loads(m.get("card_ids") or "[]")
    states = [str(r[0]) for r in con.execute(
        f"SELECT status FROM card_draft WHERE mnemosyne_id IN ({','.join('?' * len(ids))})", ids)] if ids else []
    m["cards"] = {s: states.count(s) for s in sorted(set(states))}
    m["confirmed"] = bool(m["confirmed"]) or any(s in ("approved", "written") for s in states)
    return m


def work_for(con: sqlite3.Connection, doc_id: int) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM graded_work WHERE doc_id = ?", (doc_id,)).fetchone()
    return dict(row) if row else None


def set_status(con: sqlite3.Connection, mistake_id: int, status: str | None = None, confirmed: bool | None = None,
               note: str | None = None) -> bool:
    sets: list[str] = ["updated_at = ?"]
    args: list[Any] = [now()]
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if confirmed is not None:
        sets.append("confirmed = ?")
        args.append(int(confirmed))
    if note:
        sets.append("his_words = COALESCE(his_words || ' / ', '') || ?")
        args.append(note)
    cur = con.execute(f"UPDATE mistake SET {', '.join(sets)} WHERE id = ?", (*args, mistake_id))
    return cur.rowcount > 0


def set_score(con: sqlite3.Connection, doc_id: int, earned: float, possible: float) -> bool:
    """The real score, when the scan can't show it all (a page missing, or Canvas gives the total separately).
    A rerun keeps it."""
    cur = con.execute("UPDATE graded_work SET earned = ?, possible = ?, score_set = 1, updated_at = ? WHERE doc_id = ?",
                      (earned, possible, now(), doc_id))
    return cur.rowcount > 0


def listing(con: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in con.execute("SELECT * FROM graded_work ORDER BY COALESCE(taken_on, created_at) DESC LIMIT ?", (limit,)):
        work = dict(r)
        work["mistakes"] = mistakes_for(con, int(work["doc_id"]))
        out.append(work)
    return out


def percent(earned: Any, possible: Any) -> float | None:
    return round(100 * float(earned) / float(possible), 1) if earned is not None and possible else None


def profile(con: sqlite3.Connection, today: date, days: int = 120) -> dict[str, Any]:
    """The learner profile's view: recent results against readiness, open mistakes (newest first), and the pattern by
    kind, per course."""
    since = date.fromordinal(today.toordinal() - days).isoformat()
    results = [{"title": r["title"], "course": r["course"], "date": r["taken_on"], "earned": r["earned"],
                "possible": r["possible"], "percent": percent(r["earned"], r["possible"]),
                "readiness_before": r["readiness_before"]}
               for r in con.execute("SELECT * FROM graded_work WHERE COALESCE(taken_on, created_at) >= ?"
                                    " ORDER BY COALESCE(taken_on, created_at) DESC", (since,))]
    keep = ("id", "course", "question", "kind", "concept", "lecture", "topic", "trap", "fix", "why", "his_words",
            "confirmed", "cards", "attempts", "lost")
    open_: list[dict[str, Any]] = []
    for r in con.execute("SELECT m.*, w.title FROM mistake m JOIN graded_work w USING (doc_id)"
                         " WHERE m.status = 'open' AND m.created_at >= ? ORDER BY m.created_at DESC, m.id", (since,)):
        m = with_attempts(con, with_cards(con, dict(r)))
        m["lost"] = (m["possible"] or 0) - (m["earned"] or 0) if m["possible"] is not None else None
        open_.append({**{k: m.get(k) for k in keep}, "from": m["title"]})
    pattern: dict[str, dict[str, int]] = {}
    for r in con.execute("SELECT course, kind, COUNT(*) AS n FROM mistake WHERE created_at >= ? GROUP BY course, kind",
                         (since,)):
        pattern.setdefault(str(r["course"]), {})[str(r["kind"])] = int(r["n"])
    return {"results": results, "open_mistakes": open_, "pattern": pattern,
            "how_to_read": "readiness_before = share of the exam's cards held at R ≥ 0.9 the day before; an open "
                           "mistake is one he hasn't shown he's fixed. Unconfirmed diagnoses were inferred from the "
                           "key alone: ask him before teaching to them. attempts = the fresh problems he's tried on a "
                           "mistake; clean = right with no hint."}


# ---------- the stub ----------

TICK = re.compile(r"^\s*- \[[xX]\] .*\^m(\d+)\s*$", re.MULTILINE)


def ticked(text: str) -> set[int]:
    return {int(m) for m in TICK.findall(text)}


def one_line(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def points(v: Any) -> str:
    return "?" if v is None else f"{float(v):g}"


def stub_lines(con: sqlite3.Connection, doc_id: int) -> list[str]:
    """The debrief for the document's stub: the score, readiness the day before, and each miss as a task (ticked once
    he's sure he has it)."""
    work = work_for(con, doc_id)
    if work is None:
        return []
    pct = percent(work["earned"], work["possible"])
    head = f"## Debrief: {points(work['earned'])}/{points(work['possible'])}" + (f" ({pct:g}%)" if pct is not None else "")
    lines = [head, ""]
    if work["readiness_before"] is not None:
        ready = round(100 * float(work["readiness_before"]))
        lines += [f"Exam-deck readiness the day before: {ready}% of its cards held at 90%+.", ""]
    if work["summary"]:
        lines += [one_line(work["summary"]), ""]
    for m in mistakes_for(con, doc_id):
        done = m["status"] in ("drilled", "fixed")
        lines.append(f"- [{'x' if done else ' '}] **Q{m['question']} ({points(m['earned'])}/{points(m['possible'])}): "
                     f"{one_line(m['concept'])}** · {m['kind']} ^m{m['id']}")
        for label, key in (("Trap", "trap"), ("Fix", "fix"), ("Why", "why")):
            if m.get(key):
                lines.append(f"    - {label}: {one_line(m[key])}")
        tail = [f'your note: "{one_line(m["his_words"])}"'] if m.get("his_words") else []
        if not m["confirmed"]:
            tail.append("unconfirmed: inferred from the key, so say if it's wrong")
        if m["attempts"]["tried"]:
            tail.append(f"fresh problems: {m['attempts']['clean']} of {m['attempts']['tried']} clean")
        if m["cards"]:
            tail.append(", ".join(f"{n} card{'s' if n > 1 else ''} {s}" for s, n in m["cards"].items()))
        if tail:
            lines.append(f"    - _{' · '.join(tail)}_")
    return lines


def read_ticks(con: sqlite3.Connection, text: str) -> None:
    for mid in ticked(text):
        con.execute("UPDATE mistake SET status = 'fixed', updated_at = ? WHERE id = ? AND status = 'open'", (now(), mid))

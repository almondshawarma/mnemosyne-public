"""Exam prep (P3's last scope, and the mnemosyne-exam skill's data; 2026-10-07).

**Prep material** is what Aman works before an exam: a professor's review sheet, a practice exam, a key. He files it
in Paperless once he has worked or marked it (type Lecture or Exam, titled with review/practice/key/solutions/mock,
e.g. "MATH 309 Exam 1 Review"). It's attached to its exam and never carded: cards come from understanding, after he
works it. The model reads it once for its problems (each mapped to lectures and topics, with what he did on it), the
scope and format it states, and the emphasis on it. A practice exam he marked against a key is also debriefed as
graded work (graded.py), so its misses become mistakes.

A **formula sheet** the course hands out ("PHYS 207 Exam II Formula Sheet"; they change from exam to exam) is filed
the same way and read for what's on it, so the cram sheet can say what isn't: what he has to carry in.

**The pack** is where the mnemosyne-exam skill starts: the exam and the course's exam rules, each lecture's Paperless
documents and his memory of it, the prep material with its problems' coverage, his open mistakes, and his probes.
Coverage is worked out live from the collection, since cards change daily.

**Probes** are the skill's check that a card he holds is one he can use: one step up from the card. A pass makes it
a hook to build on, a miss a gap to work. They only add, like mistake attempts.
"""

import base64
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

import pymupdf  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mnemosyne import graded
from mnemosyne.anki.core_client import Core, reads
from mnemosyne.extract import Page, pages, parse_title
from mnemosyne.generate.llm import LLM, Content, image, text
from mnemosyne.generate.propose import Context, parse
from mnemosyne.learner import count, lecture_query, probes
from mnemosyne.ledger.db import now

PREP = re.compile(r"\b(?:(?P<sheet>(?:formula|equation|crib)\s+sheets?)|(?P<key>keys?|solutions?|answers?)"
                  r"|(?P<practice>practice|mock|sample)|(?P<review>review))\b", re.IGNORECASE)
KIND_ORDER = ("sheet", "key", "practice", "review")    # a "Practice Exam Formula Sheet" is a sheet
FORMATS = ("mc", "tf", "fill-in", "work-out", "short-answer", "derivation", "other")
STATUSES = ("blank", "worked", "right", "wrong", "partial")
DOC_LINK = re.compile(r"/documents/(\d+)/")
READ_PAGES = 8               # pages per read call, with images: 12 nearly filled an MCP reply (2026-10-07 dry run)
READ_PX = 1400


def kind_of(title: str) -> str | None:
    """'MATH 309 Exam 1 Review' → review; '... Practice Exam KEY' → key; 'PHYS 207 Exam II Formula Sheet' → sheet;
    a lecture title → None."""
    if parse_title(title) is not None:
        return None
    found = {k for m in PREP.finditer(title) for k, v in m.groupdict().items() if v}
    return next((k for k in KIND_ORDER if k in found), None)


def exam_for(con: sqlite3.Connection, course: str, title: str, added: str) -> dict[str, Any] | None:
    """The exam it prepares for: the one its title numbers ('Exam 1 Review' → Exam I), else the next one after it
    was filed."""
    ref = graded.work_ref(title)
    number = ref[1] if ref is not None and ref[0] == "exam" else None
    rows = [dict(r) for r in con.execute("SELECT * FROM exam WHERE course = ? AND kind = 'exam' ORDER BY starts_at",
                                         (course,))]
    if number is not None:
        for r in rows:
            got = graded.work_ref(str(r["title"]))
            if got is not None and got[1] == number:
                return r
    upcoming = [r for r in rows if str(r["starts_at"])[:10] >= added[:10]]
    return upcoming[0] if upcoming else None


def lectures_of(exam: dict[str, Any] | None) -> list[int]:
    raw = (exam or {}).get("lectures")
    return sorted(json.loads(raw) if isinstance(raw, str) else (raw or []))


# ---------- what the model returns ----------

class Problem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    number: str
    summary: str = Field(min_length=1)
    format: str = "work-out"
    points: float | None = None
    lectures: list[int] = Field(default_factory=list[int])
    topics: list[str] = Field(default_factory=list[str])
    his_status: str = "blank"
    his_marks: str | None = None

    @field_validator("number", mode="before")
    @classmethod
    def as_text(cls, v: Any) -> str:
        return str(v).strip()

    @field_validator("format")
    @classmethod
    def known_format(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in FORMATS else "other"

    @field_validator("his_status")
    @classmethod
    def known_status(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in STATUSES else "blank"


class Material(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    scope: list[str] = Field(default_factory=list[str])
    format: str | None = None
    rules: str | None = None
    emphasis: list[str] = Field(default_factory=list[str])
    problems: list[Problem] = Field(default_factory=list[Problem])
    contents: list[str] = Field(default_factory=list[str])   # a formula sheet's items, as printed (TeX)


@dataclass(frozen=True)
class Doc:
    """One piece of prep material, as the pipeline found it."""
    doc_id: int
    course: str
    kind: str
    title: str
    link: str | None
    exam: dict[str, Any] | None
    lecture_titles: tuple[tuple[int, str], ...] = ()   # (number, title) for the exam's lectures
    notes: tuple[str, ...] = ()                         # his Paperless notes on it


PROMPT = """You read exam prep material for Aman, an engineering student, so his later study sessions can use it.
Nothing here becomes a card: list what the document tests and what he did on it.

This document: {title}, a {kind} for {course}{exam}.

Return:
- `summary`: two sentences: what it is and what it covers.
- `scope`: what the document says the exam covers, as written ("Chapters 1-3", "everything through Lecture 8").
- `format`: the exam's format when the document states it ("5 T/F, 3 fill-in, 5 work-out; 50 minutes"), else null.
- `rules`: what's allowed in the room when stated (formula sheet provided, own sheet and its size, calculator, closed
  book), else null.
- `emphasis`: the professor's emphasis, quoted ("definitely on exam", "know how to derive ...", starred items), and
  Aman's own marks that say what he thinks matters (see "How Aman marks", below).
- `problems`: every problem or question, in order, each with:
  - `number` as printed (a key's numbering when that's all there is);
  - `summary`: one line on what it asks, the physics or math rather than the numbers;
  - `format`: mc | tf | fill-in | work-out | short-answer | derivation | other;
  - `points` when printed;
  - `lectures`: the numbers of the lectures that taught it, from the list below;
  - `topics`: topic:: tags from the vocabulary below, the home domain first (canon 22);
  - `his_status`: blank (untouched) | worked (his work, no marking) | right | wrong | partial (his marks against a
    key, or a key's marks);
  - `his_marks`: what he wrote about it, verbatim, or null.

A formula sheet the course hands out has no problems. For one, `problems` is [] and `contents` lists every item on
it, in order, as printed: each formula in TeX inside \\( \\) with its label ("Gauss: \\(\\oint \\vec E\\cdot d\\vec A =
Q_{{enc}}/\\varepsilon_0\\)"), each constant with its value, each integral. Its `emphasis` is anything he wrote on it.
Leave `contents` empty for anything else.

Read every page image: his handwriting is on them.

Reply with exactly one fenced json block and no prose outside it:
{{"summary": "...", "scope": ["..."], "format": "...", "rules": null, "emphasis": ["..."],
  "problems": [{{"number": "3", "summary": "...", "format": "work-out", "points": 10, "lectures": [5],
    "topics": ["topic::math::linear-algebra::subspaces"], "his_status": "worked", "his_marks": null}}],
  "contents": []}}
"""


def system_prompt(ctx: Context, doc: Doc) -> str:
    exam = f" {doc.exam['title']}" if doc.exam else ""
    return (PROMPT.format(title=doc.title, kind=doc.kind, course=doc.course, exam=exam) +
            ("\n\n## Topic vocabulary (config/topics.yaml)\n```yaml\n" + ctx.topics + "```" if ctx.topics else "") +
            ("\n\n## How Aman marks (config/annotations.yaml)\n```yaml\n" + ctx.annotations + "```"
             if ctx.annotations else ""))


def user_content(doc: Doc, page_list: list[Page]) -> Content:
    header = [f"Source: {doc.title} ({doc.kind}). Course {doc.course}."]
    if doc.lecture_titles:
        header.append("The exam's lectures:\n" + "\n".join(f"- L{n:02d}: {t}" for n, t in doc.lecture_titles))
    if doc.notes:
        header.append("Aman's notes on this document:\n" + "\n".join(f"- {n}" for n in doc.notes))
    content: Content = [text("\n\n".join(header))]
    for page in page_list:
        content.append(text(f"--- page {page.number} ---\n{page.text or '(no text layer)'}"))
        if page.png:
            content.append(image(page.png))
    return content


def check(raw: dict[str, Any], kind: str) -> tuple[Material | None, list[str]]:
    try:
        found = Material.model_validate(raw)
    except ValidationError as e:
        return None, [str(err["msg"]) for err in e.errors()][:6]
    problems = [f"problem {p.number}: topic {t!r} isn't a topic:: tag" for p in found.problems for t in p.topics
                if not t.startswith("topic::")]
    if kind == "sheet" and not found.contents:
        problems.append("a formula sheet: list every item on it in `contents`")
    elif kind != "sheet" and not found.problems:
        problems.append("no problems listed: list every problem or question in the document")
    return found, problems


async def read(llm: LLM, ctx: Context, doc: Doc, page_list: list[Page]) -> Material:
    """One call, and one retry with the problems listed. Still wrong after that: an error on the job, not a guess."""
    system, content = system_prompt(ctx, doc), user_content(doc, page_list)
    raw = await llm.complete("prep", system, content)
    try:
        found, problems = check(parse(raw), doc.kind)
    except (ValueError, TypeError) as e:
        found, problems = None, [f"unparseable output: {e}"]
    if problems:
        fix = ("Your previous reply had these problems. Reply again with the complete corrected JSON:\n"
               + json.dumps(problems, ensure_ascii=False) + "\n\nPrevious reply:\n" + raw[:60000])
        raw = await llm.complete("prep", system, [*content, text(fix)])
        try:
            found, problems = check(parse(raw), doc.kind)
        except (ValueError, TypeError) as e:
            found, problems = None, [f"unparseable output after retry: {e}"]
    if found is None or problems:
        raise ValueError(f"prep material unreadable: {problems[:3]}")
    return found


def save(con: sqlite3.Connection, doc: Doc, m: Material) -> None:
    stamp = now()
    con.execute(
        "INSERT INTO exam_material (doc_id, exam_id, course, kind, title, link, summary, scope_json, format, rules,"
        " emphasis_json, problems_json, contents_json, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (doc_id) DO UPDATE SET exam_id = excluded.exam_id, course = excluded.course, kind = excluded.kind,"
        " title = excluded.title, link = excluded.link, summary = excluded.summary, scope_json = excluded.scope_json,"
        " format = excluded.format, rules = excluded.rules, emphasis_json = excluded.emphasis_json,"
        " problems_json = excluded.problems_json, contents_json = excluded.contents_json,"
        " updated_at = excluded.updated_at",
        (doc.doc_id, doc.exam["id"] if doc.exam else None, doc.course, doc.kind, doc.title, doc.link, m.summary,
         json.dumps(m.scope, ensure_ascii=False), m.format, m.rules, json.dumps(m.emphasis, ensure_ascii=False),
         json.dumps([p.model_dump() for p in m.problems], ensure_ascii=False),
         json.dumps(m.contents, ensure_ascii=False), stamp, stamp))


def materials(con: sqlite3.Connection, exam_id: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in con.execute("SELECT * FROM exam_material WHERE exam_id = ? ORDER BY doc_id", (exam_id,)):
        m = dict(r)
        for k in ("scope", "emphasis", "problems", "contents"):
            m[k] = json.loads(m.pop(f"{k}_json") or "[]")
        out.append(m)
    return out


# ---------- probes ----------

class ProbeIn(BaseModel):
    """One step up from a card he holds: did he take it?"""
    model_config = ConfigDict(extra="forbid")
    course: str
    hook: str = Field(min_length=1)                  # the card's prompt, or the idea he holds
    question: str = Field(min_length=1)              # the step up he was asked to take
    right: bool
    hints: bool = False
    note_id: int | None = None
    lecture: int | None = None
    topic: str | None = None
    exam: str | None = None                          # "Exam II": which exam it was prep for
    note: str = ""                                   # what broke, or what he did


def add_probe(con: sqlite3.Connection, p: ProbeIn, origin: str) -> int:
    course = p.course.replace(" ", "").upper()
    exam = exam_for(con, course, p.exam, now()) if p.exam else None
    cur = con.execute(
        "INSERT INTO probe (course, exam_id, lecture, topic, hook, note_id, question, right, hints, note, origin,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (course, exam["id"] if exam else None, p.lecture, p.topic, p.hook.strip(), p.note_id, p.question.strip(),
         int(p.right), int(p.hints), p.note.strip() or None, origin, now()))
    return int(cur.lastrowid or 0)


# ---------- the pack ----------

def lecture_docs(con: sqlite3.Connection, course: str, lectures: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Each lecture's Paperless documents (the decks it was carded from)."""
    out: dict[int, list[dict[str, Any]]] = {n: [] for n in lectures}
    for r in con.execute("SELECT DISTINCT lecture, ref, link FROM card_source WHERE course = ? AND kind = 'slides'"
                         " AND link IS NOT NULL ORDER BY lecture, link", (course,)):
        m = DOC_LINK.search(str(r["link"]))
        if m and r["lecture"] in out and all(d["doc_id"] != int(m[1]) for d in out[r["lecture"]]):
            out[r["lecture"]].append({"doc_id": int(m[1]), "title": r["ref"]})
    return out


async def pack(core: Core, con: sqlite3.Connection, exam: dict[str, Any], rules: dict[str, Any] | None,
               today: date) -> dict[str, Any]:
    course = str(exam["course"])
    lectures = lectures_of(exam)
    docs = lecture_docs(con, course, lectures)
    asked = lecture_questions(con, course, lectures)
    found = materials(con, int(exam["id"]))
    prep = [m for m in found if m["kind"] != "sheet"]
    sheet = [item for m in found if m["kind"] == "sheet" for item in m["contents"]]
    topics = sorted({t for m in prep for p in m["problems"] for t in p.get("topics", [])})
    held: dict[str, dict[str, int]] = {}
    per_lecture: list[dict[str, Any]] = []
    async with reads(core) as c:
        for n in lectures:
            q = lecture_query(course, [n])
            per_lecture.append({
                "lecture": n, "docs": docs.get(n, []), "query": q, "questions": asked.get(n, []),
                "cards": await count(c, q), "unseen": await count(c, f"{q} is:new"),
                "strong": await count(c, f"{q} -is:new prop:r>=0.9 prop:ivl>=21"),
                "held": await count(c, f"{q} -is:new prop:r>=0.85"),
                "weak": await count(c, f"{q} -is:new (prop:r<0.8 OR prop:lapses>=3)")})
        for t in topics:
            q = f"tag:{t}* -is:suspended"
            held[t] = {"cards": await count(c, q), "strong": await count(c, f"{q} -is:new prop:r>=0.9 prop:ivl>=21")}
    by_lecture = {x["lecture"]: x for x in per_lecture}
    for m in prep:
        for p in m["problems"]:
            lec = sum(by_lecture.get(n, {}).get("cards", 0) for n in p.get("lectures", []))
            top = sum(held.get(t, {}).get("cards", 0) for t in p.get("topics", []))
            p["cards"] = {"lectures": lec, "topics": top}
        m["uncovered"] = [p["number"] for p in m["problems"] if not p["cards"]["lectures"] and not p["cards"]["topics"]]
    since = date.fromordinal(today.toordinal() - 120).isoformat()
    mistakes = [m for m in graded.profile(con, today)["open_mistakes"] if m["course"] == course]
    total = sum(x["cards"] for x in per_lecture)
    return {
        "exam": {"id": exam["id"], "course": course, "title": exam["title"], "starts_at": exam["starts_at"],
                 "lectures": lectures, "mapped": bool(exam.get("confirmed")) and bool(lectures)},
        "rules": rules or {"sheet": "unknown", "detail": "not in config/courses.yaml: ask Aman, then add it there"},
        "formula_sheet": sheet if sheet else (
            "not filed yet: ask Aman to file this exam's sheet in Paperless (\"<COURSE> <Exam> Formula Sheet\")"
            if (rules or {}).get("sheet") == "provided" else None),
        "lectures": per_lecture,
        "readiness": round(sum(x["strong"] for x in per_lecture) / total, 2) if total else None,
        "prep": prep, "topics": held,
        "open_mistakes": mistakes,
        "probes": probes(con, course, since),
        "how_to_read": "strong = R ≥ 0.9 and mature (interval ≥ 21 d): hook candidates, still to be probed; held = seen "
                       "and R ≥ 0.85, the candidates early in a semester when nothing is mature yet; weak = "
                       "R < 0.8 or 3+ lapses. Each lecture's `query` finds its cards (mnemosyne_search_cards), and its "
                       "`questions` are the ones he wrote on its slides. A prep problem is uncovered when neither its "
                       "lectures nor its topics have any cards. `formula_sheet` is what the room hands him.",
    }


def lecture_questions(con: sqlite3.Connection, course: str, lectures: list[int]) -> dict[int, list[dict[str, Any]]]:
    """The questions he wrote on each lecture's slides, with their status: an open one is a confusion to probe."""
    out: dict[int, list[dict[str, Any]]] = {}
    for r in con.execute("SELECT lecture, asked, sharper, kind, status FROM question WHERE course = ? AND lecture IS NOT"
                         " NULL ORDER BY lecture, id", (course,)):
        if int(r["lecture"]) in lectures:
            out.setdefault(int(r["lecture"]), []).append({k: r[k] for k in ("asked", "sharper", "kind", "status")})
    return out


def pick_exam(con: sqlite3.Connection, course: str, title: str, today: date) -> dict[str, Any] | None:
    """'Exam II' (or '2'), else the next exam on or after today."""
    course = course.replace(" ", "").upper()
    if title.strip():
        ref = graded.work_ref(title if re.search(r"[a-z]", title, re.IGNORECASE) else f"Exam {title}")
        if ref is not None and ref[1] is not None:
            return exam_for(con, course, f"Exam {ref[1]}", today.isoformat())
    return exam_for(con, course, "", today.isoformat())


# ---------- reading a document's pages (lectures, prep, graded work) ----------

def read_pdf(pdf: bytes, doc_id: int, title: str, first: int, last: int, images: bool) -> dict[str, Any]:
    """Pages first..last (at most READ_PAGES) as text, with page images when asked (handwriting, figures)."""
    doc: Any = pymupdf.open(stream=pdf, filetype="pdf")  # pyright: ignore[reportUnknownMemberType]
    try:
        n = int(doc.page_count)
    finally:
        doc.close()
    first = max(1, first)
    last = min(n, last, first + READ_PAGES - 1)
    if first > n:
        raise ValueError(f"the document has {n} pages")
    got = pages(pdf, READ_PAGES if images else 0, 150, only=set(range(first, last + 1)), max_px=READ_PX, jpeg=True)
    out: dict[str, Any] = {"doc_id": doc_id, "title": title, "page_count": n,
                           "pages": [{"pdf_page": p.number, "text": p.text} for p in got],
                           "more": f"{last + 1}-{min(n, last + READ_PAGES)}" if last < n else None}
    if images:
        out["images"] = [{"pdf_page": p.number, "jpeg_base64": base64.b64encode(p.png).decode()} for p in got if p.png]
    return out


def page_range(spec: str) -> tuple[int, int]:
    """'3', '3-9', or '' (from the start)."""
    spec = spec.strip()
    if not spec:
        return 1, READ_PAGES
    m = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+))?", spec)
    if not m:
        raise ValueError("pages: '3' or '3-9'")
    first = int(m[1])
    return first, int(m[2]) if m[2] else first

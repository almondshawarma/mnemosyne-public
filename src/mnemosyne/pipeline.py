"""Lecture pipeline (PLAN §4.7): extract → propose → verify → dedupe → approve policy → drafts → write.

The Paperless poller calls `process` for each opted-in document; the API can run it on demand.
"""

import asyncio
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import structlog
import yaml

from mnemosyne import graded, merge, neighbors, papers, prep
from mnemosyne import questions as qstore
from mnemosyne.anki.core_client import AnkiCoreError, Core
from mnemosyne.cards import CardIn, Payload, Schema, Source, content_hash, payload_hash
from mnemosyne.extract import PageMark, fingerprint, lecture_label, pages, parse_title, same_deck, slug, unseen
from mnemosyne.generate.llm import RateLimited, Router, Truncated
from mnemosyne.generate.propose import Context, Lecture, extract_questions, generate, golden_reference, question_card
from mnemosyne.inbox import Verdict, default_decide, propose, unchecked_because
from mnemosyne.ingest import books
from mnemosyne.ingest.books import Book, Where
from mnemosyne.ingest.paperless import DONE, ERROR, OPT_IN, SKIP, Doc, Paperless
from mnemosyne.ledger import decks, store
from mnemosyne.ledger.db import now
from mnemosyne.library import Index
from mnemosyne.project import obsidian
from mnemosyne.project.hyperion import Hyperion
from mnemosyne.schedule import exams
from mnemosyne.verify import dedupe
from mnemosyne.verify.quantities import verify_card

log = structlog.get_logger()
MAX_ATTEMPTS = 2


class PipelineError(RuntimeError):
    pass


@dataclass
class Services:
    con: sqlite3.Connection
    schema: Schema
    core: Core
    llm: Router
    embedder: dedupe.Embedder
    paperless: Paperless
    repo: Path
    skill_dir: Path
    notes_dir: Path | None = None         # the vault's Sources/Paperless/: each lecture's stub holds its questions
    hyperion: Hyperion | None = None      # Helios MCP's hyperion_investigate, for questions past the course
    calibre_dir: Path | None = None       # the Calibre library, read-only: textbooks for the reading path
    calibre_url: str | None = None        # Calibre-Web, for links back to a book
    books_index: Index | None = None      # the books' full text: where a paper's prerequisites are covered

    @property
    def config(self) -> Path:
        return self.repo / "config"

    def yaml(self, name: str) -> dict[str, Any]:
        return dict(yaml.safe_load((self.config / name).read_text(encoding="utf-8")))


@dataclass
class Outcome:
    doc_id: int
    batch_id: str | None = None
    created: int = 0
    approved: int = 0
    review: int = 0
    duplicates: int = 0
    invalid: int = 0
    reasons: dict[str, int] = field(default_factory=dict[str, int])
    dropped: dict[str, str] = field(default_factory=dict[str, str])   # content_hash → the note it duplicates
    adopted: dict[int, list[str]] = field(default_factory=dict[int, list[str]])  # note id → course tags it gained
    questions: int = 0                    # new questions read off the slides
    pages: int = 0                        # pages carded: all of them, or only what changed since a carded copy
    unchanged: bool = False               # a copy of a deck already carded, with nothing new written on it
    proposal: dict[str, object] | None = None   # inbox.propose's summary of the batch, when one was proposed


def course_for(doc: Doc, tag_ids: dict[int, str], title_course: str | None) -> str | None:
    hits = [tag_ids[t] for t in doc.tags if t in tag_ids]
    return hits[0] if len(hits) == 1 else title_course


def first_pipeline_lecture(con: sqlite3.Connection, course: str) -> bool:
    """Calibration (PLAN §4.8): the first lecture per course that the pipeline processes gets full review."""
    row = con.execute("SELECT COUNT(*) FROM card_source WHERE origin = 'paperless' AND course = ?", (course,)).fetchone()
    return int(row[0]) == 0


def policy(card: CardIn, kind: str, verify: tuple[str, str], match: dedupe.Match, calibrating: bool,
           approval: dict[str, Any]) -> Verdict:
    reasons: list[str] = [f"question: {card.question}"] if card.question else []
    status, detail = verify
    if status == "fail":
        reasons.append(f"verify_failed: {detail}")
    elif status == "skip":
        reasons.append(f"verify_skipped: {detail}")
    if match.kind == "flag":
        reasons.append(f"dedupe_flagged ({match.score:.2f}): {match.text[:120]}")
    if calibrating:
        reasons.append("calibration: first pipeline lecture for this course")
    if kind not in approval.get("auto_approve", []):
        reasons.append(f"{kind} needs human review")
    if reasons:
        return Verdict(approved=False, reason="; ".join(reasons))
    return Verdict(approved=True, probation=True)


def first_field(card: CardIn) -> str:
    return str(next(iter(card.fields.values()), ""))


async def lecture_cards(svc: Services, course: str, span: range) -> list[str]:
    """The prompts of the cards these lectures already have in Anki."""
    query = " OR ".join(f"tag:course::{course.lower()}::l{n:02d}*" for n in span)
    existing = await svc.core.call("anki_notes_search", {"query": f"({query})" if len(span) > 1 else query, "limit": 100})
    return [dedupe.normalize(str(i["first_field"])) for i in existing.get("items", [])]


async def process(svc: Services, doc: Doc, tag_ids: dict[int, str]) -> Outcome:
    ref = parse_title(doc.title)
    course = course_for(doc, tag_ids, ref.course if ref else None)
    if course is None or ref is None:
        raise PipelineError(f"can't tell course and lecture from {doc.title!r}")
    limits = svc.yaml("llm.yaml").get("limits", {})
    pdf = await svc.paperless.download(doc.id)
    marks = fingerprint(pdf)
    # Slides a carded copy of this deck already had, unmarked since, are left out (another copy, or the next lecture's)
    prev = decks.predecessor(svc.con, doc.id)
    fresh = unseen(marks, decks.seen(svc.con, course, {m.key for m in marks if m.key}, doc.id),
                   decks.marks(svc.con, prev) if prev is not None else None)
    if not fresh:
        decks.save(svc.con, doc.id, course, marks)
        log.info("lecture_unchanged", doc=doc.id, title=doc.title, pages=len(marks))
        return Outcome(doc.id, unchanged=True)
    partial = len(fresh) < len(marks)
    page_list = pages(pdf, int(limits.get("max_pages", 60)), int(limits.get("image_dpi", 110)),
                      only=set(fresh) if partial else None)
    # A long deck goes in parts: 55 slide images plus the canon came to over the gateway's 100k tokens a minute in
    # one request (PHYS 207 L09, 2026-10-07), which fails as a "rate limit" however long it waits.
    size = max(1, int(limits.get("chunk_pages", 24)))
    parts = [page_list[i:i + size] for i in range(0, len(page_list), size)]
    link, notes = svc.paperless.link(doc.id), await svc.paperless.notes(doc.id)
    existing = await lecture_cards(svc, course, ref.span)
    skill_md = (svc.skill_dir / "SKILL.md").read_text(encoding="utf-8")
    golden = golden_reference(svc.skill_dir / "golden", course)
    ctx = Context.load(svc.config, svc.repo / "data" / "anchors.yaml")
    cards: list[CardIn] = []
    questions: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    invalid = 0
    source: Source | None = None
    while parts:
        part = parts.pop(0)
        label = (f"pages {part[0].number}-{part[-1].number} of {len(marks)}"
                 if len(part) < len(page_list) else None)
        lec = Lecture(course=course, lecture=ref.lecture, title=ref.title, kind="slides", ref=doc.title, link=link,
                      emphasis=notes, existing=existing + [dedupe.normalize(first_field(c)) for c in cards],
                      pages=part, partial=partial, part=label)
        try:
            gen = await generate(svc.llm, "lecture", skill_md, golden, lec, svc.schema, ctx)
        except Truncated as e:
            # A dense part's cards ran past max_tokens (PHYS 221 L06 and L07, 2026-10-20): card it in halves.
            if len(part) < 2:
                raise PipelineError(f"one page's cards don't fit in a reply ({label}): {e}") from e
            half = len(part) // 2
            parts[:0] = [part[:half], part[half:]]
            log.info("lecture_part_split", doc=doc.id, part=label, into=[len(part[:half]), len(part[half:])])
            continue
        if gen.payload is None:
            raise PipelineError(f"no valid cards{f' ({label})' if label else ''}: {gen.problems[:3]}")
        source = source or gen.payload.source
        cards += gen.payload.cards
        questions += gen.questions
        skips += gen.skipped
        invalid += len(gen.problems)
    assert source is not None
    for card in cards:
        card.fields["SourceLink"] = link or ""

    out = await check_and_propose(svc, Payload(source=source, cards=cards), "paperless", "slides",
                                  first_pipeline_lecture(svc.con, course), doc.id)
    out.invalid = invalid
    out.pages = len(fresh)
    store.save_skipped(svc.con, out.batch_id, skips)
    out.questions = await take_questions(svc, doc, course, ref.lecture, questions)
    decks.save(svc.con, doc.id, course, marks)
    log.info("lecture_processed", doc=doc.id, title=doc.title, **{k: v for k, v in vars(out).items() if k != "doc_id"})
    return out


CONNECTING = merge.CONNECTING


async def take_questions(svc: Services, doc: Doc, course: str, lecture: int, items: list[dict[str, Any]]) -> int:
    """Keep the questions Aman wrote on the slides, send the ones that reach past the course to Hyperion (a few per
    lecture, config/annotations.yaml), and write the lecture's note in the vault."""
    new = qstore.save_from_lecture(svc.con, doc.id, course, lecture, doc.title, doc.added, items)
    cfg: dict[str, Any] = (svc.yaml("annotations.yaml") if (svc.config / "annotations.yaml").exists() else {}).get(
        "hyperion") or {}
    if svc.hyperion is not None and cfg.get("auto_beyond_course", False):
        far = [q for q in (qstore.get(svc.con, i) for i in new) if q and q["beyond_course"]]
        for q in far[:int(cfg.get("max_per_lecture", 2))]:
            await ask_hyperion(svc.con, svc.hyperion, q)
    if svc.notes_dir is not None:
        try:
            obsidian.sync_doc(svc.con, svc.notes_dir, doc.id)
        except OSError as e:
            log.warning("lecture_note_failed", doc=doc.id, error=str(e)[:200])
    return len(new)


async def questions_only(svc: Services, doc_id: int) -> dict[str, Any]:
    """A questions-only pass over a filed lecture: its handwritten questions (kept like any others) and its must-card
    marks (returned, to compare with the cards it has). No cards are written."""
    doc = await svc.paperless.document(doc_id)
    ref = parse_title(doc.title)
    t = await tags(svc)
    course = course_for(doc, t.courses, ref.course if ref else None)
    if course is None or ref is None:
        raise PipelineError(f"can't tell course and lecture from {doc.title!r}")
    limits = svc.yaml("llm.yaml").get("limits", {})
    pdf = await svc.paperless.download(doc.id)
    lec = Lecture(course=course, lecture=ref.lecture, title=ref.title, kind="slides", ref=doc.title,
                  link=svc.paperless.link(doc.id), emphasis=[], existing=await lecture_cards(svc, course, ref.span),
                  pages=pages(pdf, int(limits.get("max_pages", 60)), int(limits.get("image_dpi", 110))))
    skill_md = (svc.skill_dir / "SKILL.md").read_text(encoding="utf-8")
    found, stars = await extract_questions(svc.llm, "lecture", skill_md, lec, svc.schema,
                                           Context.load(svc.config, svc.repo / "data" / "anchors.yaml"))
    new = await take_questions(svc, doc, course, ref.lecture, found)
    log.info("questions_extracted", doc=doc_id, found=len(found), new=new, starred=len(stars))
    return {"doc": doc_id, "title": doc.title, "found": len(found), "new": new, "must_card": stars}


async def hyperion_brief(hyperion: Hyperion | None, iid: str | None) -> str | None:
    """A finished Hyperion brief on a question, when there is one worth reading."""
    if hyperion is None or not iid:
        return None
    try:
        got = await hyperion.investigation(iid)
    except Exception as e:  # noqa: BLE001 -- the brief is a lead; the card is written without it
        log.warning("hyperion_brief_failed", investigation=iid, error=str(e)[:200])
        return None
    brief = str(got.get("brief") or "")
    return brief if got.get("status") == "done" and "No synthesis" not in brief else None


async def write_question_card(svc: Services, q: dict[str, Any]) -> dict[str, Any]:
    """The card a question's answer teaches, for a question that came without one. Kept on the question for Aman's
    Card it press, or with the reason it has none."""
    ref = parse_title(str(q.get("lecture_title") or ""))
    course = str(q.get("course") or "")
    existing = await lecture_cards(svc, ref.course, ref.span) if ref else []
    golden = golden_reference(svc.skill_dir / "golden", course) if course else ""
    skill_md = (svc.skill_dir / "SKILL.md").read_text(encoding="utf-8")
    card, why, problems = await question_card(svc.llm, "question", skill_md, golden, q, svc.schema,
                                              Context.load(svc.config, svc.repo / "data" / "anchors.yaml"), existing,
                                              await hyperion_brief(svc.hyperion, q.get("hyperion_id")))
    if card or why:
        qstore.set_card(svc.con, int(q["id"]), card, why)
    log.info("question_card", question=q["id"], card=card is not None, no_card=why, problems=problems[:3])
    return {"id": q["id"], "card": card, "no_card": why, "problems": problems}


async def ask_hyperion(con: sqlite3.Connection, hyperion: Hyperion, q: dict[str, Any]) -> str | None:
    """Queue one question as a Hyperion investigation: the sharper wording, with the lecture for context."""
    subject = str(q.get("sharper") or q["asked"])
    if q.get("lecture_title"):
        subject += f" (context: {q['lecture_title']})"
    try:
        got = await hyperion.investigate(subject)
    except Exception as e:  # noqa: BLE001 -- Hyperion is optional; the question stays open either way
        log.warning("hyperion_failed", question=q["id"], error=str(e)[:200])
        return None
    iid = str(got.get("id") or "")
    if iid:
        qstore.set_hyperion(con, int(q["id"]), iid)
    return iid or None


async def adopt(core: Core, found: list[tuple[CardIn, dedupe.Match]],
                con: sqlite3.Connection | None = None) -> dict[int, list[str]]:
    """A dropped duplicate hands what connects it to the card that already holds the idea: its course tags (so one
    card serves every course that teaches it and lands in each exam deck), and its topic:: and principle:: tags
    (canon 22: more connections, not more cards). Tags only add; nothing else changes.

    The existing card is a note in Anki (positive id) or a draft still waiting in the Ledger (negative id)."""
    gains: dict[int, set[str]] = {}
    for card, match in found:
        tags = {t for t in card.tags if t.startswith(CONNECTING)}
        if match.kind == "duplicate" and match.note_id is not None and tags:
            gains.setdefault(match.note_id, set()).update(tags)
    out: dict[int, list[str]] = {}
    for key, tags in gains.items():
        if key < 0:
            if con is not None and (gained := store.add_draft_tags(con, -key, sorted(tags))):
                out[key] = gained
                log.info("duplicate_adopted_by_draft", draft=-key, tags=gained)
            continue
        nid = key
        digest = hashlib.sha1(" ".join(sorted(tags)).encode()).hexdigest()[:12]
        await core.call("anki_notes_add_tags", {"note_ids": [nid], "tags": sorted(tags),
                                                "idempotency_key": f"mn-adopt-{nid}-{digest}"})
        log.info("duplicate_adopted", note=nid, tags=sorted(tags))
        out[nid] = sorted(tags)
    return out


async def check_and_propose(svc: Services, payload: Payload, origin: str, kind: str, calibrating: bool,
                            doc_id: int = 0, hold: bool = False) -> Outcome:
    """Verify gate + dedupe + approval policy, then drafts (auto-approved ones on probation). Shared by the
    lecture pipeline and by cards Claude writes in a session (`/inbox` with `checks: true`). `hold` keeps every
    card for Aman's review (the /review page) while still recording what the checks found."""
    cards = payload.cards
    anchors = svc.repo / "data" / "anchors.yaml"
    verdicts = {content_hash(c): verify_card(c.note_type, c.tags, [ch.model_dump() for ch in c.checks], anchors)
                for c in cards}
    notes = await dedupe.existing_notes(svc.core, svc.con)
    # Drafts waiting for review count too, under negative ids so they never collide with Anki note ids.
    notes |= {-did: neighbors.note_text(t, f, tags) for did, (t, f, tags) in store.open_drafts(svc.con).items()}
    texts = [dedupe.card_text(c) for c in cards]
    model = dedupe.model_of(svc.embedder) or "unknown"
    try:
        ids, matrix = await dedupe.index(svc.con, notes, svc.embedder)
        found = await dedupe.classify(texts, ids, matrix, notes, svc.embedder)
    except (httpx.HTTPError, OSError) as e:
        # Ollama down (2026-10-02: a GPU driver update left it unable to start until a reboot): don't lose the
        # cards, hold them with the reason shown.
        log.warning("dedupe_unavailable", error=str(e)[:200])
        found, model = dedupe.exact(texts, notes), "exact"
    matches = dict(zip((content_hash(c) for c in cards), found, strict=True))
    # Which Anki note each duplicate or flagged card matched, and whether it's the more fundamental framing of it
    # (canon 22): then it isn't dropped but held as a merge-up, so the note can take its wording.
    into = await merge.targets(svc.core, merge.Foundations.load(svc.config), [(c, matches[content_hash(c)]) for c in cards])
    up = {h for h, (_nid, u) in into.items() if u}
    # Held cards keep their suspected duplicates: Aman sees the match and decides (embeddings misfire, e.g. the
    # field on a ring's axis against the potential on it). Otherwise duplicates are dropped, and recorded with the
    # whole card so the weekly audit can restore any that weren't repeats (Aman, 2026-10-29).
    kept = cards if hold else [c for c in cards if matches[content_hash(c)].kind != "duplicate" or content_hash(c) in up]
    record_matches(svc.con, payload, origin, matches, {content_hash(c) for c in kept}, model)
    approval = svc.yaml("approval.yaml")

    def decide(_i: int, card: CardIn) -> Verdict:
        h = content_hash(card)
        v, match = verdicts[h], matches[h]
        nid, merge_up = into.get(h, (None, False))
        verdict = policy(card, kind, (v.status, v.detail), match, calibrating, approval)
        first = []
        if match.kind == "duplicate" and (hold or merge_up):
            first = [f"{'merge_up' if merge_up else 'dedupe_duplicate'} ({match.score:.2f}): {match.text[:120]}"]
        elif not hold:
            return replace(verdict, merge_into=nid, merge_up=merge_up) if nid is not None and not verdict.approved else verdict
        return Verdict(approved=False, reason="; ".join([*first, *filter(None, [verdict.reason])]) or None,
                       merge_into=nid, merge_up=merge_up)

    out = Outcome(doc_id, duplicates=len(cards) - len(kept),
                  dropped={} if hold else {h: m.text[:200] for h, m in matches.items() if m.kind == "duplicate" and h not in up})
    if not hold:
        out.adopted = await adopt(svc.core, [(c, matches[content_hash(c)]) for c in cards if content_hash(c) not in up],
                                  svc.con)
    if kept:
        result = propose(svc.con, svc.schema, Payload(source=payload.source, cards=kept), origin, decide)
        out.batch_id, out.created, out.proposal = result.batch_id, result.created, result.summary()
        for card in kept:
            verdict = decide(0, card)
            if verdict.approved:
                out.approved += 1
            else:
                out.review += 1
                for reason in filter(None, (verdict.reason or "").split("; ")):
                    key = reason.split(":")[0].split(" (")[0]
                    out.reasons[key] = out.reasons.get(key, 0) + 1
    return out


def record_matches(con: sqlite3.Connection, payload: Payload, origin: str, matches: dict[str, dedupe.Match],
                   kept: set[str], model: str) -> None:
    """Every card dedupe checked, with its nearest note and score (dedupe_match); a dropped card keeps its payload."""
    source = payload.source.model_dump()
    rows = [(h, origin, m.kind, round(m.score, 4), m.note_id, m.text[:300], int(h not in kept),
             json.dumps({"source": source, "card": c.model_dump()}, ensure_ascii=False) if h not in kept else None,
             model, dedupe.REPR, now())
            for c in payload.cards if (m := matches.get(h := content_hash(c))) is not None]
    con.execute("BEGIN IMMEDIATE")
    con.executemany("INSERT OR REPLACE INTO dedupe_match (content_hash, origin, kind, score, note_id, matched, dropped,"
                    " card_json, model, repr, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute("COMMIT")


async def propose_checked(svc: Services, payload: Payload, origin: str) -> dict[str, object]:
    """A proposal from a chat (the MCP tool) or Telegram: every card held for Aman, with what the verify gate and
    dedupe found. Holding means nothing is approved here, so the propose token is enough (invariant 7). The answer
    is shaped like inbox.Proposal.summary(), which the MCP tool and the Telegram hook read, plus `held` and `reasons`.
    If the checks can't run (anki-core unreachable), the cards are still kept, held with the reason."""
    if store.source_by_hash(svc.con, payload_hash(payload)) is not None:
        return propose(svc.con, svc.schema, payload, origin).summary()     # already proposed: nothing changes
    try:
        out = await check_and_propose(svc, payload, origin, payload.source.kind, calibrating=False, hold=True)
    except (AnkiCoreError, httpx.HTTPError, OSError) as e:
        log.warning("proposal_checks_unavailable", origin=origin, error=str(e)[:200])
        why = unchecked_because(f"anki-core: {str(e)[:120]}")
        return propose(svc.con, svc.schema, payload, origin, default_decide(origin, why)).summary()
    summary = out.proposal or {"batch_id": None, "status": "empty", "created": 0, "duplicates": 0, "invalid": [],
                               "already_proposed": False}
    return {**summary, "held": out.review, "reasons": out.reasons}


# ---------- the poller ----------

def job_attempts(con: sqlite3.Connection, ref: int | str, kind: str = "paperless") -> tuple[str | None, int]:
    row = con.execute("SELECT status, attempts FROM ingest_job WHERE kind = ? AND ref = ?",
                      (kind, str(ref))).fetchone()
    return (None, 0) if row is None else (str(row["status"]), int(row["attempts"]))


def charged(e: Exception) -> int:
    """A failure counts toward MAX_ATTEMPTS unless the provider was only throttling us: then the next poll tries
    again, and the document isn't tagged as an error for the gateway's busy minute."""
    return 0 if isinstance(e, RateLimited) else 1


def record_job(con: sqlite3.Connection, ref: int | str, status: str, attempts: int, error: str | None = None,
               kind: str = "paperless") -> None:
    """Jobs are keyed by kind and ref: a Paperless document id, or a reading ('chen:s2-3', 'paperless:512')."""
    stamp = now()
    con.execute("INSERT INTO ingest_job (kind, ref, status, attempts, error, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (kind, ref) DO UPDATE SET"
                " status = excluded.status, attempts = excluded.attempts, error = excluded.error,"
                " updated_at = excluded.updated_at", (kind, str(ref), status, attempts, error, stamp, stamp))


@dataclass(frozen=True)
class Tags:
    courses: dict[int, str]
    semester: int
    lecture_type: int
    done: int
    error: int
    excluded: list[int]
    opt_in: int | None
    exam_type: int | None = None
    assignment_type: int | None = None
    graded: int | None = None             # the `graded` tag: an Assignment returned with marks
    paper_type: int | None = None         # Paper: a research paper, carded when tagged `mnemosyne`


async def tags(svc: Services) -> Tags:
    courses = svc.yaml("courses.yaml")
    p = svc.paperless
    course_ids: dict[int, str] = {}
    for c in courses["courses"]:
        if (tid := await p.tag_id(c["paperless_tag"])) is not None:
            course_ids[tid] = c["code"]
    semester = await p.tag_id(courses["paperless_semester_tag"])
    lecture_type = await p.document_type_id("Lecture")
    done, error = await p.tag_id(DONE, create=True), await p.tag_id(ERROR, create=True)
    skip, opt_in = await p.tag_id(SKIP, create=True), await p.tag_id(OPT_IN, create=True)
    if semester is None or lecture_type is None or not course_ids or done is None or error is None:
        raise PipelineError("Paperless is missing the semester tag, the Lecture type, or course tags")
    return Tags(course_ids, semester, lecture_type, done, error, [t for t in (done, error, skip) if t is not None], opt_in,
                await p.document_type_id("Exam"), await p.document_type_id("Assignment"), await p.tag_id("graded"),
                await p.document_type_id("Paper"))


async def run_one(svc: Services, doc: Doc, t: Tags) -> Outcome | None:
    """Process one document with job bookkeeping; tags it done, or error after two failed tries."""
    status, attempts = job_attempts(svc.con, doc.id)
    if status == "done":
        return None
    record_job(svc.con, doc.id, "running", attempts + 1)
    try:
        outcome = await process(svc, doc, t.courses)
    except Exception as e:  # noqa: BLE001 -- recorded on the job; after two tries the document is tagged
        tries = attempts + charged(e)
        record_job(svc.con, doc.id, "error", tries, str(e)[:2000])
        log.warning("lecture_failed", doc=doc.id, title=doc.title, attempt=tries, error=str(e)[:300])
        if charged(e) and tries >= MAX_ATTEMPTS:
            await svc.paperless.add_tag(doc.id, t.error)
        return None
    record_job(svc.con, doc.id, "done", attempts + 1)
    await svc.paperless.add_tag(doc.id, t.done)
    await svc.paperless.remove_tag(doc.id, t.error)      # an earlier failed try may have tagged it
    return outcome


# ---------- stuck documents: carded in a Claude session instead (2026-10-07) ----------
# When the TAMU gateway's daily allowance runs out, lectures wait (RateLimited spends no try). Aman can card them on
# his own Claude account instead: a Claude Code session reads the deck (mnemosyne_doc_read), proposes the cards with
# the deck's Paperless link as source.link, then settles the document so the pipeline doesn't card it again.

STUCK_KINDS = ("paperless", "graded", "prep", "paper")


def throttled(error: str | None) -> bool:
    text = (error or "").lower()
    return "rate limit" in text or "throttled" in text


def problem(error: str | None) -> str:
    """A failed job's error in plain words, for the review page and Telegram."""
    text = (error or "").strip()
    low = text.lower()
    if throttled(text):
        return "Waiting for the TAMU allowance: it tries again on every poll."
    if "can't tell course and lecture" in low:
        return "Its title doesn't say the course and lecture (like \"PHYS 221 L06\"), so it couldn't be filed."
    if "unparseable output" in low or "max_tokens" in low or "truncated" in low:
        return "The model's reply was cut off or garbled, so no cards could be read from it."
    if "no valid cards" in low:
        return "None of the cards the model wrote passed the checks."
    if re.search(r"http 5\d\d|timed? ?out", low):
        return "The TAMU gateway timed out or failed while carding it."
    return (text.splitlines() or ["no error was recorded"])[0][:200]


def stuck(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents whose job hasn't finished: waiting on the gateway, or failed for real (gave_up: the poller has
    stopped trying it; only a retry from the review page runs it again). One entry per document, its latest job."""
    marks = ",".join("?" * len(STUCK_KINDS))
    # Not a copy that a newer one replaced, and not one another job finished (a review sheet once tried as a lecture)
    rows = con.execute(f"SELECT kind, ref, attempts, error, updated_at FROM ingest_job j WHERE status = 'error'"
                       f" AND kind IN ({marks})"
                       " AND ref NOT IN (SELECT CAST(replaces AS TEXT) FROM doc_version)"
                       " AND NOT EXISTS (SELECT 1 FROM ingest_job d WHERE d.ref = j.ref AND d.status = 'done')"
                       " ORDER BY updated_at", STUCK_KINDS)
    latest = {int(r["ref"]): r for r in rows if str(r["ref"]).isdigit()}
    return [{"doc_id": doc_id, "job": str(r["kind"]), "tries": int(r["attempts"]),
             "waiting_on_gateway": throttled(r["error"]),
             "gave_up": not throttled(r["error"]) and int(r["attempts"]) >= MAX_ATTEMPTS,
             "problem": problem(r["error"]), "error": str(r["error"] or "")[:240], "since": str(r["updated_at"])}
            for doc_id, r in sorted(latest.items(), key=lambda kv: str(kv[1]["updated_at"]))]


async def present(paperless: Paperless, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stuck documents with their titles and links, minus any no longer in Paperless (a copy trashed by hand)."""
    out: list[dict[str, Any]] = []
    for s in items:
        title: str | None = None
        try:
            title = (await paperless.document(int(s["doc_id"]))).title
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                continue
        except Exception as e:  # noqa: BLE001 -- a title is a nicety; the failure still shows
            log.warning("stuck_title_unavailable", doc=s["doc_id"], error=str(e)[:200])
        out.append({**s, "title": title, "link": paperless.link(int(s["doc_id"]))})
    return out


def failure_key(doc_id: int) -> str:
    return f"failed:{doc_id}"


def failures_due(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Documents the poller gave up on that Telegram hasn't mentioned yet: one notice per document, ever."""
    sent = {str(r["key"]) for r in con.execute("SELECT key FROM notice WHERE key LIKE 'failed:%'")}
    return [s for s in stuck(con) if s["gave_up"] and failure_key(s["doc_id"]) not in sent]


FAILED_VERB = {"paperless": "card", "graded": "debrief", "prep": "file", "paper": "read"}


def failure_text(item: dict[str, Any], review_url: str) -> str:
    name = item.get("title") or f"document {item['doc_id']}"
    return (f"⚠️ Mnemosyne couldn't {FAILED_VERB.get(str(item['job']), 'process')} {name} "
            f"(tried {item['tries']}×).\n{item['problem']}\n\nIt won't try again by itself. "
            f"Retry it from the review page: {review_url}")


def session_sources(con: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    """Cards proposed outside the pipeline whose source is this Paperless document."""
    return con.execute("SELECT course, lecture FROM card_source WHERE origin != 'paperless' AND link LIKE ?",
                       (f"%/documents/{doc_id}/%",)).fetchall()


async def settle(svc: Services, doc_id: int) -> dict[str, Any]:
    """A lecture a session carded: record its slides' fingerprints (so a re-send cards only what's new), mark its job
    done, and tag it done. Refused unless the session proposed cards for it: settling can't be used to skip one."""
    status, attempts = job_attempts(svc.con, doc_id)
    if status == "done":
        return {"doc_id": doc_id, "settled": False, "reason": "already done: carded by the pipeline or settled before"}
    sources = session_sources(svc.con, doc_id)
    if not sources:
        raise PipelineError("no cards were proposed for this document yet: propose them first, with its Paperless "
                            "link as source.link")
    doc, t = await svc.paperless.document(doc_id), await tags(svc)
    if doc.document_type != t.lecture_type:
        raise PipelineError(f"{doc.title!r} isn't a lecture: graded work, prep material, and papers wait for the "
                            "pipeline (they write more than cards)")
    ref = parse_title(doc.title)
    course = course_for(doc, t.courses, ref.course if ref else None) or str(sources[0]["course"] or "")
    decks.save(svc.con, doc_id, course, fingerprint(await svc.paperless.download(doc_id)))
    record_job(svc.con, doc_id, "done", attempts)
    await svc.paperless.add_tag(doc_id, t.done)
    await svc.paperless.remove_tag(doc_id, t.error)
    log.info("document_settled", doc=doc_id, title=doc.title, sources=len(sources))
    return {"doc_id": doc_id, "settled": True, "title": doc.title, "batches": len(sources)}


def nudge_key(today: date) -> str:
    return f"stuck:{today.isoformat()}"


def nudge_due(con: sqlite3.Connection, today: date) -> bool:
    """Once a day, while anything waits on the gateway."""
    waiting = any(s["waiting_on_gateway"] for s in stuck(con))
    sent = con.execute("SELECT 1 FROM notice WHERE key = ?", (nudge_key(today),)).fetchone()
    return waiting and sent is None


def nudge_text(titles: list[str]) -> str:
    listed = "\n".join(f"• {t}" for t in titles)
    return ("⏳ Waiting on the TAMU gateway (its daily allowance looks used up):\n" + listed +
            "\n\nThey'll card themselves once it resets. To card them now on your own Claude account, open "
            "Claude Code and say \"card my stuck lectures\".")


def mark_notice(con: sqlite3.Connection, key: str, message_ref: str | None) -> None:
    con.execute("INSERT OR IGNORE INTO notice (key, sent_at, message_ref) VALUES (?, ?, ?)", (key, now(), message_ref))


async def poll(svc: Services) -> list[Outcome]:
    """One pass over Paperless: lectures and returned exams added since go-live, plus anything tagged `mnemosyne`
    (readings, syllabi, graded homework, older documents)."""
    t = await tags(svc)
    go_live = date.fromisoformat(str(svc.yaml("courses.yaml")["intake_go_live"]))
    defaults = [x for x in (t.lecture_type, t.exam_type) if x is not None]
    docs = await svc.paperless.candidates(t.semester, list(t.courses), defaults, t.excluded, go_live, t.opt_in)
    out = [o for doc in docs if doc.document_type == t.lecture_type and not is_prep(doc, t)
           and (o := await run_one(svc, doc, t)) is not None]
    for doc in docs:
        if is_prep(doc, t) and (o := await run_prep(svc, doc, t)) is not None:
            out.append(o)
    for doc in docs:
        if is_graded(doc, t) and (o := await run_graded(svc, doc, t)) is not None:
            out.append(o)
    for doc in docs:
        if is_paper(doc, t) and (o := await run_paper(svc, doc, t)) is not None:
            out.append(o)
    syllabus_type = await svc.paperless.document_type_id("Syllabus")
    # Scans of a textbook, tagged `mnemosyne` and titled like "Chen 2.3". Untitled ones wait on the review page.
    catalog = books.load(svc.config / "books.yaml")
    for doc in docs:
        if (t.opt_in is None or t.opt_in not in doc.tags
                or doc.document_type in {x for x in (t.lecture_type, syllabus_type, t.exam_type, t.assignment_type,
                                                     t.paper_type) if x is not None}):
            continue
        found = books.parse_title(doc.title, catalog)
        if found is not None and (o := await run_reading(svc, found[0], found[1], doc)) is not None:
            out.append(o)
    for doc in docs:
        if doc.document_type == syllabus_type and syllabus_type is not None and job_attempts(svc.con, doc.id)[0] != "done":
            course = course_for(doc, t.courses, None)
            if course is None:
                continue
            try:
                await build_map(svc, course, doc)
                record_job(svc.con, doc.id, "done", 1)
                await svc.paperless.add_tag(doc.id, t.done)
            except Exception as e:  # noqa: BLE001
                record_job(svc.con, doc.id, "error", 1, str(e)[:2000])
                log.warning("exam_map_failed", course=course, doc=doc.id, error=str(e)[:300])
    return out


# ---------- exam prep material: review sheets, practice exams, keys (prep.py) ----------

def is_prep(doc: Doc, t: Tags) -> bool:
    """A review sheet, practice exam, or key: a Lecture or Exam titled so, with no lecture number."""
    return (doc.document_type in {x for x in (t.lecture_type, t.exam_type) if x is not None}
            and prep.kind_of(doc.title) is not None)


async def attach_prep(svc: Services, doc: Doc, t: Tags) -> Outcome:
    """Read it for its problems and attach it to its exam. No cards."""
    kind = prep.kind_of(doc.title) or "review"
    course = course_for(doc, t.courses, course_in_title(doc.title))
    if course is None:
        raise PipelineError(f"can't tell the course of {doc.title!r}")
    exam = prep.exam_for(svc.con, course, doc.title, doc.added)
    titles = {int(r["lecture"]): str(r["ref"]) for r in svc.con.execute(
        "SELECT lecture, ref FROM card_source WHERE course = ? AND kind = 'slides' AND lecture IS NOT NULL", (course,))}
    lecture_titles = tuple((n, titles.get(n, "")) for n in prep.lectures_of(exam))
    found = prep.Doc(doc.id, course, kind, doc.title, svc.paperless.link(doc.id), exam, lecture_titles,
                     tuple(await svc.paperless.notes(doc.id)))
    limits = svc.yaml("llm.yaml").get("limits", {})
    page_list = pages(await svc.paperless.download(doc.id), int(limits.get("max_pages", 60)), 200,
                      max_px=int(limits.get("graded_max_px", 1568)), jpeg=True)
    material = await prep.read(svc.llm, Context.load(svc.config, svc.repo / "data" / "anchors.yaml"), found, page_list)
    prep.save(svc.con, found, material)
    log.info("prep_attached", doc=doc.id, title=doc.title, kind=kind, exam=exam["title"] if exam else None,
             problems=len(material.problems))
    return Outcome(doc.id)


def course_in_title(title: str) -> str | None:
    """'MATH 309 Exam 1 Review' → MATH309."""
    m = re.match(r"\s*([A-Za-z]{3,4})\s*(\d{3})(?!\d)", title)
    return f"{m[1].upper()}{m[2]}" if m else None


async def run_prep(svc: Services, doc: Doc, t: Tags, again: bool = False) -> Outcome | None:
    """One prep document with job bookkeeping (ingest_job kind 'prep'). A marked practice exam is also graded work:
    that job tags it done, so this one leaves the tag alone."""
    status, attempts = job_attempts(svc.con, doc.id, "prep")
    if status == "done" and not again:
        return None
    record_job(svc.con, doc.id, "running", attempts + 1, kind="prep")
    try:
        out = await attach_prep(svc, doc, t)
    except Exception as e:  # noqa: BLE001 -- recorded on the job; after two tries the document is tagged
        tries = attempts + charged(e)
        record_job(svc.con, doc.id, "error", tries, str(e)[:2000], kind="prep")
        log.warning("prep_failed", doc=doc.id, title=doc.title, attempt=tries, error=str(e)[:300])
        if charged(e) and tries >= MAX_ATTEMPTS:
            await svc.paperless.add_tag(doc.id, t.error)
        return None
    record_job(svc.con, doc.id, "done", attempts + 1, kind="prep")
    if not is_graded(doc, t):
        await svc.paperless.add_tag(doc.id, t.done)
    await svc.paperless.remove_tag(doc.id, t.error)
    return out


# ---------- graded work: returned exams, quizzes, homework (PLAN §4.6, graded.py) ----------

def is_graded(doc: Doc, t: Tags) -> bool:
    """A returned exam (in by default, like lectures), or an Assignment tagged `graded` or `mnemosyne` (a blank
    assignment is only context, so it needs saying). Practice exams, reviews, and keys are filed as Exam too: they
    need the tag, as one he took and marked himself would have."""
    marked = any(x is not None and x in doc.tags for x in (t.graded, t.opt_in))
    if doc.document_type is not None and doc.document_type == t.exam_type:
        return marked or not NOT_RETURNED.search(doc.title)
    return doc.document_type is not None and doc.document_type == t.assignment_type and marked


NOT_RETURNED = re.compile(r"\b(practice|review|key|solutions?|mock)\b", re.IGNORECASE)


async def debrief(svc: Services, doc: Doc, t: Tags) -> Outcome:
    """Diagnose every point lost, card the misses a card can fix (held for review), keep the mistakes for the tutor,
    and write the debrief into the document's stub."""
    work = await graded_work(svc, doc, t)
    limits = svc.yaml("llm.yaml").get("limits", {})
    # The grading is handwriting, so the pages go as large as the model reads them (scans come in as huge pages)
    page_list = pages(await svc.paperless.download(doc.id), int(limits.get("max_pages", 60)), 300,
                      max_px=int(limits.get("graded_max_px", 1568)), jpeg=True)
    skill_md = (svc.skill_dir / "SKILL.md").read_text(encoding="utf-8")
    ctx = Context.load(svc.config, svc.repo / "data" / "anchors.yaml")
    found, problems, _raw = await graded.diagnose(svc.llm, skill_md, ctx, work, page_list, svc.schema)
    if found is None:
        raise PipelineError(f"no debrief: {problems[:3]}")
    if problems:
        log.warning("debrief_problems", doc=doc.id, problems=problems[:5])
    return await land_debrief(svc, work, found, len(problems))


class Rejected(PipelineError):
    """A debrief written in a session that breaks the checks: nothing is written, and the problems go back."""

    def __init__(self, problems: list[dict[str, Any]]) -> None:
        super().__init__(f"{len(problems)} problems")
        self.problems = problems


async def debrief_written(svc: Services, doc: Doc, t: Tags, written: dict[str, Any]) -> Outcome:
    """A debrief written in a Claude session, for work the pipeline couldn't debrief (the gateway's allowance ran out,
    or the reply ran past max_tokens). It is checked exactly like the model's, but strictly: any problem rejects the
    whole debrief, since the session can fix it and send it again. Then it lands the same way, and the job is done."""
    work = await graded_work(svc, doc, t)
    anchors = Context.load(svc.config, svc.repo / "data" / "anchors.yaml").anchors
    found, problems = graded.check(written, work, svc.schema, anchors)
    if found is None or problems:
        raise Rejected(problems)
    out = await land_debrief(svc, work, found, 0)
    await finish_graded(svc, doc, t, job_attempts(svc.con, doc.id, "graded")[1])
    log.info("graded_written", doc=doc.id, title=doc.title, misses=len(found.misses))
    return out


async def graded_work(svc: Services, doc: Doc, t: Tags) -> graded.Work:
    """The returned work as the debrief sees it: its course, the exam it was, and the cards he holds for it."""
    course = course_for(doc, t.courses, None)
    if course is None:
        raise PipelineError(f"can't tell the course of {doc.title!r}")
    ref = graded.work_ref(doc.title)
    kind, number = ref if ref else ("homework" if doc.document_type == t.assignment_type else "exam", None)
    exam = graded.match_exam(svc.con, course, number, doc.added) if kind == "exam" else None
    lectures: list[int] = sorted(json.loads(exam["lectures"])) if exam and exam.get("lectures") else []
    existing: dict[str, Any] = {"items": []}
    if lectures:
        existing = await svc.core.call("anki_notes_search", {
            "query": " OR ".join(f"tag:course::{course.lower()}::l{n:02d}*" for n in lectures), "limit": 100})
    return graded.Work(doc.id, course, kind, number, doc.title, svc.paperless.link(doc.id), exam,
                       tuple(graded.lecture_tags(svc.con, course)),
                       tuple(dedupe.normalize(str(i["first_field"])) for i in existing.get("items", [])),
                       tuple(await svc.paperless.notes(doc.id)))


async def land_debrief(svc: Services, work: graded.Work, found: graded.Debrief, invalid: int) -> Outcome:
    """Propose the misses' cards (held for review), keep the mistakes in the Ledger, and write the stub's debrief."""
    by_question = {m.question: graded.cards_of(m, work.link) for m in found.misses}
    cards = [c for cs in by_question.values() for c in cs]
    course, doc_id = work.course, work.doc_id
    out = Outcome(doc_id)
    carded: dict[str, list[str]] = {}
    if cards:
        source = Source(kind="homework" if work.kind == "homework" else "exam", course=course, ref=work.title,
                        link=work.link)
        out = await check_and_propose(svc, Payload(source=source, cards=cards), "paperless", work.card_kind, False,
                                      doc_id, hold=True)
        drafts = {next(iter(d.fields.values()), ""): d.mnemosyne_id
                  for d in store.batch_drafts(svc.con, out.batch_id or "")}
        carded = {q: [mid for c in cs if (mid := drafts.get(next(iter(c.fields.values()), "")))]
                  for q, cs in by_question.items()}
    out.invalid = invalid
    graded.save(svc.con, work, found, carded)
    if svc.notes_dir is not None:
        try:
            obsidian.sync_doc(svc.con, svc.notes_dir, doc_id)
        except OSError as e:
            log.warning("debrief_note_failed", doc=doc_id, error=str(e)[:200])
    log.info("graded_processed", doc=doc_id, title=work.title, earned=found.earned, possible=found.possible,
             misses=len(found.misses), kinds=[m.kind for m in found.misses], cards=len(cards),
             unconfirmed=sum(not m.confirmed for m in found.misses))
    return out


async def finish_graded(svc: Services, doc: Doc, t: Tags, attempts: int) -> None:
    record_job(svc.con, doc.id, "done", attempts + 1, kind="graded")
    await svc.paperless.add_tag(doc.id, t.done)
    await svc.paperless.remove_tag(doc.id, t.error)      # an earlier failed try may have tagged it


async def run_graded(svc: Services, doc: Doc, t: Tags, again: bool = False) -> Outcome | None:
    """One graded document with job bookkeeping (ingest_job kind 'graded'): tagged done, or error after two tries."""
    status, attempts = job_attempts(svc.con, doc.id, "graded")
    if status == "done" and not again:
        return None
    record_job(svc.con, doc.id, "running", attempts + 1, kind="graded")
    try:
        out = await debrief(svc, doc, t)
    except Exception as e:  # noqa: BLE001 -- recorded on the job; after two tries the document is tagged
        tries = attempts + charged(e)
        record_job(svc.con, doc.id, "error", tries, str(e)[:2000], kind="graded")
        log.warning("graded_failed", doc=doc.id, title=doc.title, attempt=tries, error=str(e)[:300])
        if charged(e) and tries >= MAX_ATTEMPTS:
            await svc.paperless.add_tag(doc.id, t.error)
        return None
    await finish_graded(svc, doc, t, attempts)
    return out


# ---------- research papers (papers.py) ----------

def is_paper(doc: Doc, t: Tags) -> bool:
    """A Paper in Paperless that Aman tagged `mnemosyne`: not every paper he saves is one he wants to remember."""
    return (t.paper_type is not None and doc.document_type == t.paper_type
            and t.opt_in is not None and t.opt_in in doc.tags)


PAPER_ABOUT = """Source: a research paper Aman is reading, filed as {title!r}. Follow the canon's "Research papers" section.
{course}
{tag} stands in for the usual src:: tag. Source field: the paper's first author, "et al." when there are more, year,
and figure, table, or section ("Smith et al. 2025, Fig. 3").
Page images show Aman's handwritten annotations; they are the strongest priority signals (canon 16).
Also return a top-level "prerequisites" list, as that section describes."""


async def card_paper(svc: Services, doc: Doc, t: Tags) -> Outcome:
    """A paper, carded like a reading and always held. A copy filed again under the same title (re-annotated) is
    carded only where it changed, like a lecture deck sent again."""
    paper_slug = papers.slug_for(doc.title, doc.added)
    tag, key = papers.tag_for(paper_slug), f"paper:{paper_slug}"
    course = course_for(doc, t.courses, None)
    limits = svc.yaml("llm.yaml").get("limits", {})
    pdf = await svc.paperless.download(doc.id)
    marks = fingerprint(pdf)
    fresh = unseen(marks, decks.seen(svc.con, key, {m.key for m in marks if m.key}, doc.id))
    if not fresh:
        decks.save(svc.con, doc.id, key, marks)
        log.info("paper_unchanged", doc=doc.id, title=doc.title)
        return Outcome(doc.id, unchanged=True)
    partial = len(fresh) < len(marks)
    page_list = pages(pdf, int(limits.get("max_pages", 60)), int(limits.get("image_dpi", 110)),
                      only=set(fresh) if partial else None)
    where = (f"Aman assigned it in {course}: cards go to deck Now and also carry course::{course.lower()}." if course
             else "It's self-study: cards go to deck Core, with no course:: tag.")
    existing = await svc.core.call("anki_notes_search", {"query": f"tag:{tag}", "limit": 100})
    lec = Lecture(course=course or "", lecture=0, title=doc.title, kind="paper", ref=doc.title,
                  link=svc.paperless.link(doc.id), emphasis=await svc.paperless.notes(doc.id),
                  existing=[dedupe.normalize(str(i["first_field"])) for i in existing.get("items", [])],
                  pages=page_list, partial=partial, required_tag=tag,
                  about=PAPER_ABOUT.format(title=doc.title, course=where, tag=tag))
    skill_md = (svc.skill_dir / "SKILL.md").read_text(encoding="utf-8")
    gen = await generate(svc.llm, "lecture", skill_md, "", lec, svc.schema,
                         Context.load(svc.config, svc.repo / "data" / "anchors.yaml"))
    if gen.payload is None:
        raise PipelineError(f"no valid cards: {gen.problems[:3]}")
    for card in gen.payload.cards:
        card.fields["SourceLink"] = lec.link or ""
    out = await check_and_propose(svc, Payload(source=gen.payload.source, cards=gen.payload.cards), "paperless",
                                  "paper", False, doc.id, hold=True)
    out.invalid, out.pages = len(gen.problems), len(fresh)
    store.save_skipped(svc.con, out.batch_id, gen.skipped)
    names = {b.key: b.name for b in books.load(svc.config / "books.yaml").values()}
    found = await papers.check(svc.core, svc.books_index, names, papers.clean(gen.prerequisites))
    papers.save(svc.con, doc.id, paper_slug, doc.title, papers.year_of(doc.title, doc.added), course, found)
    out.questions = await take_questions(svc, doc, course or "", 0, gen.questions)
    decks.save(svc.con, doc.id, key, marks)
    if svc.notes_dir is not None:
        try:
            obsidian.sync_doc(svc.con, svc.notes_dir, doc.id)
        except OSError as e:
            log.warning("paper_note_failed", doc=doc.id, error=str(e)[:200])
    log.info("paper_processed", doc=doc.id, title=doc.title, slug=paper_slug, prerequisites=len(found),
             missing=sum(1 for p in found if not p.get("cards")),
             **{k: v for k, v in vars(out).items() if k not in ("doc_id", "dropped", "adopted")})
    return out


async def run_paper(svc: Services, doc: Doc, t: Tags, again: bool = False) -> Outcome | None:
    """One paper with job bookkeeping (ingest_job kind 'paper'): tagged done, or error after two tries."""
    status, attempts = job_attempts(svc.con, doc.id, "paper")
    if status == "done" and not again:
        return None
    record_job(svc.con, doc.id, "running", attempts + 1, kind="paper")
    try:
        out = await card_paper(svc, doc, t)
    except Exception as e:  # noqa: BLE001 -- recorded on the job; after two tries the document is tagged
        tries = attempts + charged(e)
        record_job(svc.con, doc.id, "error", tries, str(e)[:2000], kind="paper")
        log.warning("paper_failed", doc=doc.id, title=doc.title, attempt=tries, error=str(e)[:300])
        if charged(e) and tries >= MAX_ATTEMPTS:
            await svc.paperless.add_tag(doc.id, t.error)
        return None
    record_job(svc.con, doc.id, "done", attempts + 1, kind="paper")
    await svc.paperless.add_tag(doc.id, t.done)
    await svc.paperless.remove_tag(doc.id, t.error)
    return out


# ---------- readings: textbook sections (config/books.yaml) ----------

def ebook(svc: Services, book: Book, where: str) -> tuple[Where, Path] | None:
    """Where a reading is in the book's PDF in Calibre, or None when the book isn't there. Raises ValueError when the
    book is there but `where` isn't in it."""
    if svc.calibre_dir is None or book.calibre is None:
        return None
    pdf = books.pdf_path(svc.calibre_dir, book.calibre)
    if pdf is None:
        return None
    toc, count = books.book_outline(pdf, book)
    return books.locate(where, toc, count, book.pdf_page(pdf)), pdf


def book_link(svc: Services, book: Book) -> str | None:
    return f"{svc.calibre_url.rstrip('/')}/book/{book.calibre}" if svc.calibre_url and book.calibre is not None else None


async def card_reading(svc: Services, book: Book, where: str, doc: Doc | None = None) -> Outcome:
    """A textbook section, carded like a lecture and always held for Aman's review. The pages come from the ebook in
    Calibre, or from a scan of his own copy filed in Paperless (`doc`): there his marks lead, and the ebook's text,
    when the book is in Calibre, rides along as a clean reference."""
    tag_where = books.tag_for(where)            # ValueError on something that isn't a section, chapter, or pages
    try:
        found = ebook(svc, book, where)
    except ValueError:
        if doc is None:
            raise
        found = None                            # a scan still works without the ebook's outline
    if doc is None and found is None:
        raise PipelineError(f"{book.name} isn't in Calibre (id {book.calibre}); scan the pages into Paperless instead")
    limits = svc.yaml("llm.yaml").get("limits", {})
    max_pages, dpi = int(limits.get("max_pages", 60)), int(limits.get("image_dpi", 110))
    label = found[0].label if found else where
    code = book.course_at(found[0].chapter if found else books.chapter_in(where))
    if doc is not None:
        page_list = pages(await svc.paperless.download(doc.id), max_pages, dpi)
        reference = books.text_of(found[1], found[0]) if found else None
        link, emphasis = svc.paperless.link(doc.id), await svc.paperless.notes(doc.id)
        what = (f"a scan of Aman's own copy of {book.name}, {book.title}: {label}. His handwriting, underlining, and "
                "marks on the page images are the strongest priority signals (canon 16): card what he marked first.")
    else:
        assert found is not None
        span, pdf = found
        page_list = pages(pdf, max_pages, dpi, only=set(range(span.first, span.last + 1)))
        reference, link, emphasis = None, book_link(svc, book), []
        what = f"{book.name}, {book.title}: {label} (the ebook's PDF pages {span.first}-{span.last}; no handwriting)."
    tag = f"src::book::{book.slug}::{tag_where}"
    course = (f"It's the textbook for {code}: cards go to deck Now and also carry course::{code.lower()}"
              " (no lecture part)." if code else
              "It's self-study, not a course: cards go to deck Core, with no course:: tag.")
    about = "\n".join([f"Source: {what}", f"Card {label} only; pages at the edges may run into the sections around it.",
                       course, f"{tag} stands in for the usual src:: tag. Source field: \"{book.name} {label}\"."])
    existing = await svc.core.call("anki_notes_search", {"query": f"tag:{tag}*", "limit": 100})
    lec = Lecture(course=code or "", lecture=0, title=label, kind="reading", ref=f"{book.name} {label}",
                  link=link, emphasis=emphasis,
                  existing=[dedupe.normalize(str(i["first_field"])) for i in existing.get("items", [])],
                  pages=page_list, required_tag=tag, about=about, reference=reference)
    skill_md = (svc.skill_dir / "SKILL.md").read_text(encoding="utf-8")
    golden = golden_reference(svc.skill_dir / "golden", code) if code else ""
    gen = await generate(svc.llm, "lecture", skill_md, golden, lec, svc.schema,
                         Context.load(svc.config, svc.repo / "data" / "anchors.yaml"))
    if gen.payload is None:
        raise PipelineError(f"no valid cards: {gen.problems[:3]}")
    cards = gen.payload.cards
    for card in cards:
        card.fields["SourceLink"] = link or ""
    out = await check_and_propose(svc, Payload(source=gen.payload.source, cards=cards),
                                  "paperless" if doc else "calibre", "reading", False, doc.id if doc else 0)
    out.invalid, out.pages = len(gen.problems), len(page_list)
    store.save_skipped(svc.con, out.batch_id, gen.skipped)
    if doc is not None:
        out.questions = await take_questions(svc, doc, code or "", 0, gen.questions)
    log.info("reading_processed", book=book.key, where=where, doc=doc.id if doc else None,
             **{k: v for k, v in vars(out).items() if k not in ("doc_id", "dropped", "adopted")})
    return out


def reading_ref(book: Book, where: str, doc: Doc | None) -> str:
    return f"paperless:{doc.id}" if doc is not None else f"{book.key}:{books.tag_for(where)}"


async def run_reading(svc: Services, book: Book, where: str, doc: Doc | None = None,
                      again: bool = False) -> Outcome | None:
    """One reading with job bookkeeping (ingest_job kind 'reading'). A section already carded is skipped unless
    `again`; a scan is tagged done in Paperless, or error after two failed tries."""
    ref = reading_ref(book, where, doc)
    status, attempts = job_attempts(svc.con, ref, "reading")
    if status == "done" and not again:
        return None
    record_job(svc.con, ref, "running", attempts + 1, kind="reading")
    t = await tags(svc) if doc is not None else None
    try:
        out = await card_reading(svc, book, where, doc)
    except Exception as e:  # noqa: BLE001 -- recorded on the job; the review page shows it
        tries = attempts + charged(e)
        record_job(svc.con, ref, "error", tries, str(e)[:2000], kind="reading")
        log.warning("reading_failed", ref=ref, attempt=tries, error=str(e)[:300])
        if doc is not None and t is not None and charged(e) and tries >= MAX_ATTEMPTS:
            await svc.paperless.add_tag(doc.id, t.error)
        return None
    record_job(svc.con, ref, "done", attempts + 1, kind="reading")
    if doc is not None and t is not None:
        await svc.paperless.add_tag(doc.id, t.done)
    return out


def recent_readings(con: sqlite3.Connection, limit: int = 12) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(
        "SELECT ref, status, attempts, error, updated_at FROM ingest_job WHERE kind = 'reading'"
        " ORDER BY updated_at DESC LIMIT ?", (limit,))]


async def waiting_scans(svc: Services) -> list[dict[str, Any]]:
    """Documents tagged `mnemosyne` that no pipeline takes on its own: scans whose title doesn't name a book and
    section yet. The review page asks which book and section they are."""
    t = await tags(svc)
    if t.opt_in is None:
        return []
    other = {await svc.paperless.document_type_id(n) for n in ("Syllabus", "Assignment", "Exam", "Paper")} | {t.lecture_type}
    other.discard(None)
    catalog = books.load(svc.config / "books.yaml")
    out: list[dict[str, Any]] = []
    for d in await svc.paperless.documents(tags__id__all=[t.opt_in], tags__id__none=t.excluded):
        if d.document_type in other or job_attempts(svc.con, f"paperless:{d.id}", "reading")[0] == "running":
            continue
        guess = books.parse_title(d.title, catalog)
        out.append({"id": d.id, "title": d.title, "added": d.added, "link": svc.paperless.link(d.id),
                    "book": guess[0].key if guess else None, "where": guess[1] if guess else None})
    return out


# ---------- exams (PLAN §4.5) ----------

def sync_exams(svc: Services) -> dict[str, int]:
    """Re-read the calendar into `exam` rows (daily at 04:15, and on demand)."""
    codes = {str(c["code"]) for c in svc.yaml("courses.yaml")["courses"]}
    counts = exams.upsert(svc.con, exams.from_calendar(svc.con, codes, "2026-08-01"))
    log.info("exams_synced", **counts)
    return counts


async def build_map(svc: Services, course: str, doc: Doc | None = None) -> dict[str, Any]:
    """Map each calendar exam to its lectures from the course syllabus; Aman confirms it in Telegram."""
    sync_exams(svc)
    rows = exams.course_exams(svc.con, course)
    if not rows:
        raise PipelineError(f"no {course} exams on the calendar")
    if doc is None:
        t = await tags(svc)
        syllabus_type = await svc.paperless.document_type_id("Syllabus")
        course_tag = next((tid for tid, code in t.courses.items() if code == course), None)
        found = await svc.paperless.documents(tags__id__all=[course_tag, t.semester], document_type__id=syllabus_type)
        if not found:
            raise PipelineError(f"no Fall syllabus for {course} in Paperless")
        doc = found[-1]
    limits = svc.yaml("llm.yaml").get("limits", {})
    page_list = pages(await svc.paperless.download(doc.id), int(limits.get("max_pages", 60)),
                      int(limits.get("image_dpi", 110)))
    proposal = await exams.propose_map(svc.llm, course, rows, page_list)
    exams.save_map(svc.con, course, doc.id, proposal)
    log.info("exam_map_proposed", course=course, doc=doc.id, exams=len(proposal["exams"]))
    return proposal


# The Shortcut's kinds → Paperless document type, and whether the document needs the opt-in tag
KINDS: dict[str, tuple[str, bool]] = {
    "slides": ("Lecture", False), "notes": ("Lecture", False), "review-sheet": ("Lecture", True),
    "syllabus": ("Syllabus", True), "graded-homework": ("Assignment", True), "graded-exam": ("Exam", True),
    "paper": ("Paper", True),
}
NO_COURSE = {"", "NONE", "-", "—"}       # a paper read for himself: the Shortcut's "none"


PREFIX = re.compile(r"^\s*(?P<dept>[A-Z]{3,5})[\s-]?(?P<code>\d{3})\s*(?:L|HW|Lecture|Homework|Exam)?\s*(?P<num>\d{1,2})?\b\s*[-–:|]?\s*",
                    re.IGNORECASE)


def paperless_title(course: str, kind: str, number: int | None, title: str) -> str:
    """'NUEN 201 L07 - Quantum Theory of Light'. A title that already starts with the course (say, the file name
    'NUEN 201 L07 - Quantum…') is stripped first, and its lecture number used if none was given."""
    if (m := PREFIX.match(title)) and (m["dept"] + m["code"]).upper() == course:
        if number is None and m["num"]:
            number = int(m["num"])
        title = title[m.end():]
    title = title.strip(" -–:|") or "untitled"
    code = f"{course[:-3]} {course[-3:]}"
    label = {"syllabus": "Syllabus", "graded-homework": "HW", "graded-exam": "Exam"}.get(kind, "L")
    if number is None:
        return f"{code} {label} - {title}" if label != "L" else f"{code} - {title}"
    if label in ("L", "HW"):
        return f"{code} {label}{number:02d} - {title}"
    return f"{code} {label} {number} - {title}"


async def intake(svc: Services, pdf: bytes, filename: str, course: str, number: int | None, title: str,
                 kind: str) -> dict[str, Any]:
    """File an upload from the iPad Shortcut into Paperless with the metadata the poller reads."""
    if kind not in KINDS:
        raise PipelineError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    if kind == "paper":
        return await intake_paper(svc, pdf, filename, course, title)
    courses = svc.yaml("courses.yaml")
    entry = next((c for c in courses["courses"] if c["code"] == course), None)
    if entry is None:
        raise PipelineError(f"unknown course {course!r}")
    t = await tags(svc)
    course_tag = next(tid for tid, code in t.courses.items() if code == course)
    doc_type_name, opt_in = KINDS[kind]
    doc_type = await svc.paperless.document_type_id(doc_type_name)
    tag_list = [course_tag, t.semester] + ([t.opt_in] if opt_in and t.opt_in else [])
    if kind.startswith("graded") and (graded := await svc.paperless.tag_id("graded", create=True)):
        tag_list.append(graded)
    full = paperless_title(course, kind, number, title)
    older = await earlier_versions(svc, course_tag, doc_type, full)
    task = await svc.paperless.upload(pdf, filename, full, tag_list, doc_type)
    log.info("intake", course=course, kind=kind, title=full, task=task, replaces=[d.id for d in older])
    return {"task": task, "title": full, "document_type": doc_type_name, "replaces": [d.id for d in older]}


async def intake_paper(svc: Services, pdf: bytes, filename: str, course: str, title: str) -> dict[str, Any]:
    """A research paper from the Shortcut: type Paper, tagged `mnemosyne` (so it's carded), plus the course tag
    when a course assigned it. Its title is kept as given: "Smith 2025 - Bohm diffusion" makes the best tag."""
    t = await tags(svc)
    course_tag = None if course.upper() in NO_COURSE else next(
        (tid for tid, code in t.courses.items() if code == course), None)
    if course.upper() not in NO_COURSE and course_tag is None:
        raise PipelineError(f"unknown course {course!r}")
    doc_type = await svc.paperless.document_type_id("Paper")
    tag_list = [x for x in (t.opt_in, course_tag) if x is not None]
    full = title.strip().removesuffix(".pdf") or filename.removesuffix(".pdf")
    task = await svc.paperless.upload(pdf, filename, full, tag_list, doc_type)
    log.info("intake", course=course_tag and course, kind="paper", title=full, task=task)
    return {"task": task, "title": full, "document_type": "Paper", "replaces": []}


def same_document(a: str, b: str) -> bool:
    """Same course and an overlapping lecture number (or span), or the same title for documents without one."""
    ra, rb = parse_title(a), parse_title(b)
    if ra and rb:
        return ra.course == rb.course and bool(set(ra.span) & set(rb.span))
    return a.strip().lower() == b.strip().lower()


def same_copy(title: str, marks: list[PageMark], other_title: str, other: list[PageMark]) -> bool:
    """Whether another lecture document is a copy of the same deck. Slides match by their printed text, so a deck
    reused for the next lecture is found whatever number it was sent under. Handwritten notes, which have no text
    layer, match by lecture number. Slides never match notes."""
    keyed, other_keyed = sum(1 for m in marks if m.key) >= 3, sum(1 for m in other if m.key) >= 3
    if keyed and other_keyed:
        return same_deck(marks, other)
    return not keyed and not other_keyed and same_document(title, other_title)


def span_title(titles: list[str]) -> str | None:
    """One title for every lecture a deck's copies were sent under: 'PHYS 207 L07-L08 - Capacitors'. The newest
    title (first) gives the name."""
    refs = [r for t in titles if (r := parse_title(t))]
    if not refs or len({r.course for r in refs}) > 1:
        return None
    span = range(min(r.span[0] for r in refs), max(r.lecture for r in refs) + 1)
    course = refs[0].course
    return f"{course[:-3]} {course[-3:]} {lecture_label(span)} - {refs[0].title}"


async def earlier_versions(svc: Services, course_tag: int, doc_type: int | None, title: str) -> list[Doc]:
    docs = await svc.paperless.documents(tags__id__all=[course_tag], document_type__id=doc_type)
    return [d for d in docs if same_document(d.title, title)]


async def marks_of(svc: Services, doc: Doc, course: str) -> list[PageMark]:
    """A document's page fingerprints, taken now if it was filed before fingerprints were kept."""
    if found := decks.marks(svc.con, doc.id):
        return found
    got = fingerprint(await svc.paperless.download(doc.id))
    decks.save(svc.con, doc.id, course, got)
    return got


async def copies_of(svc: Services, course: str, course_tag: int, doc_type: int | None, title: str,
                    marks: list[PageMark], exclude: int) -> list[Doc]:
    out: list[Doc] = []
    for d in await svc.paperless.documents(tags__id__all=[course_tag], document_type__id=doc_type):
        if d.id != exclude and same_copy(title, marks, d.title, await marks_of(svc, d, course)):
            out.append(d)
    return out


async def follow(svc: Services, old: int, new: int, title: str) -> None:
    """What pointed at a lecture's old copy now points at the new one: its cards' SourceLink (in the Ledger, and the
    one field in Anki for cards already written), and its questions, with their note in the vault."""
    link = svc.paperless.link(new)
    for nid in store.relink(svc.con, svc.paperless.link(old), link):
        try:
            await svc.core.call("anki_notes_update_fields", {"note_id": nid, "fields": {"SourceLink": link},
                                                             "idempotency_key": f"mn-relink-{nid}-{new}"})
        except Exception as e:  # noqa: BLE001 -- a stale link is harmless; the rest of the move still happens
            log.warning("relink_failed", note=nid, error=str(e)[:200])
    if svc.notes_dir is not None:
        try:
            obsidian.move_doc(svc.con, svc.notes_dir, old, new, title)
        except OSError as e:
            log.warning("lecture_note_move_failed", old=old, new=new, error=str(e)[:200])
    qstore.move_doc(svc.con, old, new, title)   # a no-op when the note move already did it


async def settle_upload(svc: Services, task_id: str, older: list[int], pdf: bytes | None = None,
                        course: str | None = None, title: str = "", wait_s: float = 5,
                        tries: int = 60) -> dict[str, Any]:
    """After an upload, once Paperless has consumed it, the new copy replaces older copies of the same document.
    For lectures that's any copy of the same deck, matched page by page, so a deck reused for the next lecture is
    found whatever number it was sent under; for anything else, the same title. Notes and tags carry over, the old
    copy's cards and questions follow, and the old copy goes to Paperless's trash. The title gains every lecture the
    copies were sent under ('L07-L08').

    A file Paperless refuses as identical to one it has adds nothing, unless that document never finished carding:
    then it runs again."""
    status, new_id, error = "pending", None, ""
    for _ in range(tries):
        status, new_id, error = await svc.paperless.task(task_id)
        if status in ("success", "failure"):
            break
        await asyncio.sleep(wait_s)
    t = await tags(svc)
    if status == "failure" and "duplicate" in error.lower():
        m = re.search(r"#(\d+)", error)
        keep = int(m[1]) if m else (older[-1] if older else None)
        if keep is None:
            log.warning("upload_duplicate_unknown", task=task_id, error=error[:300])
            return {"status": status}
        if job_attempts(svc.con, keep)[0] == "done":
            kept = await svc.paperless.document(keep)
            if title and (wider := span_title([kept.title, title])) and wider != kept.title:
                await svc.paperless.set_title(keep, wider)   # the same deck, sent again for a later lecture
            log.info("upload_unchanged", task=task_id, document=keep)
            return {"duplicate_of": keep, "unchanged": True}
        svc.con.execute("DELETE FROM ingest_job WHERE kind = 'paperless' AND ref = ?", (str(keep),))
        for tag in (t.done, t.error):
            await svc.paperless.remove_tag(keep, tag)
        log.info("upload_duplicate", task=task_id, rerun=keep)
        return {"duplicate_of": keep, "outcome": await process_document(svc, keep)}
    if status != "success" or new_id is None:
        log.warning("upload_not_consumed", task=task_id, status=status, error=error[:300])
        return {"status": status}
    new = await svc.paperless.document(new_id)
    replace = [o for o in older if o != new_id]
    course_tag = next((tid for tid, code in t.courses.items() if code == course), None)
    if pdf is not None and course is not None and course_tag is not None and new.document_type == t.lecture_type:
        found = await copies_of(svc, course, course_tag, new.document_type, new.title, fingerprint(pdf), new_id)
        replace = [d.id for d in found]
    if not replace:
        return {"document": new_id, "replaced": []}
    name = await replace_copies(svc, new, [await svc.paperless.document(o) for o in replace], {t.done, t.error})
    return {"document": new_id, "replaced": replace, "title": name}


async def replace_copies(svc: Services, new: Doc, olds: list[Doc], pipeline_tags: set[int]) -> str:
    """The new copy takes the old copies' place: notes and tags carry over, their cards and questions follow, and
    the old copies go to Paperless's trash. Returns the title, which gains every lecture they were sent under."""
    name = span_title([new.title, *(d.title for d in olds)]) or new.title
    if name != new.title:
        await svc.paperless.set_title(new.id, name)
    for old in olds:
        for note in await svc.paperless.notes(old.id):
            await svc.paperless.add_note(new.id, note)
        for tag in old.tags - pipeline_tags:
            await svc.paperless.add_tag(new.id, tag)
        decks.replaced(svc.con, new.id, old.id)
        await follow(svc, old.id, new.id, name)
        await svc.paperless.trash(old.id)
        log.info("upload_replaced", old=old.id, new=new.id, title=name)
    return name


def lecture_tag(course: str, lecture: int, title: str) -> str:
    return f"course::{course.lower()}::l{lecture:02d}-{slug(title)}"


async def refile(svc: Services, doc_id: int, course: str, number: int | None = None) -> dict[str, Any]:
    """A lecture filed under the wrong course (the Shortcut's course picked wrong) moves to the right one, as if it
    had been sent there: its title and course tag in Paperless; its cards' lecture tag and Source, in the Ledger and in
    Anki; its questions and page fingerprints. A copy of the same deck already filed in that course is then replaced
    by this one, like any re-share. Running it again changes nothing."""
    doc, t = await svc.paperless.document(doc_id), await tags(svc)
    ref = parse_title(doc.title)
    old = course_for(doc, t.courses, ref.course if ref else None)
    if doc.document_type != t.lecture_type or ref is None or old is None:
        raise PipelineError(f"only a filed lecture can be refiled; {doc.title!r} isn't one")
    course_tag = next((tid for tid, code in t.courses.items() if code == course), None)
    if course_tag is None:
        raise PipelineError(f"unknown course {course!r}")
    # The right number is in what's left of the title ("MATH 309 L07 - …"), or given; the wrong course's isn't it
    title = paperless_title(course, "slides", ref.lecture if number is None and old == course else number, ref.title)
    new = parse_title(title)
    if new is None:
        raise PipelineError(f"no lecture number for {ref.title!r}: give one")
    old_tag, new_tag = lecture_tag(old, ref.lecture, ref.title), lecture_tag(course, new.lecture, new.title)
    old_src, new_src = f"{old} L{ref.lecture:02d}", f"{course} L{new.lecture:02d}"
    written = store.refile(svc.con, svc.paperless.link(doc_id), course, new.lecture, title, (old_tag, new_tag),
                           (old_src, new_src))
    for nid, source in written:
        await svc.core.call("anki_notes_update_fields", {"note_id": nid, "fields": {"Source": source},
                                                         "idempotency_key": f"mn-refile-src-{nid}-{new_src}"})
    retagged: list[int] = []
    if old_tag != new_tag:
        # Every note with the old tag: this lecture's, and earlier cards the dedupe adopted for it
        found = await svc.core.call("anki_notes_search", {"query": f"tag:{old_tag}", "limit": 100})
        for nid in (int(i["id"]) for i in found.get("items", [])):
            await svc.core.call("anki_notes_add_tags", {"note_ids": [nid], "tags": [new_tag],
                                                        "idempotency_key": f"mn-refile-tag-{nid}-{new_tag}"})
            await svc.core.call("anki_notes_remove_tags", {"note_ids": [nid], "tags": [old_tag],
                                                           "idempotency_key": f"mn-refile-untag-{nid}-{old_tag}"})
            retagged.append(nid)
    moved = qstore.refile_doc(svc.con, doc_id, course, new.lecture, title)
    decks.set_course(svc.con, doc_id, course)
    # Paperless last: its title is what tells a rerun the wrong course, should anything above fail
    if title != doc.title:
        await svc.paperless.set_title(doc_id, title)
    for tid in doc.tags & set(t.courses) - {course_tag}:
        await svc.paperless.remove_tag(doc_id, tid)
    if course_tag not in doc.tags:
        await svc.paperless.add_tag(doc_id, course_tag)
    log.info("refiled", doc=doc_id, title=title, was=doc.title, retagged=len(retagged), questions=moved)

    marks = await marks_of(svc, doc, course)
    olds = await copies_of(svc, course, course_tag, doc.document_type, title, marks, doc_id)
    refiled = await svc.paperless.document(doc_id)
    name = await replace_copies(svc, refiled, olds, {t.done, t.error}) if olds else title
    if svc.notes_dir is not None:
        try:
            obsidian.sync_doc(svc.con, svc.notes_dir, doc_id)
        except OSError as e:
            log.warning("lecture_note_failed", doc=doc_id, error=str(e)[:200])
    return {"document": doc_id, "was": doc.title, "title": name, "anki_sources": [n for n, _ in written],
            "anki_retagged": retagged, "questions": moved, "replaced": [d.id for d in olds]}


async def process_document(svc: Services, doc_id: int, job: str | None = None) -> Outcome | None:
    """Run one document now, whatever its date or tags (still skipped if its job already finished). An exam or an
    assignment asked for by id is debriefed as graded work, and a review sheet filed as a lecture is prep, as the
    poller would. `job` (a failed job retried from the review page) says which job runs again."""
    doc, t = await svc.paperless.document(doc_id), await tags(svc)
    if job == "prep" or (job is None and doc.document_type == t.lecture_type and is_prep(doc, t)):
        return await run_prep(svc, doc, t)
    if job == "graded" or (job is None and doc.document_type is not None
                           and doc.document_type in (t.exam_type, t.assignment_type)):
        return await run_graded(svc, doc, t)
    if job == "paper" or (job is None and doc.document_type is not None and doc.document_type == t.paper_type):
        return await run_paper(svc, doc, t)
    return await run_one(svc, doc, t)

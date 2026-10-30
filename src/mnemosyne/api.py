"""HTTP API. Reached only over the tailnet and the Docker network; nothing here is public.

Two bearer tokens (invariant 7): the propose token can only add drafts; the approve token can also
approve, reject, and drive Telegram. Cloud-facing MCP tools get the propose token only.
"""

import asyncio
import hmac
import json
import sqlite3
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import structlog
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # pyright: ignore[reportMissingTypeStubs]
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from mnemosyne import (
    audio,
    clips,
    graded,
    learner,
    library,
    merge,
    neighbors,
    papers,
    prep,
    reviews,
    telegram,
    videos,
    weekly,
)
from mnemosyne import questions as qstore
from mnemosyne.anki.core_client import AnkiCoreError, Core, CoreClient
from mnemosyne.cards import TAG, Payload, Schema
from mnemosyne.config import Settings, read_secret
from mnemosyne.extract import parse_title
from mnemosyne.generate.llm import Router
from mnemosyne.inbox import PREAPPROVED_ORIGINS, default_decide, propose, unchecked_because
from mnemosyne.ingest import books
from mnemosyne.ingest.paperless import Doc, Paperless
from mnemosyne.ledger import db, store
from mnemosyne.pipeline import (
    PipelineError,
    Rejected,
    Services,
    ask_hyperion,
    build_map,
    check_and_propose,
    debrief_written,
    ebook,
    failure_key,
    failure_text,
    failures_due,
    intake,
    job_attempts,
    mark_notice,
    nudge_due,
    nudge_key,
    nudge_text,
    poll,
    present,
    process_document,
    propose_checked,
    questions_only,
    reading_ref,
    recent_readings,
    refile,
    run_graded,
    run_paper,
    run_prep,
    run_reading,
    settle,
    settle_upload,
    stuck,
    sync_exams,
    tags,
    waiting_scans,
    write_question_card,
)
from mnemosyne.project import metrics, obsidian, report
from mnemosyne.project.hyperion import HyperionClient
from mnemosyne.schedule import exam_decks, exams, governor, leech
from mnemosyne.verify.dedupe import OllamaEmbedder
from mnemosyne.web import review
from mnemosyne.write import write_approved

log = structlog.get_logger()
PAGE_HTML = Path(review.__file__).with_name(review.PAGE)
# How long a chat or Telegram proposal waits for the lecture poller before it is kept unchecked (the MCP tool's
# timeout is 60 s).
BUSY_WAIT = 40.0
WATCH_HTML = Path(review.__file__).with_name("watch.html")
Level = Literal["propose", "approve"]


class VideoRequest(BaseModel):
    video: str   # a YouTube link or id


class VideoStudy(BaseModel):
    video: str        # a YouTube link or id
    start: str        # mm:ss
    end: str          # mm:ss, at most 120 s after start
    fps: float = 6.0  # frames a second kept (1-12)
    look: str = ""    # a stretch ("1:25-1:30": its filmstrips) or times ("1:27.5,1:41": full frames)


class ClipSubmission(BaseModel):
    code: str                  # the scene's Python (ManimCE, optionally on mn_style)
    title: str
    courses: list[str] = []
    topics: list[str] = []
    watch_for: str = ""
    replace: str = ""          # a slug from an earlier submission, to fix what its stills showed


class ClipKeep(BaseModel):
    days: int | None = None    # None: the default (config/clips.yaml)
    forever: bool = False


class Proposed(BaseModel):
    payload: Payload
    origin: str = "inbox"
    checks: bool = False   # run the verify gate, dedupe, and approval policy like a processed lecture


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    by: str = "api"


class Sent(BaseModel):
    batch_id: str
    message_ref: str


class Callback(BaseModel):
    data: str
    by: str = "telegram"


class TagAdd(BaseModel):
    add: list[str]
    note: str
    by: str = "claude"


class Score(BaseModel):
    earned: float
    possible: float


class MistakeDecision(BaseModel):
    action: Literal["confirm", "wrong", "fixed", "reopen"]
    note: str = ""                                   # with "wrong": what actually went wrong, in his words


class ReadingIn(BaseModel):
    book: str                                        # a key or alias from config/books.yaml
    where: str                                       # "2.3", "ch 2", or "p 39-47"
    doc_id: int | None = None                        # a scan of Aman's copy in Paperless, instead of the ebook
    again: bool = False                              # card a section that was carded before


class RefileIn(BaseModel):
    course: str                                      # the course it belongs to, e.g. MATH309
    number: int | None = None                        # its lecture number, when the title doesn't carry it


class LeechDecision(BaseModel):
    verdict: Literal["apply", "keep", "dismiss"]
    note: str = ""
    fields: dict[str, str] | None = None             # Aman's edit of a rewrite
    cards: list[dict[str, Any]] | None = None        # Aman's edit of replacement or added cards
    by: str = "review-page"


class State:
    def __init__(self, settings: Settings, con: sqlite3.Connection, schema: Schema, core: Core | None) -> None:
        self.settings = settings
        self.con = con
        self.schema = schema
        self.core = core
        self.tokens: dict[Level, str | None] = {"propose": read_secret(settings.propose_token_file),
                                                 "approve": read_secret(settings.approve_token_file)}
        self.write_lock = asyncio.Lock()
        self.poll_lock = asyncio.Lock()
        self.pipeline: Services | None = None
        self.books_index: library.Index | None = None
        if settings.books_index:
            try:
                self.books_index = library.Index(settings.books_index)
            except sqlite3.Error as e:             # book search is optional; the api never fails over it
                log.warning("book_index_unavailable", path=str(settings.books_index), error=str(e))

    async def write(self) -> dict[str, int]:
        if self.core is None:
            return {"skipped_no_anki_core": 1}
        async with self.write_lock:
            return await write_approved(self.con, self.core, self.settings.media_roots)

    async def daily(self) -> None:
        """04:15: the only scheduled collection write (invariant 8). Re-read exams from the calendar, write
        approved cards, build exam decks (so today's new cards are in them), then walk decks and today's
        new-card limits, which have to come after the exam decks have taken their cards."""
        if self.pipeline is not None:
            try:
                sync_exams(self.pipeline)
            except Exception as e:  # noqa: BLE001
                log.warning("exam_sync_failed", error=str(e)[:300])
        await self.write()
        try:
            await self.exam_decks()
        except Exception as e:  # noqa: BLE001 -- a failed build is logged; yesterday's decks stay as they were
            log.warning("exam_decks_failed", error=str(e)[:300])
        try:
            await self.governor()
        except Exception as e:  # noqa: BLE001 -- no today-only limits means no new cards today, never too many
            log.warning("governor_failed", error=str(e)[:300])
        await self.nightly()

    async def nightly(self) -> None:
        """The rest of 04:15, all reads except the one suspension a leech replacement Aman applied is waiting on:
        the leech doctor, metrics, the learner snapshot the tutor reads, and on Sundays the report. Plus clearing
        clip videos whose time is up (media only, never the collection)."""
        steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
            ("leech_finish", self.leech_finish), ("leech_doctor", self.leech_doctor), ("metrics", self.metrics),
            ("learner_snapshot", self.learner_snapshot), ("clip_expiry", self.clip_expiry),
            ("neighbors_index", self.neighbors_index)]
        if self.today().weekday() == 6:
            steps.append(("weekly_report", self.weekly_report))
        for name, step in steps:
            try:
                await step()
            except Exception as e:  # noqa: BLE001 -- each step is independent; a failure is logged, the rest run
                log.warning(f"{name}_failed", error=str(e)[:300])

    def budget_minutes(self) -> float:
        return governor.Budget.from_yaml(self.budget()).minutes

    def doctor(self) -> leech.Doctor | None:
        if self.core is None or self.pipeline is None:
            return None
        return leech.Doctor(self.con, self.core, self.pipeline.llm, self.pipeline.embedder, self.schema,
                            (self.settings.skill_dir / "SKILL.md").read_text(encoding="utf-8"),
                            self.settings.repo / "data" / "anchors.yaml")

    async def leech_finish(self) -> int:
        if self.core is None:
            return 0
        async with self.write_lock:
            return await leech.finish_pending(self.con, self.core)

    async def leech_doctor(self, limit: int = leech.PER_NIGHT, dry_run: bool = False) -> dict[str, Any]:
        d = self.doctor()
        if d is None:
            return {"skipped": "needs anki-core and the pipeline (LLM and embeddings)"}
        return await leech.run(d, limit, dry_run)

    async def metrics(self) -> dict[str, Any]:
        if self.core is None:
            return {"skipped_no_anki_core": 1}
        today = self.today()
        m = await metrics.collect(self.core, self.con, today, self.settings.tz)
        day = today - timedelta(days=1)
        metrics.save(self.con, day, m)
        s = self.settings
        sent = await metrics.to_influx(s.influx_url, s.influx_org, s.influx_bucket, s.influx_token_file,
                                       metrics.line_protocol(day, s.tz, m))
        log.info("metrics_collected", day=day.isoformat(), influx=sent)
        return {"day": day.isoformat(), "influx": sent, **m}

    async def clip_expiry(self) -> None:
        cleared = clips.expire(self.con, self.settings.media_roots, clips.default_keep(self.settings.config_dir))
        log.info("clip_expiry", cleared=len(cleared), slugs=cleared)

    def neighbors_config(self) -> neighbors.Config:
        return neighbors.Config.load(self.settings.config_dir / "llm.yaml")

    async def neighbors_index(self) -> dict[str, int]:
        """The semantic-neighbours index (docs/embeddings.md): reads the collection, writes only the Ledger."""
        cfg = self.neighbors_config()
        if self.core is None or not cfg.enabled:
            return {"skipped": 1}
        return await neighbors.refresh(self.con, self.core, self.settings.ollama_url, cfg.model)

    async def learner_snapshot(self) -> dict[str, Any]:
        if self.core is None:
            return {"skipped_no_anki_core": 1}
        prof = await learner.profile(self.core, self.con, self.settings.tz, self.budget_minutes())
        learner.save_snapshot(self.con, prof)
        return prof

    async def weekly_report(self, deliver_now: bool = False) -> str:
        if self.core is None:
            return ""
        today = self.today()
        body = await report.build(self.core, self.con, today, self.settings.tz, self.budget_minutes())
        after = (datetime.now(UTC).isoformat(timespec="seconds") if deliver_now
                 else report.deliver_after(today, self.settings.tz))
        report.save(self.con, today, body, after)
        return body

    def budget(self) -> dict[str, Any]:
        return yaml.safe_load((self.settings.config_dir / "budget.yaml").read_text(encoding="utf-8"))

    def today(self) -> date:
        return datetime.now(ZoneInfo(self.settings.tz)).date()

    def courses(self) -> list[dict[str, Any]]:
        data = yaml.safe_load((self.settings.config_dir / "courses.yaml").read_text(encoding="utf-8"))
        return list(data["courses"])

    async def course_document(self, doc: Doc) -> bool:
        """A lecture, exam, assignment, or syllabus with a course tag: what the exam tools may read. The rest of
        Paperless (statements, IDs, receipts) is never readable through the propose token."""
        assert self.pipeline is not None
        t = await tags(self.pipeline)
        syllabus = await self.pipeline.paperless.document_type_id("Syllabus")
        kinds = {x for x in (t.lecture_type, t.exam_type, t.assignment_type, syllabus) if x is not None}
        return doc.document_type in kinds and any(tag in t.courses for tag in doc.tags)

    async def exam_decks(self) -> dict[str, Any]:
        if self.core is None:
            return {"skipped_no_anki_core": 1}
        async with self.write_lock:
            return await exam_decks.sync(self.con, self.core, self.today(), self.settings.tz,
                                         exam_decks.Window.from_budget(self.budget()))

    async def governor(self, apply: bool = True) -> dict[str, Any]:
        if self.core is None:
            return {"skipped_no_anki_core": 1}
        async with self.write_lock:
            return await governor.run(self.con, self.core, governor.Budget.from_yaml(self.budget()), self.today(),
                                      apply)

    async def poll(self) -> list[dict[str, Any]]:
        """One Paperless pass, then write whatever the policy auto-approved (PLAN §11: right after approval)."""
        if self.pipeline is None or self.poll_lock.locked():
            return []
        async with self.poll_lock:
            try:
                outcomes = await poll(self.pipeline)
            except Exception as e:  # noqa: BLE001 -- a bad pass is logged; the next one retries
                log.warning("poll_failed", error=str(e)[:300])
                return []
        if any(o.approved for o in outcomes):
            await self.write()
        self.sync_notes()
        return [asdict(o) for o in outcomes]

    def sync_notes(self, doc_id: int | None = None) -> None:
        """Questions in the lecture stubs: read back what Aman ticked, rewrite what changed."""
        root = self.settings.stubs_dir
        if root is None:
            return
        try:
            old = self.settings.old_notes_dir
            if old is not None and old.is_dir() and (moved := obsidian.retire_question_notes(self.con, old, root)):
                log.info("question_notes_retired", moved=moved)
            if doc_id is None:
                obsidian.sync_all(self.con, root)
            else:
                obsidian.sync_doc(self.con, root, doc_id)
        except OSError as e:
            log.warning("lecture_notes_failed", error=str(e)[:200])


def auth(level: Level) -> Callable[[Request, str | None], Awaitable[Level]]:
    """Dependency: the propose token passes only where proposing is enough; the approve token passes everywhere."""
    allowed: list[Level] = ["propose", "approve"] if level == "propose" else ["approve"]

    async def check(request: Request, authorization: Annotated[str | None, Header()] = None) -> Level:
        tokens: dict[Level, str | None] = request.app.state.mn.tokens
        raw = (authorization or "").strip().strip('"').strip()
        given = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
        for granted in allowed:
            token = tokens[granted]
            if token and given and hmac.compare_digest(given, token):
                return granted
        log.warning("auth_failed", path=request.url.path, header_present=bool(authorization),
                    bearer_prefix=raw.lower().startswith("bearer "), token_length=len(given))
        raise HTTPException(401, "missing or wrong token: send the header Authorization = Bearer <token>")
    return check


Proposer = Annotated[Level, Depends(auth("propose"))]
Approver = Annotated[Level, Depends(auth("approve"))]


def intake_fields(course: str, number: str | None, title: str, kind: str, stem: str,
                  codes: set[str]) -> tuple[str, int | None, str, str]:
    """Shortcut fields, falling back to the file name ("MATH 309 L03 - Vector Spaces") when a field is blank or
    arrives as the literal variable name (a typed "Course" instead of the inserted variable)."""
    from mnemosyne.pipeline import KINDS

    course = course.upper().replace(" ", "").replace("-", "")
    parsed = parse_title(stem)
    if course not in codes and parsed and parsed.course in codes:
        course = parsed.course
    num = int(number) if number and number.strip().isdigit() else None
    if title.strip().lower() in ("", "title"):
        title = stem
    if kind not in KINDS:
        log.info("intake_kind_defaulted", given=kind)
        kind = "slides"
    return course, num, title.strip(), kind


def course_mismatch(course: str, stem: str, codes: set[str]) -> str | None:
    """A file named for one course ("MATH 309 L07 - …") sent with another picked: refused, since one of the two is a
    mis-tap and filing it under the wrong course mixes it into that course's lectures and exams."""
    parsed = parse_title(stem)
    if parsed and parsed.course in codes and course in codes and parsed.course != course:
        return f"the file is {parsed.course} but {course} was picked; share it again with the right course"
    return None


def create_app(settings: Settings, core: Core | None = None, con: sqlite3.Connection | None = None,
               schedule: bool = True) -> FastAPI:
    con = con or db.connect(settings.ledger)
    db.migrate(con)
    schema = Schema.load(settings.notetypes_dir, settings.config_dir / "decks.yaml")
    if core is None and (token := read_secret(settings.anki_core_token_file)):
        core = CoreClient(settings.anki_core_url, token)
    state = State(settings, con, schema, core)
    if core is not None and (ptoken := read_secret(settings.paperless_token_file)):
        state.pipeline = Services(con, schema, core, Router(settings.config_dir / "llm.yaml"),
                                  OllamaEmbedder(settings.ollama_url,
                                                 neighbors.Config.load(settings.config_dir / "llm.yaml").dedupe_model),
                                  Paperless(settings.paperless_url, ptoken, settings.paperless_public_url),
                                  settings.repo, settings.skill_dir, settings.stubs_dir,
                                  HyperionClient(settings.helios_mcp_url) if settings.helios_mcp_url else None,
                                  settings.calibre_dir, settings.calibre_public_url)
        state.pipeline.books_index = state.books_index

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        scheduler = AsyncIOScheduler(timezone=settings.tz)
        if schedule:
            hour, minute = (int(x) for x in settings.daily_write_at.split(":"))
            scheduler.add_job(state.daily, "cron", hour=hour, minute=minute, id="daily_write")  # pyright: ignore[reportUnknownMemberType]
            if state.pipeline is not None and settings.poll_minutes > 0:
                scheduler.add_job(state.poll, "interval", minutes=settings.poll_minutes, id="paperless_poll",  # pyright: ignore[reportUnknownMemberType]
                                  max_instances=1, coalesce=True)
            scheduler.start()
        if schedule and state.books_index is not None:
            index = state.books_index

            def warm() -> None:
                for b, pdf in shelf():
                    try:
                        index.ensure(pdf, b)
                    except Exception as e:  # noqa: BLE001 -- one bad PDF doesn't stop the rest
                        log.warning("book_index_failed", book=b.key, error=str(e)[:200])
                log.info("book_index_warm", books=len(shelf()))
            threading.Thread(target=warm, name="book-index", daemon=True).start()
        yield
        if schedule:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="mnemosyne", lifespan=lifespan)
    app.state.mn = state

    @app.get("/health")
    async def health() -> dict[str, Any]:
        state.con.execute("SELECT 1 FROM card_batch LIMIT 1")
        return {"ok": True, "anki_core": state.core is not None, "pipeline": state.pipeline is not None}

    @app.get("/courses/codes")
    async def course_codes() -> list[str]:
        """A plain list for the iPad Shortcut's Choose from List (no loop needed). "none" is for a paper read for
        himself, not for a course."""
        data = yaml.safe_load((settings.config_dir / "courses.yaml").read_text(encoding="utf-8"))
        return [str(c["code"]) for c in data["courses"]] + ["none"]

    @app.get("/courses")
    async def courses() -> list[dict[str, Any]]:
        """For the iPad Shortcut's course menu."""
        data = yaml.safe_load((settings.config_dir / "courses.yaml").read_text(encoding="utf-8"))
        return [{"code": c["code"], "name": c["name"]} for c in data["courses"]]

    @app.post("/inbox")
    async def inbox(body: Proposed, level: Proposer, background: BackgroundTasks) -> dict[str, object]:
        if body.origin in PREAPPROVED_ORIGINS and level != "approve":
            raise HTTPException(403, f"origin {body.origin!r} needs the approve token")
        if body.checks:
            if state.pipeline is None:
                raise HTTPException(503, "pipeline not configured")
            if level != "approve":
                raise HTTPException(403, "checked proposals can auto-approve, so they need the approve token")
            async with state.poll_lock:
                outcome = await check_and_propose(state.pipeline, body.payload, body.origin, body.payload.source.kind,
                                                  calibrating=False)
            log.info("proposed_checked", origin=body.origin, **{k: v for k, v in asdict(outcome).items()})
            if outcome.approved:
                background.add_task(state.write)
            return asdict(outcome)
        if state.pipeline is not None and body.origin not in PREAPPROVED_ORIGINS:
            # Held, with the verify gate and dedupe. The lecture poller can hold the lock for minutes and the MCP
            # tool gives up after 60 s, so past BUSY_WAIT the cards are kept unchecked, with the reason.
            try:
                await asyncio.wait_for(state.poll_lock.acquire(), BUSY_WAIT)
            except TimeoutError:
                busy = default_decide(body.origin, unchecked_because("a lecture was being carded"))
                result = propose(state.con, state.schema, body.payload, body.origin, busy)
                log.info("proposed_unchecked_busy", origin=body.origin, batch=result.batch_id, created=result.created)
                return result.summary()
            try:
                summary = await propose_checked(state.pipeline, body.payload, body.origin)
            finally:
                state.poll_lock.release()
            log.info("proposed", origin=body.origin, checked=True,
                     **{k: v for k, v in summary.items() if k != "invalid"})
            return summary
        result = propose(state.con, state.schema, body.payload, body.origin)
        log.info("proposed", origin=body.origin, **{k: v for k, v in result.summary().items() if k != "invalid"},
                 invalid=len(result.invalid))
        if result.status == "approved" and not result.already_proposed:
            background.add_task(state.write)
        return result.summary()

    @app.get("/batches")
    async def batches(_: Approver, status: str | None = None) -> list[dict[str, Any]]:
        return [asdict(b) for b in store.list_batches(state.con, status)]

    @app.get("/batches/{batch_id}")
    async def batch(batch_id: str, _: Approver) -> dict[str, Any]:
        found = store.get_batch(state.con, batch_id)
        if found is None:
            raise HTTPException(404, "no such batch")
        return {**asdict(found), "drafts": [asdict(d) for d in store.batch_drafts(state.con, batch_id)]}

    @app.post("/batches/{batch_id}/decision")
    async def decision(batch_id: str, body: Decision, _: Approver, background: BackgroundTasks) -> dict[str, Any]:
        if store.get_batch(state.con, batch_id) is None:
            raise HTTPException(404, "no such batch")
        changed = store.decide(state.con, batch_id, body.action == "approve", body.by)
        if changed and body.action == "approve":
            background.add_task(state.write)
        return {"changed": changed, "batch": asdict(store.get_batch(state.con, batch_id))}  # type: ignore[arg-type]

    async def poll_until_seen(rounds: int = 10, every_s: float = 30) -> None:
        """After an upload, poll every 30 s for up to 5 min so the card batch lands well inside 10 minutes."""
        for _ in range(rounds):
            await asyncio.sleep(every_s)
            if await state.poll():
                return

    @app.post("/intake", response_model=None)
    async def intake_upload(_: Proposer, background: BackgroundTasks, file: Annotated[UploadFile, File()],
                            course: Annotated[str, Form()], kind: Annotated[str, Form()] = "slides",
                            number: Annotated[str | None, Form()] = None,
                            title: Annotated[str, Form()] = "") -> dict[str, Any] | JSONResponse:
        """The iPad Shortcut: file a PDF into Paperless with the right tags; the pipeline picks it up."""
        if state.pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        pdf = await file.read()
        kind = kind.strip().lower()
        log.info("intake_request", course=course, kind=kind, number=number, title=title[:80], filename=file.filename,
                 content_type=file.content_type, size=len(pdf), head=pdf[:8].hex())
        if not pdf.lstrip()[:5].startswith(b"%PDF"):
            log.warning("intake_rejected", reason="not a PDF")
            raise HTTPException(400, f"send a PDF (got {file.content_type}, {len(pdf)} bytes)")
        filename = file.filename or "upload.pdf"
        stem = filename.removesuffix(".pdf")
        codes = set(await course_codes())
        course, num, title, kind = intake_fields(course, number, title, kind, stem, codes)
        if why := course_mismatch(course, stem, codes):
            log.warning("intake_rejected", reason=why)
            # The Shortcut's notification shows `title`, so the refusal is what it says
            return JSONResponse({"detail": why, "title": f"NOT filed: {why}"}, status_code=409)
        try:
            result = await intake(state.pipeline, pdf, filename, course, num, title, kind)
        except PipelineError as e:
            log.warning("intake_rejected", reason=str(e))
            raise HTTPException(400, str(e)) from e
        pipeline = state.pipeline

        async def after_upload() -> None:
            async with state.poll_lock:   # replace before the poller could see both versions
                settled = await settle_upload(pipeline, str(result["task"]), list(result["replaces"]), pdf=pdf,
                                              course=course, title=str(result["title"]))
            if kind in ("slides", "notes", "paper") and "duplicate_of" not in settled:
                await poll_until_seen()
            elif settled.get("outcome"):
                await state.write()

        background.add_task(after_upload)
        return result

    # ---------- readings: textbook sections, from the ebook in Calibre or a scan of Aman's copy ----------

    def catalog() -> dict[str, books.Book]:
        return books.load(settings.config_dir / "books.yaml")

    @app.get("/readings")
    async def readings(_: Approver) -> dict[str, Any]:
        """The review page's Read tab: the books, scans tagged `mnemosyne` that still need a book and section, and
        what was carded lately."""
        if state.pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        shelf = [{"key": b.key, "name": b.name, "title": b.title,
                  "course": b.course or ", ".join(dict.fromkeys(c for *_, c in b.chapter_courses)) or None,
                  "ebook": bool(settings.calibre_dir and b.calibre is not None
                                and books.pdf_path(settings.calibre_dir, b.calibre))} for b in catalog().values()]
        try:
            scans = await waiting_scans(state.pipeline)
        except Exception as e:  # noqa: BLE001 -- the rest of the tab still works without Paperless
            log.warning("waiting_scans_failed", error=str(e)[:200])
            scans = []
        return {"books": shelf, "scans": scans, "recent": recent_readings(state.con)}

    @app.get("/readings/books/{key}/outline")
    async def reading_outline(key: str, _: Approver) -> list[dict[str, Any]]:
        book = books.find(catalog(), key)
        pdf = (books.pdf_path(settings.calibre_dir, book.calibre)
               if book and settings.calibre_dir and book.calibre is not None else None)
        if book is None or pdf is None:
            raise HTTPException(404, "that book isn't in Calibre")
        toc, _count = books.book_outline(pdf, book)
        return books.sections(toc)

    # ---- books: read-only (the propose token is enough), for the tutor and sessions: quote, cite, practice ----

    def shelved(key: str) -> tuple[books.Book, Path]:
        book = books.find(catalog(), key)
        pdf = (books.pdf_path(settings.calibre_dir, book.calibre)
               if book and settings.calibre_dir and book.calibre is not None else None)
        if book is None or pdf is None:
            raise HTTPException(404, f"no book {key!r} in Calibre; GET /books lists them")
        return book, pdf

    def shelf() -> list[tuple[books.Book, Path]]:
        if settings.calibre_dir is None:
            return []
        return [(b, p) for b in catalog().values()
                if b.calibre is not None and (p := books.pdf_path(settings.calibre_dir, b.calibre)) is not None]

    def book_status(book: books.Book, pdf: Path) -> library.Status:
        return state.books_index.status_of(pdf, book) if state.books_index else library.status(pdf, book)

    def courses_of(book: books.Book) -> list[str]:
        return [book.course] if book.course else list(dict.fromkeys(c for *_, c in book.chapter_courses))

    @app.get("/books")
    async def books_list(_: Proposer, book: str | None = None) -> dict[str, Any]:
        """The books with how to treat each (status), or one book's chapters and sections."""
        if book:
            b, pdf = shelved(book)
            toc, n = await asyncio.to_thread(books.book_outline, pdf, b)
            st = await asyncio.to_thread(book_status, b, pdf)
            return {"key": b.key, "name": b.name, "title": b.title, "courses": courses_of(b), "status": asdict(st),
                    "note": library.NOTES[st.text], "pages": n, "outline": books.sections(toc)}

        def rows() -> list[dict[str, Any]]:
            return [{"key": b.key, "name": b.name, "title": b.title, "courses": courses_of(b),
                     "status": asdict(book_status(b, pdf))} for b, pdf in shelf()]
        return {"books": await asyncio.to_thread(rows), "notes": library.NOTES}

    @app.get("/books/search")
    async def books_search(q: str, _: Proposer, book: str | None = None, limit: int = 10) -> dict[str, Any]:
        """Where the books talk about something: page hits with printed page, section, and a snippet."""
        if state.books_index is None:
            raise HTTPException(503, "no book index configured (MNEMOSYNE_BOOKS_INDEX)")
        index = state.books_index
        targets = [shelved(book)] if book else shelf()

        def run() -> list[dict[str, Any]]:
            for b, pdf in targets:
                index.ensure(pdf, b)
            return index.search(q, [b.key for b, _ in targets], limit)
        hits = await asyncio.to_thread(run)
        names = {b.key: b.name for b, _ in targets}
        return {"query": q, "hits": [h | {"cite": f"{names[h['book']]}, p. {h['page']}"} for h in hits]}

    @app.get("/books/{key}/read")
    async def books_read(key: str, where: str, _: Proposer, images: Literal["auto", "yes", "no"] = "auto") -> dict[str, Any]:
        """A section, chapter, or pages as printed text with a citation; page images when the text can't carry the math."""
        b, pdf = shelved(key)
        try:
            return await asyncio.to_thread(library.read, pdf, b, where, images)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/books/{key}/problems")
    async def books_problems(key: str, where: str, _: Proposer, difficulty: int = 0, limit: int = 8, offset: int = 0,
                             images: Literal["auto", "yes", "no"] = "auto") -> dict[str, Any]:
        """Practice problems from a chapter or section, with the book's difficulty marks when it has them."""
        b, pdf = shelved(key)
        try:
            return await asyncio.to_thread(library.problems, pdf, b, where, max(0, min(difficulty, 3)),
                                           max(1, min(limit, 30)), max(0, offset), images)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/readings")
    async def reading(body: ReadingIn, _: Approver, background: BackgroundTasks) -> dict[str, Any]:
        """Card a textbook section. Checked here (book, section, pages), carded in the background; the cards wait
        on the review page like any lecture's. A scan gets a title that names it ("Chen 2.3 - Nonuniform B Field")."""
        pipeline = state.pipeline
        if pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        book = books.find(catalog(), body.book)
        if book is None:
            raise HTTPException(404, f"no book {body.book!r} in config/books.yaml")
        try:
            books.tag_for(body.where)
            found = ebook(pipeline, book, body.where) if body.doc_id is None else None
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if body.doc_id is None and found is None:
            raise HTTPException(400, f"{book.name} isn't in Calibre; scan the pages into Paperless instead")
        doc = await pipeline.paperless.document(body.doc_id) if body.doc_id is not None else None
        if doc is not None:
            try:
                found = ebook(pipeline, book, body.where)
            except ValueError:
                found = None
            name = f"{book.name} {body.where.strip()}" + (f" - {found[0].label}" if found else "")
            if doc.title != name:
                await pipeline.paperless.set_title(doc.id, name)
                doc = await pipeline.paperless.document(doc.id)
        span = found[0] if found else None
        if not body.again and job_attempts(state.con, reading_ref(book, body.where, doc), "reading")[0] == "done":
            return {"already": True, "book": book.name, "label": span.label if span else body.where}

        async def run() -> None:
            async with state.poll_lock:
                out = await run_reading(pipeline, book, body.where, doc, again=body.again)
            if out is not None and out.approved:
                await state.write()

        background.add_task(run)
        log.info("reading_queued", book=book.key, where=body.where, doc=body.doc_id)
        return {"queued": True, "book": book.name, "label": span.label if span else body.where,
                "pages": [span.first, span.last] if span and doc is None else None}

    # ---------- the review page: everything waiting for Aman, from any device on the tailnet ----------

    @app.get("/media/preview", include_in_schema=False)
    async def media_preview(note_type: str, key: str, _: Approver) -> FileResponse:
        """The IPA recording or ASL video a card will get when it's written, for the review page's player."""
        path = audio.find(settings.media_roots, note_type, key)
        if path is None:
            raise HTTPException(404, "no file for that card")
        kind = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav", ".mp4": "video/mp4",
                ".webm": "video/webm"}.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=kind, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/review", response_class=HTMLResponse, include_in_schema=False)
    async def review_page() -> HTMLResponse:
        """The page itself is static; every call it makes carries the approve token the device stored."""
        return HTMLResponse(PAGE_HTML.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

    # ---------- /watch: animated clips (Manim scenes rendered by scripts/render.py), same token as /review ----------

    @app.get("/watch", response_class=HTMLResponse, include_in_schema=False)
    async def watch_page() -> HTMLResponse:
        return HTMLResponse(WATCH_HTML.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

    def clip_list() -> list[dict[str, Any]]:
        return clips.library(settings.media_roots, state.con, settings.render_queue,
                             clips.default_keep(settings.config_dir))

    def one_clip(slug: str) -> dict[str, Any]:
        found = next((c for c in clip_list() if c["slug"] == slug), None)
        if found is None:
            raise HTTPException(404, "no such clip")
        return found

    @app.get("/watch/clips")
    async def watch_clips(_: Approver) -> dict[str, Any]:
        return {"clips": clip_list()}

    @app.get("/watch/clips/{slug}/video", include_in_schema=False)
    async def watch_video(slug: str, _: Approver, via: str = "page") -> FileResponse:
        """The clip itself. Each fetch is one open: the page fetches it once per viewing."""
        path = clips.video(settings.media_roots, slug)
        if path is None:
            raise HTTPException(404, "no video for that clip (it may have been cleared: render it again)")
        clips.record_view(state.con, slug, via)
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})

    @app.put("/watch/clips/{slug}/keep")
    async def watch_keep(slug: str, body: ClipKeep, _: Approver) -> dict[str, Any]:
        """How long this clip keeps its video: `days` (None = the default in config/clips.yaml), or forever."""
        one_clip(slug)
        try:
            clips.set_keep(state.con, slug, body.days, body.forever)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return one_clip(slug)

    @app.post("/watch/clips/{slug}/clear")
    async def watch_clear(slug: str, _: Approver) -> dict[str, Any]:
        """Delete the video now; the code, stills, and metadata stay, so it can be rendered again."""
        one_clip(slug)
        if clips.clear(state.con, settings.media_roots, slug):
            log.info("clip_cleared", slug=slug, by="page")
        return one_clip(slug)

    @app.post("/watch/clips/{slug}/render")
    async def watch_render(slug: str, _: Approver) -> dict[str, Any]:
        """Render the clip again from its own scene.py, in a sandbox (scripts/render_runner.py, about a minute)."""
        try:
            clips.request_render(settings.media_roots, settings.render_queue, slug)
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from None
        return one_clip(slug)

    @app.get("/watch/clips/{slug}/scene", response_class=PlainTextResponse, include_in_schema=False)
    async def watch_scene(slug: str, _: Approver) -> PlainTextResponse:
        d = clips.folder(settings.media_roots, slug)
        if d is None or not (d / "scene.py").is_file():
            raise HTTPException(404, "no source for that clip")
        return PlainTextResponse((d / "scene.py").read_text(encoding="utf-8"))

    # ---------- clips a chat submits as code: rendered in a sandbox by scripts/render_runner.py ----------

    @app.get("/clips")
    async def clips_list(_: Proposer) -> dict[str, Any]:
        """The published clips, so a chat can find one before making another."""
        return {"clips": [{k: c[k] for k in ("slug", "title", "courses", "duration", "has_video", "watch_for")}
                          for c in clip_list()], "watch": f"{settings.public_url or ''}/watch"}

    @app.post("/clips")
    async def clips_submit(body: ClipSubmission, level: Proposer) -> dict[str, Any]:
        """Queue a chat's scene to be rendered and published. Only the code's text comes in; it runs only inside
        the render sandbox (docs/security/clip-submit.md)."""
        if settings.media_dir is None:
            raise HTTPException(503, "no media folder on this server")
        try:
            made = clips.submit(state.con, settings.media_dir, settings.render_queue, body.code, body.title,
                                body.courses, body.topics, body.watch_for, body.replace, level)
        except clips.SubmitError as e:
            raise HTTPException(422, str(e)) from None
        log.info("clip_submitted", slug=made["slug"], scene=made["scene"], bytes=len(body.code), by=level)
        return made

    @app.get("/clips/toolkit")
    async def clips_toolkit(_: Proposer) -> dict[str, Any]:
        """amanim's API, the house style, and harvest candidates, read live from the Obelisk's copies."""
        return clips.toolkit(settings.scenes_dir, settings.amanim_dir, settings.media_roots)

    @app.get("/clips/{slug}")
    async def clips_status(slug: str, _: Proposer) -> dict[str, Any]:
        try:
            return clips.report(settings.media_roots, settings.render_queue, slug, settings.public_url,
                                scenes=settings.scenes_dir)
        except LookupError as e:
            raise HTTPException(404, str(e)) from None

    # ---------- lecture videos the tutor has watched (videos.py): read with the propose token, like the books ----------

    def watched(run: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return run()
        except videos.VideoError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/videos")
    async def videos_list(_: Proposer) -> dict[str, Any]:
        return videos.library(settings.media_roots, settings.watch_queue)

    @app.get("/videos/search")
    async def videos_search(q: str, _: Proposer, video: str = "", limit: int = 10) -> dict[str, Any]:
        return watched(lambda: videos.search(settings.media_roots, q, video, limit))

    @app.get("/videos/{vid}/watch")
    async def videos_span(vid: str, _: Proposer, start: str = "", end: str = "",
                          images: Literal["auto", "yes", "no"] = "auto") -> dict[str, Any]:
        return watched(lambda: videos.span(settings.media_roots, vid, start, end, images))

    @app.get("/videos/{vid}/frames")
    async def videos_frames(vid: str, at: str, _: Proposer) -> dict[str, Any]:
        return watched(lambda: videos.frames(settings.media_roots, vid, [t for t in at.split(",") if t.strip()]))

    @app.post("/videos/watch")
    async def videos_request(body: VideoRequest, level: Proposer) -> dict[str, Any]:
        """Queue a YouTube video for the watcher. It only adds a request (the propose token is enough); the watcher
        fetches YouTube only, and keeps captions and frames, never the video."""
        result = watched(lambda: videos.request(settings.media_roots, settings.watch_queue, body.video, level))
        log.info("video_requested", **result)
        return result

    @app.post("/videos/study")
    async def videos_study(body: VideoStudy, level: Proposer) -> dict[str, Any]:
        """Study mode: a span of an animation, densely (motion, cuts, palette, composition). Returns the study when it's
        done; otherwise only adds a request to the watcher's queue, so the propose token is enough."""
        result = watched(lambda: videos.study(settings.media_roots, settings.watch_queue, body.video, body.start,
                                              body.end, body.fps, body.look, level))
        if result["state"] != "studied":
            log.info("video_study_requested", **{k: v for k, v in result.items() if k in ("id", "span", "state")})
        return result

    @app.get("/review/queue")
    async def review_queue(_: Approver) -> dict[str, Any]:
        waiting: list[dict[str, Any]] = []
        for b in store.list_batches(state.con, "pending", limit=200):
            if b.origin == reviews.ORIGIN:
                continue   # shown inside its review set
            drafts =[asdict(d) for d in store.batch_drafts(state.con, b.id) if d.status == "pending"]
            if drafts:
                waiting.append({**asdict(b), "drafts": drafts})
        since = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        unfinished = stuck(state.con)
        return {
            "batches": waiting,
            "reviews": store.list_reviews(state.con),
            "stuck": store.stuck(state.con),
            # Lectures (and graded work, prep, papers) the pipeline couldn't finish, so a failure isn't silent
            "documents": await present(state.pipeline.paperless, unfinished) if state.pipeline is not None
            else unfinished,
            "maps": [{**dict(m), "detail": json.loads(m["detail_json"])} for m in exams.pending_maps(state.con)],
            "exams": [{**dict(e), "lectures": json.loads(e["lectures"]) if e["lectures"] else None}
                      for e in exams.upcoming(state.con, since)],
            "coverage": review.coverage(store.approved_tags(state.con)),
            "decks": sorted(state.schema.decks),
            "leeches": leech.cases(state.con, "proposed"),
            "questions": qstore.listing(state.con, "open"),
            "question_stats": qstore.stats(state.con, state.today() - timedelta(days=7)),
        }

    @app.post("/batches/{batch_id}/cards")
    async def decide_cards(batch_id: str, body: review.CardActions, _: Approver,
                           background: BackgroundTasks) -> dict[str, Any]:
        """Approve, edit, reject, or merge single cards. Edits are checked like proposals; a card with problems is left
        pending and its problems returned, while the rest of the request still goes through. Merge settles a card into
        the note dedupe matched (its tags join it); merge up also rewrites that note in this card's wording."""
        if store.get_batch(state.con, batch_id) is None:
            raise HTTPException(404, "no such batch")
        drafts = {d.mnemosyne_id: d for d in store.batch_drafts(state.con, batch_id)}
        decided: list[store.CardDecision] = []
        problems: dict[str, list[str]] = {}
        merged = 0
        for action in body.decisions:
            draft = drafts.get(action.mnemosyne_id)
            if draft is None or draft.status != "pending":
                problems[action.mnemosyne_id] = [f"not pending ({draft.status if draft else 'not in this batch'})"]
                continue
            if action.action in ("merge", "merge_up"):
                if state.core is None:
                    problems[action.mnemosyne_id] = ["anki-core isn't configured"]
                    continue
                try:
                    async with state.write_lock:
                        done = await merge.merge(state.con, state.core, state.schema.decks, draft,
                                                 action.action == "merge_up", body.by, action.note)
                except (ValueError, AnkiCoreError) as e:
                    problems[action.mnemosyne_id] = [str(e)[:300]]
                    continue
                merged += 1
                if done["write"]:
                    background.add_task(state.write)
                continue
            got = review.resolve(draft, action, state.schema)
            if isinstance(got, list):
                problems[action.mnemosyne_id] = got
            else:
                decided.append(got)
        changed = store.decide_cards(state.con, batch_id, decided, body.by) + merged
        if any(d.approve for d in decided):
            background.add_task(state.write)
        log.info("cards_decided", batch=batch_id, changed=changed, approved=sum(d.approve for d in decided),
                 edited=sum(d.before is not None for d in decided), merged=merged, problems=len(problems))
        return {"changed": changed, "problems": problems, "batch": asdict(store.get_batch(state.con, batch_id))}  # type: ignore[arg-type]

    # ---------- review sets: batches Claude wrote in a session, reviewed card by card on the page ----------

    @app.post("/reviews")
    async def review_push(body: reviews.ReviewIn, _: Proposer) -> dict[str, Any]:
        """Holds every new card for Aman (nothing is approved here), so the propose token is enough."""
        if store.review_exists(state.con, body.id):
            raise HTTPException(409, f"review {body.id!r} exists; never overwrite Aman's decisions, use a new id")
        try:
            parsed = reviews.parse_cards(body, state.schema)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        fresh = reviews.new_cards(state.con, parsed)
        dropped: dict[str, str] = {}
        if fresh and state.pipeline is not None:
            async with state.poll_lock:
                outcome = await check_and_propose(state.pipeline, Payload(source=body.source, cards=fresh),
                                                  reviews.ORIGIN, body.source.kind, calibrating=False, hold=True)
            dropped = outcome.dropped
        elif fresh:
            reviews.propose_held(state.con, state.schema, body.source, fresh)
        result = reviews.create(state.con, body, parsed, dropped)
        log.info("review_pushed", **result, new=len(fresh))
        return {**result, "new": len(fresh), "url": f"/review#r/{body.id}"}

    @app.get("/reviews")
    async def review_list(_: Approver, status: str = "open,reviewed") -> list[dict[str, Any]]:
        return store.list_reviews(state.con, tuple(status.split(",")))

    @app.get("/reviews/{review_id}")
    async def review_get(review_id: str, _: Approver) -> dict[str, Any]:
        found = store.get_review(state.con, review_id)
        if found is None:
            raise HTTPException(404, "no such review")
        return found

    @app.post("/reviews/{review_id}/items")
    async def review_items(review_id: str, body: reviews.ItemActions, _: Approver,
                           background: BackgroundTasks) -> dict[str, Any]:
        try:
            result = reviews.decide(state.con, state.schema, review_id, body.decisions, body.by)
        except KeyError as e:
            raise HTTPException(404, "no such review") from e
        if result.pop("write"):
            background.add_task(state.write)
        log.info("review_decided", review=review_id, changed=result["changed"], problems=len(result["problems"]))
        return result

    @app.delete("/reviews/{review_id}")
    async def review_delete(review_id: str, _: Approver) -> dict[str, bool]:
        """Undo a mistaken push, only while nothing in it is decided. Held drafts stay; a re-push links them."""
        if not store.delete_review(state.con, review_id):
            raise HTTPException(409, "only an open review with no decisions can be deleted")
        return {"changed": True}

    @app.put("/reviews/{review_id}/notes")
    async def review_notes(review_id: str, body: reviews.ReviewNotes, _: Approver) -> dict[str, bool]:
        return {"changed": store.set_review_notes(state.con, review_id, body.notes.strip(), body.promote)}

    @app.post("/reviews/{review_id}/finish")
    async def review_finish(review_id: str, _: Approver) -> dict[str, bool]:
        return {"changed": store.set_review_status(state.con, review_id, "reviewed")}

    @app.post("/reviews/{review_id}/reopen")
    async def review_reopen(review_id: str, _: Approver) -> dict[str, bool]:
        """Undo Finish, until Claude has applied the batch."""
        return {"changed": store.reopen_review(state.con, review_id)}

    @app.get("/reviews/{review_id}/export")
    async def review_export(review_id: str, _: Approver) -> dict[str, Any]:
        found = store.get_review(state.con, review_id)
        if found is None:
            raise HTTPException(404, "no such review")
        return reviews.export(found)

    @app.post("/reviews/{review_id}/applied")
    async def review_applied(review_id: str, _: Approver) -> dict[str, bool]:
        return {"changed": store.set_review_status(state.con, review_id, "applied")}

    @app.get("/review/decisions")
    async def review_decisions(_: Approver, since: str = "1970-01-01") -> list[dict[str, Any]]:
        """Per-card decisions made on the page since a time, with notes and what edits replaced."""
        return store.decided_since(state.con, since)

    @app.post("/drafts/{mnemosyne_id}/revise")
    async def revise_draft(mnemosyne_id: str, body: review.Revision, _: Approver,
                           background: BackgroundTasks) -> dict[str, Any]:
        """Edit a card that is already approved or in Anki. It passes the same checks as a proposal, keeps what it
        replaced, and the next write updates the note in place (upsert on MnemosyneID; nothing is deleted)."""
        draft = store.get_draft(state.con, mnemosyne_id)
        if draft is None:
            raise HTTPException(404, "no such draft")
        if draft.status not in ("approved", "written", "error"):
            raise HTTPException(409, f"draft is {draft.status}; decide pending cards on the review page")
        got = review.resolve(draft, review.CardAction(mnemosyne_id=mnemosyne_id, action="edit", note=body.note,
                                                      fields=body.fields, tags=body.tags, deck=body.deck), state.schema)
        if isinstance(got, list):
            raise HTTPException(400, "; ".join(got))
        if got.before is None:
            return {"changed": False}
        store.revise(state.con, got, body.by)
        background.add_task(state.write)
        log.info("draft_revised", mnemosyne_id=mnemosyne_id, by=body.by, changed=sorted(got.before))
        return {"changed": True, "before": got.before}

    @app.post("/drafts/{mnemosyne_id}/tags")
    async def draft_tags(mnemosyne_id: str, body: TagAdd, _: Approver) -> dict[str, Any]:
        """Connect a card to more courses, topics, and principles (canon 22). Tags only add. A card waiting for review
        gains them in the Ledger; a card in Anki gets them on its next write (an upsert that adds tags)."""
        draft = store.get_draft(state.con, mnemosyne_id)
        if draft is None:
            raise HTTPException(404, "no such draft")
        if bad := [t for t in body.add if not TAG.match(t)]:
            raise HTTPException(400, f"bad tags: {', '.join(bad)}")
        row = state.con.execute("SELECT id FROM card_draft WHERE mnemosyne_id = ?", (mnemosyne_id,)).fetchone()
        if draft.status in ("pending", "approved"):
            return {"gained": store.add_draft_tags(state.con, int(row["id"]), body.add), "status": draft.status}
        if draft.status in ("written", "error"):
            new = list(dict.fromkeys([*draft.tags, *body.add]))
            if new == draft.tags:
                return {"gained": [], "status": draft.status}
            store.revise(state.con, store.CardDecision(mnemosyne_id, approve=True, note=body.note, tags=new,
                                                       before={"tags": draft.tags}), body.by)
            await state.write()
            return {"gained": [t for t in new if t not in draft.tags], "status": "written"}
        raise HTTPException(409, f"draft is {draft.status}")

    @app.post("/drafts/{mnemosyne_id}/withdraw")
    async def withdraw_draft(mnemosyne_id: str, body: review.Withdrawal, _: Approver) -> dict[str, bool]:
        """Take back an approved card that never reached Anki (e.g. its front duplicates an existing note)."""
        changed = store.withdraw(state.con, mnemosyne_id, body.note, body.by)
        if not changed:
            raise HTTPException(409, "only an approved card that isn't in Anki yet can be withdrawn")
        log.info("draft_withdrawn", mnemosyne_id=mnemosyne_id, by=body.by)
        return {"changed": True}

    @app.post("/exams/map/{course}/decision")
    async def map_decision(course: str, body: review.MapDecision, _: Approver) -> dict[str, bool]:
        if body.lectures and any(n < 1 or n > 99 for ns in body.lectures.values() for n in ns):
            raise HTTPException(400, "lecture numbers run 1-99")
        return {"changed": exams.decide_map(state.con, course.upper(), body.action == "confirm", body.lectures)}

    @app.put("/exams/{exam_id}/lectures")
    async def exam_lectures(exam_id: int, body: review.Lectures, _: Approver) -> dict[str, bool]:
        if any(n < 1 or n > 99 for n in body.lectures):
            raise HTTPException(400, "lecture numbers run 1-99")
        if not exams.set_lectures(state.con, exam_id, body.lectures):
            raise HTTPException(404, "no such exam")
        return {"changed": True}

    @app.put("/exams/{exam_id}/date")
    async def exam_date(exam_id: int, body: review.ExamDate, _: Approver) -> dict[str, Any]:
        """Move an exam (it was rescheduled before the calendar knew). Its deck and pack follow the new date."""
        try:
            day = date.fromisoformat(body.date)
            at = time.fromisoformat(body.time) if body.time else None
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        starts_at = exams.set_date(state.con, exam_id, day, at, body.by)
        if starts_at is None:
            raise HTTPException(404, "no such exam")
        log.info("exam_moved", exam=exam_id, starts_at=starts_at, by=body.by)
        return {"changed": True, "starts_at": starts_at}

    @app.post("/exams/sync")
    async def exams_sync(_: Approver) -> dict[str, int]:
        if state.pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        return sync_exams(state.pipeline)

    @app.post("/exams/map/{course}")
    async def exams_map(course: str, _: Approver) -> dict[str, Any]:
        """Propose the lecture coverage of each exam from the course syllabus; confirmed in Telegram."""
        if state.pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        try:
            return await build_map(state.pipeline, course.upper())
        except PipelineError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/exams/decks")
    async def exam_decks_now(_: Approver) -> dict[str, Any]:
        """Build exam decks now instead of at 04:15 (e.g. right after confirming a map)."""
        return await state.exam_decks()

    @app.post("/schedule/run")
    async def schedule_now(_: Approver, dry_run: bool = False) -> dict[str, Any]:
        """The walk decks and today's new-card limits now, instead of at 04:15. Same day, same result.
        `?dry_run=true` measures and plans without writing anything."""
        return await state.governor(apply=not dry_run)

    @app.get("/schedule")
    async def schedule_history(_: Approver, days: int = 14) -> dict[str, Any]:
        """Measured minutes per day against the budget (P4's ±15% check), and what the governor decided."""
        return governor.history(state.con, governor.Budget.from_yaml(state.budget()), days)

    # ---- the learner model: read-only, so the propose token (cloud-facing MCP) may read it ----

    @app.get("/learner/profile")
    async def learner_profile(_: Proposer, fresh: bool = False) -> dict[str, Any]:
        """What Aman knows and struggles with. Today's 04:15 snapshot unless `fresh`, which takes ~20 s."""
        if not fresh and (snap := learner.snapshot(state.con, settings.tz)) is not None:
            return snap
        if state.core is None:
            raise HTTPException(503, "anki-core isn't configured")
        return await state.learner_snapshot()

    @app.get("/learner/search")
    async def learner_search(q: str, _: Proposer, limit: int = 20) -> dict[str, Any]:
        """Cards on a topic (plain words or Anki search syntax), each with Aman's memory of it."""
        if state.core is None:
            raise HTTPException(503, "anki-core isn't configured")
        return await learner.search(state.core, q, max(1, min(limit, 50)))

    @app.get("/learner/neighbors")
    async def learner_neighbors(_: Proposer, q: str | None = None, card_id: int | None = None,
                                k: int = 10) -> dict[str, Any]:
        """Cards that mean the same as `q` (or as card `card_id`), each with Aman's memory of it. Off unless
        `embeddings.neighbors` is on in llm.yaml."""
        cfg = state.neighbors_config()
        if not cfg.enabled:
            raise HTTPException(404, "semantic neighbours are off (embeddings.neighbors in config/llm.yaml)")
        if state.core is None:
            raise HTTPException(503, "anki-core isn't configured")
        try:
            return await neighbors.search(state.con, state.core, cfg, settings.ollama_url, q, card_id,
                                          max(1, min(k, 50)))
        except (LookupError, ValueError) as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/neighbors/refresh")
    async def neighbors_refresh(_: Approver) -> dict[str, int]:
        """Rebuild the neighbours index now instead of at 04:15."""
        if state.core is None:
            raise HTTPException(503, "anki-core isn't configured")
        cfg = state.neighbors_config()
        return await neighbors.refresh(state.con, state.core, settings.ollama_url, cfg.model)

    @app.get("/learner/exams")
    async def learner_exams(_: Proposer, days: int = 60) -> list[dict[str, Any]]:
        if state.core is None:
            raise HTTPException(503, "anki-core isn't configured")
        return await learner.exam_status(state.core, state.con, settings.tz, state.today(), days)

    @app.get("/learner/stats")
    async def learner_stats(_: Proposer, days: int = 30) -> dict[str, Any]:
        return learner.stats(state.con, state.budget_minutes(), days)

    @app.get("/weekly")
    async def weekly_audit(_: Proposer, last: str = "", days: int = 7) -> dict[str, Any]:
        """The weekly audit's evidence (the mnemosyne-weekly skill): the `days` Anki days ending `last` (default
        yesterday). Read-only; Anki's part is left out when anki-core isn't configured."""
        today = state.today()
        try:
            end = date.fromisoformat(last) if last else today - timedelta(days=1)
        except ValueError:
            raise HTTPException(422, "last must be a date, YYYY-MM-DD") from None
        return await weekly.week(state.core, state.con, settings.tz, end, today, state.budget_minutes(),
                                max(1, min(days, 28)))

    # ---- the leech doctor ----

    @app.get("/leeches")
    async def leech_list(_: Approver, status: str = "proposed") -> list[dict[str, Any]]:
        return leech.cases(state.con, status)

    @app.post("/leeches/run")
    async def leech_run(_: Approver, dry_run: bool = False, limit: int = leech.PER_NIGHT) -> dict[str, Any]:
        """Diagnose now instead of at 04:15. `dry_run` lists the candidates without calling the LLM."""
        return await state.leech_doctor(max(1, min(limit, 20)), dry_run)

    @app.post("/leeches/{case_id}/decision")
    async def leech_decision(case_id: int, body: LeechDecision, _: Approver) -> dict[str, Any]:
        case = leech.get_case(state.con, case_id)
        if case is None:
            raise HTTPException(404, "no such case")
        if state.core is None:
            raise HTTPException(503, "anki-core isn't configured")
        try:
            async with state.write_lock:
                out = await leech.decide(state.con, state.core, state.schema,
                                         settings.repo / "data" / "anchors.yaml", case, body.verdict, body.by,
                                         body.note, body.fields, body.cards)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if out.get("write"):
            out["written"] = await state.write()
            if out["status"] == "applying":
                done = await state.leech_finish()
                out["status"] = "fixed" if done else "applying"
        log.info("leech_decided", case=case_id, verdict=body.verdict, status=out["status"])
        return out

    # ---- graded work: debriefs of returned exams and homework (graded.py) ----

    @app.get("/graded")
    async def graded_list(_: Approver) -> dict[str, Any]:
        return {"work": graded.listing(state.con), "profile": graded.profile(state.con, state.today())}

    @app.post("/graded/{doc_id}")
    async def graded_run(doc_id: int, _: Approver, background: BackgroundTasks, again: bool = False) -> dict[str, Any]:
        """Debrief a returned exam or homework now (the poller does it within five minutes anyway). `again` redoes
        one already debriefed, say after adding a note to it in Paperless."""
        pipeline = state.pipeline
        if pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        doc = await pipeline.paperless.document(doc_id)

        async def run() -> None:
            async with state.poll_lock:
                await run_graded(pipeline, doc, await tags(pipeline), again=again)
            state.sync_notes(doc_id)

        background.add_task(run)
        return {"queued": True, "doc_id": doc_id, "title": doc.title}

    @app.post("/graded/{doc_id}/written")
    async def graded_written(doc_id: int, body: dict[str, Any], _: Approver) -> dict[str, Any]:
        """A debrief written in a Claude session, in the format the model returns, for work the pipeline couldn't
        debrief (stuck.md). Checked like the model's; any problem comes back with nothing written."""
        pipeline = state.pipeline
        if pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        doc = await pipeline.paperless.document(doc_id)
        async with state.poll_lock:
            try:
                out = await debrief_written(pipeline, doc, await tags(pipeline), body)
            except Rejected as e:
                raise HTTPException(422, {"problems": e.problems}) from e
            except PipelineError as e:
                raise HTTPException(400, str(e)) from e
        state.sync_notes(doc_id)
        return {"doc_id": doc_id, "title": doc.title, "batch_id": out.batch_id, "created": out.created,
                "duplicates": out.duplicates, "mistakes": len(graded.mistakes_for(state.con, doc_id))}

    @app.get("/papers")
    async def papers_list(_: Proposer) -> dict[str, Any]:
        """Papers carded lately, each with what it assumes Aman knows and how he holds it (the tutor reads this)."""
        return {"papers": papers.recent(state.con)}

    @app.post("/papers/{doc_id}")
    async def paper_run(doc_id: int, _: Approver, background: BackgroundTasks, again: bool = False) -> dict[str, Any]:
        """Card a paper now (the poller does it within five minutes once it's type Paper and tagged mnemosyne).
        `again` redoes one already carded."""
        pipeline = state.pipeline
        if pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        doc = await pipeline.paperless.document(doc_id)

        async def run() -> None:
            async with state.poll_lock:
                await run_paper(pipeline, doc, await tags(pipeline), again=again)
            state.sync_notes(doc_id)

        background.add_task(run)
        return {"queued": True, "doc_id": doc_id, "title": doc.title}

    @app.post("/graded/{doc_id}/score")
    async def graded_score(doc_id: int, body: Score, _: Approver) -> dict[str, Any]:
        """The real score, when the scan doesn't show it all (pages missing, or Canvas gives the total)."""
        if body.possible <= 0 or not 0 <= body.earned <= body.possible:
            raise HTTPException(400, "earned must be between 0 and possible")
        if not graded.set_score(state.con, doc_id, body.earned, body.possible):
            raise HTTPException(404, "that document hasn't been debriefed")
        state.sync_notes(doc_id)
        return {"doc_id": doc_id, "earned": body.earned, "possible": body.possible}

    @app.post("/mistakes/{mistake_id}/decision")
    async def mistake_decision(mistake_id: int, body: MistakeDecision, _: Approver) -> dict[str, Any]:
        """Confirm a diagnosis, correct it ("wrong" with what really happened), or mark the mistake fixed."""
        row = state.con.execute("SELECT doc_id FROM mistake WHERE id = ?", (mistake_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such mistake")
        if body.action == "confirm":
            graded.set_status(state.con, mistake_id, confirmed=True, note=body.note or None)
        elif body.action == "wrong":
            if not body.note.strip():
                raise HTTPException(400, "say what actually went wrong")
            graded.set_status(state.con, mistake_id, confirmed=True, note=f"Aman: {body.note.strip()}")
        else:
            graded.set_status(state.con, mistake_id, "fixed" if body.action == "fixed" else "open")
        state.sync_notes(int(row["doc_id"]))
        return {"id": mistake_id, "action": body.action}

    @app.post("/mistakes/{mistake_id}/attempts")
    async def mistake_attempt(mistake_id: int, body: graded.Attempt, _: Proposer) -> dict[str, Any]:
        """A fresh problem he tried on a mistake's trap (the tutor logs it). Like proposing a card, it only adds:
        marking the mistake fixed stays his tick."""
        if graded.add_attempt(state.con, mistake_id, body, "mcp") is None:
            raise HTTPException(404, "no such mistake")
        row = state.con.execute("SELECT * FROM mistake WHERE id = ?", (mistake_id,)).fetchone()
        state.sync_notes(int(row["doc_id"]))
        return {"id": mistake_id, "attempts": graded.with_attempts(state.con, dict(row))["attempts"]}

    # ---- exam prep: the mnemosyne-exam skill's pack, documents, and probes (prep.py) ----

    @app.get("/exams/pack")
    async def exam_pack(course: str, _: Proposer, exam: str = "") -> dict[str, Any]:
        """Everything an exam's prep starts from. `exam`: "Exam II" or "2"; empty means the course's next exam."""
        if state.core is None:
            raise HTTPException(503, "anki-core isn't configured")
        found = prep.pick_exam(state.con, course, exam, state.today())
        if found is None:
            raise HTTPException(404, f"no {exam or 'upcoming'} exam for {course} in the calendar")
        rules = next((c.get("exam") for c in state.courses() if c["code"] == found["course"]), None)
        return await prep.pack(state.core, state.con, found, rules, state.today())

    @app.get("/documents/{doc_id}/read")
    async def document_read(doc_id: int, _: Proposer, pages: str = "", images: bool = True) -> dict[str, Any]:
        """A course document's pages (a lecture, prep material, graded work) as text, with page images by default:
        the handwriting is the point. Twelve pages a call; `more` says what's next. Only course documents."""
        pipeline = state.pipeline
        if pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        doc = await pipeline.paperless.document(doc_id)
        if not await state.course_document(doc):
            raise HTTPException(403, "only course documents (lectures, exams, assignments, syllabi) can be read here")
        try:
            first, last = prep.page_range(pages)
            pdf = await pipeline.paperless.download(doc_id)
            return await asyncio.to_thread(prep.read_pdf, pdf, doc_id, doc.title, first, last, images)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/probes")
    async def probe_add(body: prep.ProbeIn, _: Proposer) -> dict[str, Any]:
        """One step up from a card he holds, and whether he took it. Only adds, like an attempt."""
        return {"id": prep.add_probe(state.con, body, "mcp")}

    @app.post("/prep/{doc_id}")
    async def prep_run(doc_id: int, _: Approver, background: BackgroundTasks, again: bool = False) -> dict[str, Any]:
        """Attach a review sheet, practice exam, or key to its exam now (the poller does it within five minutes).
        `again` reads one already attached."""
        pipeline = state.pipeline
        if pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        doc = await pipeline.paperless.document(doc_id)
        if prep.kind_of(doc.title) is None:
            raise HTTPException(400, f"{doc.title!r} isn't titled as a review, practice exam, or key")

        async def run() -> None:
            async with state.poll_lock:
                await run_prep(pipeline, doc, await tags(pipeline), again=again)

        background.add_task(run)
        return {"queued": True, "doc_id": doc_id, "title": doc.title}

    # ---- Aman's questions ----

    @app.get("/questions")
    async def question_list(_: Approver, status: str = "open") -> dict[str, Any]:
        return {"questions": qstore.listing(state.con, status),
                "stats": qstore.stats(state.con, state.today() - timedelta(days=7))}

    @app.post("/questions")
    async def question_add(body: qstore.QuestionIn, _: Proposer) -> dict[str, Any]:
        """A question from a session (the tutor logs the ones Aman asks). Like proposing a card, it only adds."""
        return {"id": qstore.add(state.con, body)}

    @app.post("/questions/extract/{doc_id}")
    async def question_extract(doc_id: int, _: Approver) -> dict[str, Any]:
        """Read a filed lecture's handwritten questions and must-card marks, without carding it again."""
        if state.pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        try:
            return await questions_only(state.pipeline, doc_id)
        except PipelineError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/questions/{qid}/card")
    async def question_card(qid: int, _: Approver) -> dict[str, Any]:
        """Write the card a question's answer teaches (one that came without one). It waits on the question for
        Aman's Card it press; nothing reaches Anki from here."""
        q = qstore.get(state.con, qid)
        if q is None:
            raise HTTPException(404, "no such question")
        if state.pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        out = await write_question_card(state.pipeline, q)
        if not out["card"] and not out["no_card"]:
            raise HTTPException(502, "; ".join(out["problems"][:3]) or "no card came back")
        return {**out, "question": qstore.get(state.con, qid)}

    @app.post("/questions/cards")
    async def question_cards(_: Approver, background: BackgroundTasks, status: str = "open") -> dict[str, Any]:
        """Write the missing card for every question that has neither a card nor a reason it has none."""
        pipeline = state.pipeline
        if pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        waiting = qstore.without_cards(state.con, status)

        async def run() -> None:
            for q in waiting:
                try:
                    await write_question_card(pipeline, q)
                except Exception as e:  # noqa: BLE001 -- one question's failure doesn't stop the rest
                    log.warning("question_card_failed", question=q["id"], error=str(e)[:200])
            for doc_id in {int(q["doc_id"]) for q in waiting if q.get("doc_id")}:
                state.sync_notes(doc_id)

        background.add_task(run)
        return {"queued": len(waiting)}

    @app.post("/questions/{qid}/decision")
    async def question_decision(qid: int, body: qstore.QuestionDecision, _: Approver) -> dict[str, Any]:
        q = qstore.get(state.con, qid)
        if q is None:
            raise HTTPException(404, "no such question")
        out: dict[str, Any] = {"id": qid}
        if body.action == "card":
            mid, problems = qstore.card_it(state.con, state.schema, settings.repo / "data" / "anchors.yaml", q)
            if problems:
                raise HTTPException(400, "; ".join(problems))
            qstore.set_status(state.con, qid, "carded", body.note, mid)
            out["mnemosyne_id"] = mid
            out["written"] = await state.write()
        elif body.action == "hyperion":
            client = state.pipeline.hyperion if state.pipeline else None
            if client is None:
                raise HTTPException(503, "Hyperion isn't configured (MNEMOSYNE_HELIOS_MCP_URL)")
            out["hyperion_id"] = await ask_hyperion(state.con, client, q)
        else:
            qstore.set_status(state.con, qid, "open" if body.action == "reopen" else body.action, body.note)
        out["status"] = (qstore.get(state.con, qid) or {}).get("status")
        if q.get("doc_id"):
            state.sync_notes(int(q["doc_id"]))
        return out

    # ---- metrics and the weekly report ----

    @app.post("/metrics/run")
    async def metrics_run(_: Approver) -> dict[str, Any]:
        return await state.metrics()

    @app.post("/report/run")
    async def report_run(_: Approver, deliver: bool = False) -> dict[str, Any]:
        """Build this week's report now. With `deliver`, Telegram sends it within a minute."""
        return {"text": await state.weekly_report(deliver_now=deliver), "delivering": deliver}

    @app.get("/exams")
    async def exam_list(_: Approver) -> list[dict[str, Any]]:
        return [dict(r) for r in state.con.execute("SELECT * FROM exam ORDER BY starts_at")]

    @app.post("/pipeline/poll")
    async def poll_now(_: Approver) -> list[dict[str, Any]]:
        """Run one Paperless pass now instead of waiting for the next scheduled one."""
        return await state.poll()

    @app.post("/pipeline/document/{doc_id}")
    async def run_document(doc_id: int, _: Approver,
                           job: Literal["paperless", "graded", "prep", "paper"] | None = None) -> dict[str, Any]:
        """Process one Paperless document now (e.g. a backlog lecture, or a failed one retried from the review page:
        `job` is the job that failed), then write what was auto-approved."""
        if state.pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        async with state.poll_lock:
            outcome = await process_document(state.pipeline, doc_id, job)
        if outcome and outcome.approved:
            await state.write()
        if outcome:
            return asdict(outcome)
        left = next((s for s in stuck(state.con) if s["doc_id"] == doc_id), None)
        if left is not None:
            return {"doc_id": doc_id, "failed": left["problem"], "waiting_on_gateway": left["waiting_on_gateway"]}
        return {"doc_id": doc_id, "skipped": "already processed"}

    @app.post("/pipeline/document/{doc_id}/refile")
    async def refile_document(doc_id: int, body: RefileIn, _: Approver) -> dict[str, Any]:
        """A lecture filed under the wrong course moves to the right one, with its cards, questions, and fingerprints;
        a copy of the same deck already in that course is replaced by it."""
        if state.pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        async with state.poll_lock:
            try:
                return await refile(state.pipeline, doc_id, body.course.upper().replace(" ", ""), body.number)
            except PipelineError as e:
                raise HTTPException(400, str(e)) from e

    @app.get("/pipeline/stuck")
    async def pipeline_stuck(_: Proposer) -> dict[str, Any]:
        """Documents the pipeline hasn't finished, with their titles: what a session can card instead."""
        items = stuck(state.con)
        if state.pipeline is not None:
            items = await present(state.pipeline.paperless, items)
        return {"stuck": items, "how": "waiting_on_gateway: the TAMU allowance ran out; it retries every poll. "
                                       "A lecture can be carded in a session instead (mnemosyne-cards, stuck.md)."}

    @app.post("/pipeline/document/{doc_id}/settle")
    async def pipeline_settle(doc_id: int, _: Proposer) -> dict[str, Any]:
        """After a session carded a lecture: fingerprints its slides, marks its job done. Refused unless cards for it
        were proposed (source.link), so it can only finish a lecture, never skip one."""
        if state.pipeline is None:
            raise HTTPException(503, "pipeline not configured")
        try:
            async with state.poll_lock:
                return await settle(state.pipeline, doc_id)
        except PipelineError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/write")
    async def write_now(_: Approver) -> dict[str, int]:
        return await state.write()

    @app.get("/telegram/outbox")
    async def outbox(_: Approver) -> list[dict[str, Any]]:
        """Batches Telegram hasn't announced yet. The bot sends each, then POSTs /telegram/sent."""
        out: list[dict[str, Any]] = []
        for b in store.unnotified(state.con):
            drafts = store.batch_drafts(state.con, b.id)
            out.append({"batch_id": b.id, "text": telegram.announcement(b, drafts),
                        "keyboard": telegram.keyboard(b.id, drafts)})
        for m in exams.unnotified_maps(state.con):
            course = str(m["course"])
            out.append({"batch_id": f"map:{course}",
                        "text": exams.announcement(course, json.loads(m["detail_json"])),
                        "keyboard": [[{"text": "✅ Confirm", "callback_data": f"mn|c|{course}"},
                                      {"text": "✗ Wrong", "callback_data": f"mn|x|{course}"}]]})
        for r in report.due(state.con):
            out.append({"batch_id": f"report:{r['week']}", "text": r["text"], "keyboard": []})
        if nudge_due(state.con, state.today()):
            titles: list[str] = []
            for s in stuck(state.con):
                if not s["waiting_on_gateway"]:
                    continue
                try:
                    titles.append((await state.pipeline.paperless.document(int(s["doc_id"]))).title
                                  if state.pipeline is not None else f"document {s['doc_id']}")
                except Exception:  # noqa: BLE001 -- the nudge still helps without a title
                    titles.append(f"document {s['doc_id']}")
            out.append({"batch_id": nudge_key(state.today()), "text": nudge_text(titles), "keyboard": []})
        due = failures_due(state.con)
        if due and state.pipeline is not None:
            due = await present(state.pipeline.paperless, due)   # a copy gone from Paperless isn't mentioned
        for f in due:
            out.append({"batch_id": failure_key(int(f["doc_id"])),
                        "text": failure_text(f, f"{settings.public_url or ''}/review#waiting"), "keyboard": []})
        return out

    @app.post("/telegram/sent")
    async def sent(body: Sent, _: Approver) -> dict[str, bool]:
        if body.batch_id.startswith("map:"):
            exams.mark_map_notified(state.con, body.batch_id.removeprefix("map:"), body.message_ref)
        elif body.batch_id.startswith("report:"):
            report.mark_sent(state.con, body.batch_id.removeprefix("report:"), body.message_ref)
        elif body.batch_id.startswith(("stuck:", "failed:")):
            mark_notice(state.con, body.batch_id, body.message_ref)
        else:
            store.mark_notified(state.con, body.batch_id, body.message_ref)
        return {"ok": True}

    @app.post("/telegram/callback")
    async def callback(body: Callback, _: Approver, background: BackgroundTasks) -> dict[str, Any]:
        parsed = telegram.parse(body.data)
        if parsed is None:
            raise HTTPException(400, "not a mnemosyne callback")
        action, batch_id = parsed
        if action in ("c", "x"):
            ok = exams.decide_map(state.con, batch_id, action == "c")
            verb = "Confirmed" if action == "c" else "Marked wrong"
            return asdict(telegram.Reply(toast=f"{verb}" if ok else "Already decided",
                                         text=f"{verb}: exam map for {batch_id}" if ok else None))
        found = store.get_batch(state.con, batch_id)
        if found is None:
            return asdict(telegram.Reply(toast="That batch no longer exists"))
        drafts = store.batch_drafts(state.con, batch_id)
        if action == "s":
            return asdict(telegram.Reply(toast="Sending every card", messages=telegram.full_listing(drafts)))
        approve = action == "a"
        if not store.decide(state.con, batch_id, approve, body.by):
            return asdict(telegram.Reply(toast=f"Already {found.status}"))
        if approve:
            background.add_task(state.write)
        verb = "Approved" if approve else "Rejected"
        done = store.get_batch(state.con, batch_id) or found
        return asdict(telegram.Reply(toast=f"{verb} {len(drafts)} cards",
                                     text=f"{verb}: {telegram.title(done)}", write=approve))

    return app

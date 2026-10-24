"""The weekly audit's evidence (the mnemosyne-weekly skill, PLAN §9): one week of cards, what Aman did with them, how
the new ones are holding, and where the gaps are. Read-only: the audit itself runs in a Claude session, on Aman's
own account, and changes nothing without his OK.

Each section answers one question the audit asks:
- intake: what was carded, from where, and how much of it needed him;
- said: his edits and rejections, in his words, with the card as written (the canon's misses);
- holding: how the cards first studied this week are doing, by course and by note type;
- leeches: what the leech doctor diagnosed, and which of those cards the pipeline wrote;
- skipped: what the pipeline chose not to card, and why;
- questions, mistakes, probes: where his understanding caught, and whether cards closed it;
- coverage and health: exams coming with lectures uncarded, jobs that failed or are waiting;
- load: minutes against the budget, and upkeep (minutes he spent curating).
"""

import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mnemosyne import clips
from mnemosyne import questions as qstore
from mnemosyne.anki.core_client import Core, reads
from mnemosyne.learner import count, exam_status, ids, plain, views
from mnemosyne.learner import probes as probe_rows
from mnemosyne.project.metrics import anki_day_bounds, upkeep
from mnemosyne.project.report import week_rows

NOTE_TYPES = ("M-Basic", "M-Cloze", "M-Estimate", "M-Error", "M-Lang")
SHOW = 25   # examples per list: enough to see a pattern, few enough to read


def bounds(last: date, tz: str, days: int = 7) -> tuple[date, float, float, str, str]:
    """The audit's window: the `days` Anki days ending with `last`, as epoch seconds and as Ledger timestamps."""
    first = last - timedelta(days=days - 1)
    start, end = anki_day_bounds(first, tz)[0], anki_day_bounds(last, tz)[1]
    lo, hi = (datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds") for t in (start, end))
    return first, start, end, lo, hi


def first_field(fields_json: str, limit: int = 200) -> str:
    fields: dict[str, str] = json.loads(fields_json)
    return plain(next((v for k, v in fields.items() if v and not k.startswith("Speak")), ""), limit)


def reason_kind(reason: str | None) -> str:
    """'dedupe_flagged (0.90): …' → 'dedupe_flagged'; every held card's reason starts with its kind."""
    found = re.match(r"[A-Za-z_-]+", (reason or "").strip())
    return found.group(0) if found else "none"


def diff(before: dict[str, str], after: dict[str, str]) -> dict[str, dict[str, str]]:
    return {k: {"was": plain(before.get(k, ""), 200), "now": plain(after.get(k, ""), 200)}
            for k in sorted(set(before) | set(after))
            if not k.startswith("Speak") and before.get(k, "") != after.get(k, "")}


def intake(con: sqlite3.Connection, lo: str, hi: str) -> dict[str, Any]:
    rows = con.execute(
        "SELECT d.status, d.decided_by, d.review_reason, d.note_type, d.before_json, s.origin, s.kind, s.course,"
        " s.lecture, s.ref FROM card_draft d JOIN card_batch b ON b.id = d.batch_id JOIN card_source s"
        " ON s.id = b.source_id WHERE d.created_at >= ? AND d.created_at < ?", (lo, hi)).fetchall()
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        key = (str(r["course"] or "-"), str(r["kind"]), str(r["ref"] or r["origin"]))
        g = groups.setdefault(key, {"course": key[0], "kind": key[1], "ref": key[2], "origin": r["origin"],
                                    "cards": 0, "auto_approved": 0, "held": 0, "edited": 0, "rejected": 0,
                                    "waiting": 0})
        g["cards"] += 1
        by_him = r["decided_by"] in ("review-page", "telegram")
        held = bool(r["review_reason"])
        g["held"] += held
        g["auto_approved"] += not held and not by_him and r["status"] in ("approved", "written")
        g["edited"] += r["before_json"] is not None
        g["rejected"] += r["status"] == "rejected"
        g["waiting"] += r["status"] == "pending"
    return {"cards": len(rows),
            "by_status": dict(Counter(str(r["status"]) for r in rows)),
            "by_note_type": dict(Counter(str(r["note_type"]) for r in rows)),
            "held_because": dict(Counter(reason_kind(r["review_reason"]) for r in rows if r["review_reason"])),
            "sources": sorted(groups.values(), key=lambda g: (g["course"], g["ref"]))}


def said(con: sqlite3.Connection, lo: str, hi: str) -> dict[str, Any]:
    """His decisions this week with a reason or an edit: the most direct evidence of what the canon gets wrong."""
    edits: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    untouched = 0
    # decided_at exists since 2026-10-07; before that, updated_at is the nearest record of the decision
    for r in con.execute(
            "SELECT d.status, d.decided_by, d.decision_note, d.before_json, d.fields_json, d.tags_json, s.course,"
            " s.kind FROM card_draft d JOIN card_batch b ON b.id = d.batch_id JOIN card_source s"
            " ON s.id = b.source_id WHERE COALESCE(d.decided_at, d.updated_at) >= ?"
            " AND COALESCE(d.decided_at, d.updated_at) < ? AND d.decided_by IS NOT NULL", (lo, hi)):
        base = {"by": r["decided_by"], "note": r["decision_note"] or "", "course": r["course"], "kind": r["kind"]}
        if r["before_json"]:
            edits.append({**base, "changed": diff(json.loads(r["before_json"]), json.loads(r["fields_json"]))})
        elif r["status"] == "rejected":
            rejected.append({**base, "card": first_field(r["fields_json"]),
                             "tags": [t for t in json.loads(r["tags_json"]) if t.startswith(("type::", "topic::"))]})
        elif r["decided_by"] in ("review-page", "telegram"):
            untouched += 1
    sets: list[dict[str, Any]] = []
    for r in con.execute("SELECT verdict, reason, card_json, edits_json FROM review_item WHERE decided_at >= ?"
                         " AND decided_at < ? AND verdict IN ('edit', 'reject')", (lo, hi)):
        card: dict[str, Any] = json.loads(r["card_json"])
        fields: dict[str, str] = card.get("fields") or {}
        edited: dict[str, Any] = json.loads(r["edits_json"]) if r["edits_json"] else {}
        new_fields: dict[str, str] = edited.get("fields") or {}
        sets.append({"verdict": r["verdict"], "reason": r["reason"] or "",
                     "card": plain(next(iter(fields.values()), ""), 200),
                     **({"changed": diff(fields, {**fields, **new_fields})} if new_fields else {})})
    by_him = [x for x in rejected if x["by"] in ("review-page", "telegram")]
    return {"edits": edits[:SHOW], "edits_total": len(edits),
            "rejected_by_aman": by_him[:SHOW], "rejected_by_aman_total": len(by_him),
            "rejected_by_claude": len(rejected) - len(by_him), "approved_untouched": untouched,
            "review_sets": sets[:SHOW], "review_sets_total": len(sets)}


async def holding(core: Core, days: int, courses: list[str]) -> dict[str, Any]:
    """Cards first studied in the window: how many, how many he's already failed at least once, and the worst."""
    window = f"introduced:{days}"
    async with reads(core) as c:
        def pair(q: str) -> tuple[str, str]:
            return f"{window} {q}".strip(), f"{window} rated:{days}:1 {q}".strip()

        async def tally(q: str) -> dict[str, Any]:
            seen_q, again_q = pair(q)
            seen = await count(c, seen_q)
            again = await count(c, again_q) if seen else 0
            return {"introduced": seen, "failed_once": again, "fail_share": round(again / seen, 2) if seen else None}

        out: dict[str, Any] = {"all": await tally(""), "by_course": {}, "by_note_type": {}, "ai_written": {}}
        for course in courses:
            t = await tally(f"tag:course::{course.lower()}::*")
            if t["introduced"]:
                out["by_course"][course] = t
        for nt in NOTE_TYPES:
            t = await tally(f'"note:{nt}"')
            if t["introduced"]:
                out["by_note_type"][nt] = t
        out["ai_written"] = {"pipeline": await tally("tag:gen::ai"), "other": await tally("-tag:gen::ai")}
        worst, total = await ids(c, f"{window} prop:lapses>=1 -is:suspended", SHOW)
        out["lapsed"] = {"total": total, "cards": sorted(await views(c, worst), key=lambda v: -v["lapses"])}
    return out


def leeches(con: sqlite3.Connection, lo: str, hi: str) -> dict[str, Any]:
    rows = con.execute("SELECT * FROM leech_case WHERE found_at >= ? AND found_at < ? ORDER BY found_at",
                       (lo, hi)).fetchall()
    written = {str(r[0]) for r in con.execute("SELECT mnemosyne_id FROM card_draft WHERE note_id IS NOT NULL")}
    cases: list[dict[str, Any]] = [
        {"card": first_field(r["fields_json"]), "deck": r["deck"], "lapses": r["lapses"], "diagnosis": r["diagnosis"],
         "explanation": plain(r["explanation"] or "", 240), "action": r["action"], "status": r["status"],
         "pipeline_card": r["mnemosyne_id"] in written} for r in rows]
    return {"found": len(cases), "by_diagnosis": dict(Counter(str(c["diagnosis"]) for c in cases)),
            "pipeline_cards": sum(1 for c in cases if c["pipeline_card"]), "cases": cases[:SHOW]}


def skipped(con: sqlite3.Connection, lo: str, hi: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in con.execute("SELECT b.skipped_json, s.course, s.ref FROM card_batch b JOIN card_source s"
                         " ON s.id = b.source_id WHERE b.created_at >= ? AND b.created_at < ?"
                         " AND b.skipped_json IS NOT NULL", (lo, hi)):
        items: list[dict[str, str]] = json.loads(r["skipped_json"])
        out += [{"course": r["course"], "ref": r["ref"], **i} for i in items]
    return out


def asked(con: sqlite3.Connection, first: date, lo: str, hi: str) -> dict[str, Any]:
    rows = [qstore.row(r) for r in con.execute(
        "SELECT * FROM question WHERE created_at >= ? AND created_at < ? ORDER BY created_at", (lo, hi))]
    return {**qstore.stats(con, first),
            "status": dict(Counter(str(q["status"]) for q in rows)),
            "no_card": [{"asked": q["asked"], "why": q["no_card"]} for q in rows if q.get("no_card")][:SHOW],
            "open": [{"course": q["course"], "lecture": q["lecture"], "asked": q["asked"], "kind": q["kind"]}
                     for q in rows if q["status"] == "open"][:SHOW]}


def mistakes(con: sqlite3.Connection, lo: str, hi: str) -> dict[str, Any]:
    found = [{k: r[k] for k in ("course", "question", "kind", "concept", "lecture", "status", "confirmed")}
             for r in con.execute("SELECT * FROM mistake WHERE created_at >= ? AND created_at < ?", (lo, hi))]
    tries = con.execute("SELECT a.right, a.hints, m.concept, m.course FROM mistake_attempt a JOIN mistake m"
                        " ON m.id = a.mistake_id WHERE a.created_at >= ? AND a.created_at < ?", (lo, hi)).fetchall()
    never = [{"course": r["course"], "concept": r["concept"], "kind": r["kind"]} for r in con.execute(
        "SELECT m.course, m.concept, m.kind FROM mistake m WHERE m.status = 'open' AND m.kind != 'careless'"
        " AND NOT EXISTS (SELECT 1 FROM mistake_attempt a WHERE a.mistake_id = m.id)")]
    return {"new": found, "attempts": len(tries), "clean": sum(1 for t in tries if t["right"] and not t["hints"]),
            "open_never_tried": never[:SHOW], "open_never_tried_total": len(never)}


def short_error(error: str | None) -> str | None:
    """An error line without the gateway's HTML error page (Cloudflare's 524 comes back as a whole document)."""
    if not error:
        return None
    head, html_at = error, error.lower().find("<!doctype")
    if html_at < 0:
        html_at = error.lower().find("<html")
    if html_at >= 0:
        head = error[:html_at].rstrip(": ") + " (an HTML error page)"
    return head[:240]


def health(con: sqlite3.Connection, lo: str) -> list[dict[str, Any]]:
    return [{**{k: r[k] for k in ("kind", "ref", "status", "attempts", "updated_at")}, "error": short_error(r["error"])}
            for r in con.execute("SELECT * FROM ingest_job WHERE updated_at >= ? AND status != 'done'"
                                 " ORDER BY updated_at DESC", (lo,))][:SHOW]


async def week(core: Core | None, con: sqlite3.Connection, tz: str, last: date, today: date,
               budget_minutes: float, days: int = 7) -> dict[str, Any]:
    first, start, end, lo, hi = bounds(last, tz, days)
    load, metrics = week_rows(con, first, last)
    minutes = [float(r["minutes"]) for r in load]
    courses = [str(r[0]) for r in con.execute("SELECT code FROM course WHERE active = 1 ORDER BY code")]
    out: dict[str, Any] = {
        "window": {"first": first.isoformat(), "last": last.isoformat(), "days": days},
        "how_to_read": "Anki days run 04:00 to 04:00 Chicago. 'held' cards waited for Aman; 'auto_approved' went "
                       "straight in. fail_share = share of cards first studied this window that he has rated Again "
                       "at least once. A leech's pipeline_card means the pipeline wrote it. upkeep = his minutes "
                       "deciding cards, maps, and leeches. clips = opens of animated clips on /watch, by where "
                       "they were opened from (a card's link, the page, a session).",
        "load": {"days_studied": sum(m > 0 for m in minutes), "minutes": round(sum(minutes)),
                 "budget_minutes_a_day": budget_minutes, "reviews": sum(int(r["reviews"]) for r in load),
                 "days_within_15pct": sum(abs(m - budget_minutes) <= 0.15 * budget_minutes for m in minutes),
                 "upkeep": upkeep(con, start, end),
                 "new_let_in": sum(int(m.get("new_governed") or 0) for m in metrics),
                 "retention_30d": (metrics[-1] if metrics else {}).get("retention"),
                 "retention_by_deck": (metrics[-1] if metrics else {}).get("retention_by_deck")},
        "intake": intake(con, lo, hi),
        "said": said(con, lo, hi),
        "leeches": leeches(con, lo, hi),
        "skipped": skipped(con, lo, hi),
        "questions": asked(con, first, lo, hi),
        "mistakes": mistakes(con, lo, hi),
        "probes": probe_rows(con, None, lo)[:SHOW],
        "jobs_not_done": health(con, lo),
        "clips": clips.views(con, lo, hi),
    }
    if core is not None:
        out["holding"] = await holding(core, days + (today - last).days, courses)
        exams = await exam_status(core, con, tz, today, days=21, weakest=3)
        out["exams"] = [{k: e.get(k) for k in ("course", "title", "date", "mapped", "readiness", "unseen",
                                               "lectures_without_cards", "weakest")} for e in exams]
    return out


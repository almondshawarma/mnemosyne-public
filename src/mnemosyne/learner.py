"""The learner model: what Aman knows, where he struggles, and how ready he is for each exam.

Claude reads it through the read-only MCP tools (mnemosyne_learner_profile, _search_cards, _exam_status,
_card_stats) and the mnemosyne-tutor skill turns it into tutoring. Everything here only reads: the collection
through anki-core, the rest from the Ledger. The full profile is rebuilt at 04:15 and kept as a snapshot, because
it takes a hundred or so reads; `fresh=True` rebuilds it on demand.
"""

import html
import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from mnemosyne import graded
from mnemosyne.anki.core_client import Caller, Core, reads
from mnemosyne.ledger import store
from mnemosyne.ledger.db import now
from mnemosyne.schedule import exam_decks
from mnemosyne.web.review import coverage

DECKS = ("Now", "Core", "Lang", "Reservoir")
STRUGGLING = "-is:suspended -is:new (tag:marked OR tag:leech OR prop:lapses>=3 OR prop:d>0.83)"
RECENT_MISSES = "-is:suspended rated:7:1"
PAIRS = (("Front", "Back"), ("Question", "Answer"), ("Situation", "Fix"), ("Text", "Extra"), ("Target", "Meaning"),
         ("Symbol", "Description"))
STATE = {0: "new", 1: "learning", 2: "review", 3: "relearning"}
QUIET = frozenset({"MnemosyneID", "Source", "SourceLink", "Reverse", "Produce", "Read", "Lang"})
ANKI_SYNTAX = re.compile(r"\b(?:tag|deck|note|prop|is|rated|card|nid|cid|added|front|back):", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def plain(value: str, limit: int = 240) -> str:
    """Field HTML as readable text; MathJax stays as TeX, which Claude reads fine."""
    value = re.sub(r"<img[^>]*>", " [image] ", value, flags=re.IGNORECASE)
    value = re.sub(r"\[sound:[^\]]*\.(?:mp4|webm|mov|mkv)\]", " [video] ", value, flags=re.IGNORECASE)
    value = re.sub(r"\[sound:[^\]]*\]", " [audio] ", value)
    out = html.unescape(TAG_RE.sub(" ", value.replace("<br>", " ").replace("<br/>", " ")))
    out = re.sub(r"\s+", " ", out).strip()
    return out if len(out) <= limit else out[:limit - 1] + "…"


def card_view(card: dict[str, Any]) -> dict[str, Any]:
    """One card as a tutor needs it: what it asks, what it answers, and how well Aman holds it."""
    fields = {str(f["name"]): str(f["value"]) for f in card.get("fields", [])}
    prompt_key, answer_key = next(((p, a) for p, a in PAIRS if p in fields),
                                  tuple((list(fields) + ["", ""])[:2]))
    sched: dict[str, Any] = card.get("scheduling") or {}
    fsrs: dict[str, Any] = card.get("fsrs") or {}
    memory: dict[str, Any] = fsrs.get("memory_state") or {}
    summary: dict[str, Any] = card.get("review_summary") or {}
    queue = int(sched.get("queue", 0))
    r = fsrs.get("retrievability")
    last = summary.get("latest_review_at")
    prompt, answer = plain(fields.get(prompt_key, "")), plain(fields.get(answer_key, ""))
    if not prompt:   # audio-first or image cards: the first field that says something
        rest = [plain(v) for k, v in fields.items() if k not in QUIET and plain(v)]
        prompt, answer = (rest + ["", ""])[0], answer or (rest + ["", ""])[1]
    return {
        "card_id": card.get("id"), "note_id": card.get("note_id"), "prompt": prompt, "answer": answer,
        "deck": summary.get("original_deck") or card.get("deck_name"),
        "state": "suspended" if queue == -1 else STATE.get(int(sched.get("type", 0)), "unknown"),
        "retrievability": round(float(r), 2) if r is not None else None,
        "stability_days": round(float(memory["stability"]), 1) if memory.get("stability") is not None else None,
        "difficulty": round(float(memory["difficulty"]), 1) if memory.get("difficulty") is not None else None,
        "lapses": int(sched.get("lapses", 0)), "reviews": int(summary.get("reviews", sched.get("reps", 0)) or 0),
        "last_review": datetime.fromtimestamp(int(last), UTC).date().isoformat() if last else None,
    }


async def count(c: Caller, query: str) -> int:
    return int((await c.call("anki_cards_search", {"query": query, "offset": 0, "limit": 1})).get("total", 0))


async def ids(c: Caller, query: str, limit: int) -> tuple[list[int], int]:
    page = await c.call("anki_cards_search", {"query": query, "offset": 0, "limit": max(1, min(limit, 100))})
    items: list[dict[str, Any]] = page.get("items") or []
    return [int(i["id"]) for i in items], int(page.get("total", 0))


async def views(c: Caller, card_ids: list[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cid in card_ids:
        card = await c.call("anki_cards_get", {"card_id": cid, "include_sections": ["review_summary", "fsrs"]})
        out.append(card_view(card))
    return out


def anki_query(text: str) -> str:
    """Anki search syntax passes through; plain words become a search of every field (Anki matches substrings)."""
    text = text.strip()
    if ANKI_SYNTAX.search(text):
        return text
    words = [w.replace('"', "") for w in text.split() if w]
    return " ".join(f'"{w}"' if not w.startswith("-") else w for w in words)


async def search(core: Core, text: str, limit: int = 20) -> dict[str, Any]:
    """Cards matching a topic, with Aman's memory of each. What a tutor checks before explaining anything."""
    query = anki_query(text)
    async with reads(core) as c:
        found, total = await ids(c, query, limit)
        cards = await views(c, found)
    seen = [v for v in cards if v["state"] not in ("new", "suspended")]
    rs = [v["retrievability"] for v in seen if v["retrievability"] is not None]
    return {"query": query, "total": total, "shown": len(cards), "cards": cards,
            "summary": {"new": sum(v["state"] == "new" for v in cards), "seen": len(seen),
                        "mean_retrievability": round(sum(rs) / len(rs), 2) if rs else None,
                        "weak": sum(1 for v in seen if (v["retrievability"] or 1) < 0.8 or v["lapses"] >= 3)}}


def lecture_query(course: str, lectures: list[int]) -> str:
    tags = " OR ".join(f"tag:course::{course.lower()}::l{n:02d}*" for n in lectures)
    return f"({tags}) -is:suspended"


async def exam_status(core: Core, con: sqlite3.Connection, tz: str, today: date, days: int = 60,
                      weakest: int = 8) -> list[dict[str, Any]]:
    """Every exam in the next `days`: its map, coverage, readiness (share of its cards held at R ≥ 0.9), and the
    cards most likely to be missed."""
    cov = coverage(store.approved_tags(con))
    out: list[dict[str, Any]] = []
    async with reads(core) as c:
        decks = await exam_decks.all_decks(c)
        for e in exam_decks.ledger_exams(con):
            if e.get("kind") != "exam":
                continue
            day = exam_decks.exam_day(str(e["starts_at"]), tz)
            left = (day - today).days
            if left < 0 or left > days:
                continue
            raw = e.get("lectures")
            lectures = sorted(json.loads(raw) if isinstance(raw, str) else (raw or []))
            item: dict[str, Any] = {"course": e["course"], "title": e["title"], "date": day.isoformat(),
                                    "days_left": left, "lectures": lectures,
                                    "mapped": bool(e.get("confirmed")) and bool(lectures),
                                    "deck": exam_decks.deck_name(str(e["course"]), str(e["title"]))}
            item["deck_built"] = item["deck"] in decks
            if not item["mapped"]:
                out.append(item)
                continue
            per = cov.get(str(e["course"]).upper(), {})
            item["cards_per_lecture"] = {f"L{n:02d}": per.get(str(n), 0) for n in lectures}
            item["lectures_without_cards"] = [f"L{n:02d}" for n in lectures if not per.get(str(n))]
            base = lecture_query(str(e["course"]), lectures)
            total, new = await count(c, base), await count(c, f"{base} is:new")
            ready, weak = await count(c, f"{base} -is:new prop:r>=0.9"), await count(c, f"{base} -is:new prop:r<0.8")
            found, _ = await ids(c, f"{base} -is:new prop:r<0.8", weakest)
            item.update(cards=total, unseen=new, ready=ready, weak=weak,
                        readiness=round(ready / total, 2) if total else None,
                        weakest=sorted(await views(c, found), key=lambda v: v["retrievability"] or 0))
            out.append(item)
    return sorted(out, key=lambda x: x["date"])


def load_summary(con: sqlite3.Connection, budget_minutes: float, days: int = 14) -> dict[str, Any]:
    rows = [dict(r) for r in con.execute("SELECT day, reviews, minutes FROM load_day ORDER BY day DESC LIMIT ?",
                                         (days,))]
    if not rows:
        return {"days": 0, "budget_minutes": budget_minutes}
    minutes = [float(r["minutes"]) for r in rows]
    return {"days": len(rows), "budget_minutes": budget_minutes,
            "mean_minutes": round(sum(minutes) / len(minutes), 1),
            "mean_reviews": round(sum(int(r["reviews"]) for r in rows) / len(rows), 1),
            "days_within_15pct": sum(abs(m - budget_minutes) <= 0.15 * budget_minutes for m in minutes),
            "recent": rows[:7]}


def preferences(con: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Aman's recent reasons for rejecting or editing cards: his taste, in his words."""
    out: list[dict[str, Any]] = []
    for r in con.execute(
            "SELECT decision_note, status, fields_json, updated_at FROM card_draft "
            "WHERE decision_note IS NOT NULL AND decision_note != '' AND decided_by = 'review-page' "
            "ORDER BY updated_at DESC LIMIT ?", (limit,)):
        fields = json.loads(r["fields_json"])
        out.append({"said": r["decision_note"], "verdict": "rejected" if r["status"] == "rejected" else "kept",
                    "card": plain(next(iter(fields.values()), ""), 120), "at": r["updated_at"][:10]})
    for r in con.execute(
            "SELECT reason, verdict, card_json, decided_at FROM review_item "
            "WHERE reason IS NOT NULL AND reason != '' ORDER BY decided_at DESC LIMIT ?", (limit,)):
        card: dict[str, Any] = json.loads(r["card_json"])
        fields: dict[str, str] = card.get("fields") or {}
        out.append({"said": r["reason"], "verdict": r["verdict"], "card": plain(next(iter(fields.values()), ""), 120),
                    "at": (r["decided_at"] or "")[:10]})
    return sorted(out, key=lambda x: x["at"], reverse=True)[:limit]


async def profile(core: Core, con: sqlite3.Connection, tz: str, budget_minutes: float) -> dict[str, Any]:
    """The whole picture, compact enough to read at the start of a study session."""
    today = datetime.now(ZoneInfo(tz)).date()
    cov = coverage(store.approved_tags(con))
    async with reads(core) as c:
        collection: dict[str, dict[str, int]] = {}
        for d in DECKS:
            q = f'"deck:{d}" -is:suspended'
            collection[d] = {"cards": await count(c, q), "unseen": await count(c, f"{q} is:new"),
                             "due_today": await count(c, f"{q} is:due"), "mature": await count(c, f"{q} prop:ivl>=21")}
        stats = await c.call("anki_review_stats", {"query": "rated:30", "days": 30})
        summary: dict[str, Any] = stats.get("summary") or {}
        courses: dict[str, dict[str, Any]] = {}
        for course, per in sorted(cov.items()):
            q = f"tag:course::{course.lower()}::* -is:suspended"
            seen = await count(c, f"{q} -is:new")
            courses[course] = {"lectures_carded": sorted(int(n) for n in per), "cards": await count(c, q),
                               "seen": seen, "strong": await count(c, f"{q} -is:new prop:r>=0.9"),
                               "weak": await count(c, f"{q} -is:new prop:r<0.8")}
        leech_ids, leech_total = await ids(c, STRUGGLING, 12)
        miss_ids, miss_total = await ids(c, RECENT_MISSES, 12)
        struggling = sorted(await views(c, leech_ids), key=lambda v: (-v["lapses"], v["retrievability"] or 0))
        misses = await views(c, miss_ids)
    exams = await exam_status(core, con, tz, today, days=45, weakest=5)
    open_leeches = con.execute("SELECT COUNT(*) FROM leech_case WHERE status = 'proposed'").fetchone()[0]
    return {
        "as_of": now(), "today": today.isoformat(),
        "how_to_read": "retrievability = predicted chance Aman recalls the card right now (FSRS); stability_days = "
                       "how long until that drops to 90%; mature = interval ≥ 21 days; weak = R < 0.8 or 3+ lapses.",
        "collection": collection,
        "retention_30d": {"reviews": summary.get("reviews"), "retention": summary.get("retention"),
                          "ratings": summary.get("ratings")},
        "load_14d": load_summary(con, budget_minutes),
        "courses": courses,
        "struggling": {"total": leech_total, "cards": struggling, "leech_doctor_waiting": int(open_leeches)},
        "missed_this_week": {"total": miss_total, "cards": misses},
        "exams": exams,
        "preferences": preferences(con),
        "open_questions": [{k: q[k] for k in ("id", "course", "lecture", "asked", "sharper", "kind", "answer")}
                           for q in con_questions(con)],
        "graded": graded.profile(con, today),
        "probes": probe_summary(con, today),
        "papers": recent_papers(con),
    }


# Probes (prep.py writes them): one step up from a card he holds, and whether he took it.

def probes(con: sqlite3.Connection, course: str | None, since: str) -> list[dict[str, Any]]:
    q = "SELECT * FROM probe WHERE created_at >= ?" + (" AND course = ?" if course else "") + " ORDER BY created_at DESC"
    rows = con.execute(q, (since, course) if course else (since,))
    return [{**{k: r[k] for k in ("course", "lecture", "topic", "hook", "note_id", "question", "note")},
             "right": bool(r["right"]), "hints": bool(r["hints"]), "at": str(r["created_at"])[:10]} for r in rows]


def probe_summary(con: sqlite3.Connection, today: date, days: int = 30) -> dict[str, Any]:
    """For the learner profile: cards he recalls but couldn't use, lately."""
    found = probes(con, None, date.fromordinal(today.toordinal() - days).isoformat())
    clean = [p for p in found if p["right"] and not p["hints"]]
    return {"tried": len(found), "clean": len(clean),
            "missed": [p for p in found if not p["right"]][:12],
            "how_to_read": "a probe is one step up from a card he holds; a miss is a card he can recall but not use"}


def recent_papers(con: sqlite3.Connection, limit: int = 5) -> list[dict[str, Any]]:
    """Papers he's reading (papers.py), each with what it assumes and whether he holds it: where a tutor starts."""
    out: list[dict[str, Any]] = []
    for r in con.execute("SELECT title, slug, year, prerequisites FROM paper ORDER BY updated_at DESC LIMIT ?", (limit,)):
        pre: list[dict[str, Any]] = json.loads(r["prerequisites"] or "[]")
        out.append({"title": r["title"], "tag": f"src::paper::{r['slug']}", "year": r["year"],
                    "prerequisites": [{"concept": p.get("concept"), "cards": p.get("cards"), "weak": p.get("weak"),
                                       "book": p.get("book")} for p in pre]})
    return out


def con_questions(con: sqlite3.Connection, limit: int = 15) -> list[dict[str, Any]]:
    """Aman's open questions, newest first: what a tutor should offer to work through."""
    rows = con.execute("SELECT id, course, lecture, asked, sharper, kind, answer FROM question WHERE status = 'open'"
                       " ORDER BY created_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def save_snapshot(con: sqlite3.Connection, prof: dict[str, Any]) -> None:
    con.execute("INSERT INTO learner_snapshot (id, profile_json, created_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET profile_json = excluded.profile_json, created_at = excluded.created_at",
                (json.dumps(prof, ensure_ascii=False), now()))


def snapshot(con: sqlite3.Connection, tz: str) -> dict[str, Any] | None:
    """Today's snapshot (built after the 04:00 rollover), or None when it's stale or missing."""
    row = con.execute("SELECT profile_json, created_at FROM learner_snapshot WHERE id = 1").fetchone()
    if row is None:
        return None
    built = datetime.fromisoformat(row["created_at"]).astimezone(ZoneInfo(tz))
    rollover = datetime.now(ZoneInfo(tz)).replace(hour=4, minute=0, second=0, microsecond=0)
    if datetime.now(ZoneInfo(tz)) < rollover:
        rollover -= timedelta(days=1)
    return json.loads(row["profile_json"]) if built >= rollover else None


def stats(con: sqlite3.Connection, budget_minutes: float, days: int = 30) -> dict[str, Any]:
    """Ledger-only numbers: load against budget, daily metrics, the governor's decisions, card flow."""
    metrics = [{"day": r["day"], **json.loads(r["metrics_json"])} for r in
               con.execute("SELECT day, metrics_json FROM metric_day ORDER BY day DESC LIMIT ?", (days,))]
    flow = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) AS n FROM card_draft GROUP BY status")}
    leeches = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) AS n FROM leech_case GROUP BY status")}
    return {"load": load_summary(con, budget_minutes, days), "metrics": metrics, "cards": flow, "leeches": leeches}

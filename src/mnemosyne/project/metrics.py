"""Daily metrics (PLAN §9), taken at 04:15 for the Anki day that just ended.

They're kept in the Ledger (metric_day), which the weekly report and the MCP card_stats tool read. Once Aman has
created secrets/influx_token (scripts/obelisk_secrets.sh), each day is also written to InfluxDB's `mnemosyne` bucket
for the Grafana dashboard in ops/grafana/.

- load: reviews and minutes (from the governor's measurement)
- retention: true retention over 30 days, overall and per deck
- fluency index: the share of yesterday's Core answers that were right in under 3 s
- backlog: due today and overdue
- exam readiness: for exams inside their window, the share of their cards held at R ≥ 0.9
- leeches waiting, and the approval rate by source over 7 days
- upkeep: minutes Aman spent deciding things (cards, batches, review sets, exam maps, leeches), from the moments he
  decided them: decisions less than 10 minutes apart are one sitting, and each sitting counts a minute more than its
  span. It's what the system costs him, set against what it's for (the review minutes).
"""

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog

from mnemosyne.anki.core_client import AnkiCoreError, Caller, Core, reads
from mnemosyne.learner import count, lecture_query
from mnemosyne.ledger.db import now
from mnemosyne.schedule import exam_decks

log = structlog.get_logger()
DECKS = ("Core", "Now", "Lang", "Reservoir")
FLUENT_SECONDS = 3.0
ROLLOVER = time(4, 0)
SITTING_GAP = timedelta(minutes=10)
# Every moment Aman decided something. A batch decided as it arrived was the approval policy, not him, and Claude's
# own calls (rejecting a clear duplicate) aren't his time.
DECISIONS = (
    "SELECT decided_at FROM card_draft WHERE decided_at >= ? AND decided_at < ? AND decided_by IS NOT 'claude'",
    ("SELECT decided_at FROM card_batch WHERE decided_at >= ? AND decided_at < ? AND decided_at != created_at"
     " AND decided_by IS NOT 'claude'"),
    "SELECT decided_at FROM review_item WHERE decided_at >= ? AND decided_at < ?",
    "SELECT decided_at FROM exam_map WHERE decided_at >= ? AND decided_at < ?",
    "SELECT decided_at FROM leech_case WHERE decided_at >= ? AND decided_at < ?",
)


def anki_day_bounds(day: date, tz: str) -> tuple[float, float]:
    """An Anki day runs from 04:00 local to 04:00 the next morning (the rollover)."""
    start = datetime.combine(day, ROLLOVER, ZoneInfo(tz))
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def seconds(ts: Any) -> float:
    v = float(ts)
    return v / 1000 if v > 1e11 else v   # anki-core reports seconds; tolerate milliseconds


async def reviews_between(c: Caller, query: str, start: float, end: float, cap: int = 2000) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while offset < cap:
        page = await c.call("anki_reviews_list", {"query": query, "offset": offset, "limit": 100, "order": "newest",
                                                  "include_fields": ["rating_label", "answer_time"]})
        items: list[dict[str, Any]] = page.get("items") or []
        for i in items:
            t = seconds(i.get("reviewed_at", 0))
            if start <= t < end:
                out.append(i)
        if not page.get("has_more") or not items or seconds(items[-1].get("reviewed_at", 0)) < start:
            return out
        offset += len(items)
    return out


def fluency(reviews: list[dict[str, Any]]) -> float | None:
    if not reviews:
        return None
    fast = sum(1 for r in reviews if int(r.get("rating", 0)) >= 2 and float(r.get("answer_seconds") or 99) < FLUENT_SECONDS)
    return round(fast / len(reviews), 3)


async def retention(c: Caller, query: str) -> float | None:
    try:
        stats = await c.call("anki_review_stats", {"query": query, "days": 30})
    except AnkiCoreError:
        return None
    summary: dict[str, Any] = stats.get("summary") or {}
    r = summary.get("retention")
    return round(float(r), 3) if r is not None else None


def upkeep(con: sqlite3.Connection, start: float, end: float) -> dict[str, Any]:
    """Minutes spent curating between two instants, and how many decisions: one per moment, so approving a whole
    batch, or a card and the batch it settles, is one."""
    lo, hi = (datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds") for t in (start, end))
    moments = sorted({datetime.fromisoformat(str(r[0])) for q in DECISIONS for r in con.execute(q, (lo, hi))})
    sittings: list[list[datetime]] = []
    for t in moments:
        if sittings and t - sittings[-1][1] <= SITTING_GAP:
            sittings[-1][1] = t
        else:
            sittings.append([t, t])
    minutes = sum((b - a).total_seconds() / 60 + 1 for a, b in sittings)
    return {"minutes": round(minutes, 1), "decisions": len(moments)}


def approval_rates(con: sqlite3.Connection, since: str) -> dict[str, float]:
    rows = con.execute("SELECT b.id, s.origin, d.status FROM card_draft d JOIN card_batch b ON b.id = d.batch_id "
                       "JOIN card_source s ON s.id = b.source_id WHERE d.updated_at >= ? "
                       "AND d.status IN ('approved', 'written', 'rejected')", (since,))
    tally: dict[str, list[int]] = {}
    for r in rows:
        t = tally.setdefault(str(r["origin"]), [0, 0])
        t[0 if r["status"] == "rejected" else 1] += 1
    return {o: round(ok / (ok + no), 3) for o, (no, ok) in tally.items() if ok + no}


async def collect(core: Core, con: sqlite3.Connection, today: date, tz: str, window_days: int = 10) -> dict[str, Any]:
    day = today - timedelta(days=1)
    load = con.execute("SELECT reviews, minutes FROM load_day WHERE day = ?", (day.isoformat(),)).fetchone()
    out: dict[str, Any] = {"reviews": int(load["reviews"]) if load else None,
                           "minutes": float(load["minutes"]) if load else None}
    start, end = anki_day_bounds(day, tz)
    async with reads(core) as c:
        out["retention"] = await retention(c, "rated:30")
        out["retention_by_deck"] = {d: await retention(c, f'"deck:{d}" rated:30') for d in DECKS}
        out["due"] = await count(c, "is:due -is:suspended")
        out["overdue"] = await count(c, "prop:due<0 -is:suspended")
        core_reviews = await reviews_between(c, '"deck:Core" rated:2', start, end)
        out["core_reviews"] = len(core_reviews)
        out["fluency_index"] = fluency(core_reviews)
        readiness: dict[str, float | None] = {}
        for e in exam_decks.ledger_exams(con):
            if e.get("kind") != "exam" or not e.get("confirmed"):
                continue
            left = (exam_decks.exam_day(str(e["starts_at"]), tz) - today).days
            raw = e.get("lectures")
            lectures = sorted(json.loads(raw) if isinstance(raw, str) else (raw or []))
            if not 0 <= left <= window_days or not lectures:
                continue
            base = lecture_query(str(e["course"]), lectures)
            total = await count(c, base)
            ready = await count(c, f"{base} -is:new prop:r>=0.9")
            readiness[exam_decks.deck_name(str(e["course"]), str(e["title"])).removeprefix("~Exam::")] = (
                round(ready / total, 3) if total else None)
        out["exam_readiness"] = readiness
    out["leeches_waiting"] = int(con.execute("SELECT COUNT(*) FROM leech_case WHERE status = 'proposed'").fetchone()[0])
    out["questions_asked"] = int(con.execute("SELECT COUNT(*) FROM question WHERE created_at >= ? AND created_at < ?",
                                             (day.isoformat(), today.isoformat())).fetchone()[0])
    out["questions_open"] = int(con.execute("SELECT COUNT(*) FROM question WHERE status = 'open'").fetchone()[0])
    out["approval_7d"] = approval_rates(con, (today - timedelta(days=7)).isoformat())
    kept = upkeep(con, start, end)
    out["upkeep_minutes"], out["upkeep_decisions"] = kept["minutes"], kept["decisions"]
    gov = con.execute("SELECT new_total FROM governor_run WHERE day = ?", (today.isoformat(),)).fetchone()
    out["new_governed"] = int(gov["new_total"]) if gov else None
    return out


def save(con: sqlite3.Connection, day: date, metrics: dict[str, Any]) -> None:
    con.execute("INSERT INTO metric_day (day, metrics_json, created_at) VALUES (?, ?, ?) ON CONFLICT(day) DO UPDATE "
                "SET metrics_json = excluded.metrics_json, created_at = excluded.created_at",
                (day.isoformat(), json.dumps(metrics, ensure_ascii=False), now()))


def escape_tag(value: str) -> str:
    return value.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")


def line_protocol(day: date, tz: str, m: dict[str, Any]) -> str:
    """InfluxDB line protocol, stamped at noon of the day it describes."""
    ts = int(datetime.combine(day, time(12, 0), ZoneInfo(tz)).timestamp())
    fields = {k: m.get(k) for k in ("reviews", "minutes", "retention", "due", "overdue", "fluency_index",
                                    "core_reviews", "leeches_waiting", "new_governed", "questions_asked",
                                    "questions_open", "upkeep_minutes", "upkeep_decisions")}
    by_deck: dict[str, Any] = m.get("retention_by_deck") or {}
    fields |= {f"retention_{d.lower()}": v for d, v in by_deck.items()}
    body = ",".join(f"{k}={float(v)}" for k, v in fields.items() if v is not None)
    lines = [f"mnemosyne {body} {ts}"] if body else []
    readiness: dict[str, Any] = m.get("exam_readiness") or {}
    approval: dict[str, Any] = m.get("approval_7d") or {}
    for exam, r in readiness.items():
        if r is not None:
            lines.append(f"mnemosyne_exam,exam={escape_tag(exam)} readiness={float(r)} {ts}")
    for origin, rate in approval.items():
        lines.append(f"mnemosyne_approval,origin={escape_tag(origin)} rate={float(rate)} {ts}")
    return "\n".join(lines)


async def to_influx(url: str, org: str, bucket: str, token_file: Path | None, body: str) -> bool:
    """Write to InfluxDB if a token exists. Returns whether anything was sent."""
    if not body or token_file is None or not token_file.exists():
        return False
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        return False
    async with httpx.AsyncClient(timeout=20) as http:
        resp = await http.post(f"{url.rstrip('/')}/api/v2/write", params={"org": org, "bucket": bucket, "precision": "s"},
                               content=body.encode(), headers={"Authorization": f"Token {token}"})
    if resp.status_code >= 300:
        log.warning("influx_write_failed", status=resp.status_code, body=resp.text[:200])
        return False
    return True

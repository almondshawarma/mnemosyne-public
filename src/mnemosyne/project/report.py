"""The Sunday report (PLAN §9): one Telegram message on how the week went.

Built in Sunday's 04:15 run and held in the Ledger until 09:00 Chicago time, when Telegram's outbox releases it
(no 4 a.m. pings). It covers load against the budget, upkeep (minutes spent curating), retention, the fluency
index, the leech doctor, the next two weeks of exams with their readiness, and the three slowest right answers in
Core: the anchors that are known but not yet fluent. It ends with the cue for the weekly audit (the
mnemosyne-weekly skill), which reads the same week in depth in a Claude session.
"""

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from mnemosyne import questions as qstore
from mnemosyne.anki.core_client import Core, reads
from mnemosyne.learner import card_view, exam_status
from mnemosyne.ledger.db import now
from mnemosyne.project.metrics import anki_day_bounds, reviews_between, upkeep

DELIVER_AT = time(9, 0)
AUDIT_CUE = "Weekly audit: in Claude Code, say \"run the weekly audit\" (20–30 min; nothing changes without your OK)."


def nice(d: date) -> str:
    return f"{d:%a %b} {d.day}"   # no %-d: it isn't portable


def pct(v: float | None) -> str:
    return "–" if v is None else f"{round(100 * v)}%"


def course_name(c: str) -> str:
    return f"{c[:-3]} {c[-3:]}" if len(c) > 3 and c[-3:].isdigit() else c


async def slowest_right(core: Core, start: float, end: float, n: int = 3) -> list[tuple[str, float]]:
    async with reads(core) as c:
        reviews = await reviews_between(c, '"deck:Core" rated:7', start, end, cap=3000)
        # Anki caps answer time at 60 s: a capped answer is a card left open, not a slow recall
        right = sorted((r for r in reviews if int(r.get("rating", 0)) >= 2 and float(r.get("answer_seconds") or 0) < 59),
                       key=lambda r: -float(r.get("answer_seconds") or 0))
        out: list[tuple[str, float]] = []
        seen: set[int] = set()
        for r in right:
            cid = int(r["card_id"])
            if cid in seen:
                continue
            seen.add(cid)
            card = await c.call("anki_cards_get", {"card_id": cid})
            out.append((card_view(card)["prompt"], float(r.get("answer_seconds") or 0)))
            if len(out) >= n:
                break
    return out


def week_rows(con: sqlite3.Connection, first: date, last: date) -> tuple[list[sqlite3.Row], list[dict[str, Any]]]:
    load = con.execute("SELECT day, reviews, minutes FROM load_day WHERE day BETWEEN ? AND ? ORDER BY day",
                       (first.isoformat(), last.isoformat())).fetchall()
    metrics = [{"day": r["day"], **json.loads(r["metrics_json"])} for r in con.execute(
        "SELECT day, metrics_json FROM metric_day WHERE day BETWEEN ? AND ? ORDER BY day",
        (first.isoformat(), last.isoformat()))]
    return load, metrics


async def build(core: Core, con: sqlite3.Connection, today: date, tz: str, budget_minutes: float) -> str:
    last = today - timedelta(days=1)
    first = today - timedelta(days=7)
    load, metrics = week_rows(con, first, last)
    start, end = anki_day_bounds(first, tz)[0], anki_day_bounds(last, tz)[1]
    lines = [f"🧠 Mnemosyne · week to {nice(last)}"]
    minutes = [float(r["minutes"]) for r in load]
    if load:
        studied = sum(m > 0 for m in minutes)
        within = sum(abs(m - budget_minutes) <= 0.15 * budget_minutes for m in minutes)
        lines.append(f"Load: studied {studied} of 7 days, avg {sum(minutes) / 7:.0f} min a day (budget {budget_minutes:.0f}),"
                     f" {sum(int(r['reviews']) for r in load)} reviews; {within} day{'s' if within != 1 else ''} within ±15%")
    kept = upkeep(con, start, end)
    if kept["decisions"]:
        lines.append(f"Upkeep: {kept['minutes']:.0f} min deciding cards, maps, and leeches ({kept['decisions']} decisions),"
                     f" against {sum(minutes):.0f} min of reviews")
    latest = metrics[-1] if metrics else {}
    if latest:
        by_deck: dict[str, float | None] = latest.get("retention_by_deck") or {}
        decks = " · ".join(f"{d} {pct(v)}" for d, v in by_deck.items() if v is not None)
        lines.append(f"Retention (30 days): {pct(latest.get('retention'))}{' · ' + decks if decks else ''}")
        flu = [float(m["fluency_index"]) for m in metrics if m.get("fluency_index") is not None]
        if flu:
            lines.append(f"Fluency: {pct(sum(flu) / len(flu))} of Core answers right in under 3 s")
    closed = {r["status"]: r["n"] for r in con.execute(
        "SELECT status, COUNT(*) AS n FROM leech_case WHERE decided_at >= ? GROUP BY status", (first.isoformat(),))}
    waiting = int(con.execute("SELECT COUNT(*) FROM leech_case WHERE status = 'proposed'").fetchone()[0])
    if closed or waiting:
        parts = [f"{closed.get(s, 0)} {label}" for s, label in (("fixed", "fixed"), ("kept", "kept"),
                                                                 ("dismissed", "dismissed")) if closed.get(s)]
        lines.append(f"Leech doctor: {', '.join(parts) or 'nothing closed'}"
                     f"{f' · {waiting} waiting on the review page' if waiting else ''}")
    qs = qstore.stats(con, first)
    if qs["asked"] or qs["open"]:
        lines.append("")
        mix = ", ".join(f"{n} {qstore.KIND_LABEL.get(k, k)}" for k, n in sorted(qs["kinds"].items(), key=lambda kv: -kv[1]))
        deep = f"; {pct(qs['deep_share'])} went past 'what does it mean'" if qs["deep_share"] is not None else ""
        lines.append(f"Questions: you asked {qs['asked']} this week ({mix or 'none'}){deep}. {qs['open']} still open.")
        if qs["example"]:
            lines.append(f"  You asked: “{qs['example']['asked'][:140]}”")
            lines.append(f"  Asked sharper: “{qs['example']['sharper'][:180]}”")
    exams = await exam_status(core, con, tz, today, days=14, weakest=0)
    if exams:
        lines.append("")
        lines.append("Exams in the next two weeks:")
        for e in exams:
            name = f"{course_name(str(e['course']))} {e['title']}"
            when = nice(date.fromisoformat(str(e["date"])))
            if not e["mapped"]:
                lines.append(f"• {name} · {when} · not mapped yet: map it on the review page")
            else:
                gaps = f" · no cards for {', '.join(e['lectures_without_cards'])}" if e.get("lectures_without_cards") else ""
                lines.append(f"• {name} · {when} · {pct(e.get('readiness'))} ready, {e.get('unseen', 0)} unseen{gaps}")
    slow = await slowest_right(core, start, end)
    if slow:
        lines.append("")
        lines.append("Slowest right answers in Core (known, not yet fluent):")
        lines += [f"{i}. {p[:110]} ({s:.0f} s)" for i, (p, s) in enumerate(slow, 1)]
    new = sum(int(m.get("new_governed") or 0) for m in metrics)
    if new:
        lines.append(f"\nNew cards let in by the governor: {new}")
    lines.append(f"\n{AUDIT_CUE}")
    return "\n".join(lines)


def deliver_after(today: date, tz: str) -> str:
    return datetime.combine(today, DELIVER_AT, ZoneInfo(tz)).astimezone(UTC).isoformat(timespec="seconds")


def save(con: sqlite3.Connection, week: date, body: str, after: str) -> None:
    con.execute("INSERT INTO report (week, text, created_at, deliver_after) VALUES (?, ?, ?, ?) ON CONFLICT(week) DO "
                "UPDATE SET text = excluded.text, created_at = excluded.created_at, deliver_after = excluded.deliver_after"
                " WHERE report.notified_at IS NULL", (week.isoformat(), body, now(), after))


def due(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM report WHERE notified_at IS NULL AND deliver_after <= ? ORDER BY week",
                       (datetime.now(UTC).isoformat(timespec="seconds"),)).fetchall()


def mark_sent(con: sqlite3.Connection, week: str, message_ref: str) -> None:
    con.execute("UPDATE report SET notified_at = ?, message_ref = ? WHERE week = ?", (now(), message_ref, week))

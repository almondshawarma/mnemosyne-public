"""Exam decks (PLAN §5.2): one filtered deck per mapped exam, from T−10 through exam day, then removed.

`plan` is pure: it turns the Ledger's exams and today's date into the decks that should exist. `sync` applies
that plan through anki-core at 04:15 (invariant 8), exam decks before walk decks, since a card sits in at most
one filtered deck. Removing a filtered deck returns its cards home with their history, so nothing is lost.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from mnemosyne.anki.core_client import Caller, Core

log = structlog.get_logger()
PREFIX = "~Exam::"


@dataclass(frozen=True)
class Window:
    days_before: int = 10
    r_start: float = 0.85
    r_end: float = 0.95

    @classmethod
    def from_budget(cls, budget: dict[str, Any]) -> "Window":
        w: dict[str, Any] = budget.get("exam_window") or {}
        return cls(int(w.get("days_before", 10)), float(w.get("r_threshold_start", 0.85)),
                   float(w.get("r_threshold_end", 0.95)))


@dataclass(frozen=True)
class ExamDeck:
    name: str
    course: str
    title: str
    day: date
    lectures: tuple[int, ...]
    threshold: float
    search: str


@dataclass
class Plan:
    build: list[ExamDeck] = field(default_factory=list[ExamDeck])
    passed: list[str] = field(default_factory=list[str])       # deck names whose exam is over
    unmapped: list[str] = field(default_factory=list[str])     # exams in the window with no lectures yet


def deck_name(course: str, title: str) -> str:
    """The name Aman used on Desktop: ~Exam::PHYS207-Exam-I."""
    return f"{PREFIX}{course}-{re.sub(r'\s+', '-', title.strip())}"


def exam_day(starts_at: str, tz: str) -> date:
    """A bare date is a calendar day in Texas; a timestamp is converted to local time first."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", starts_at):
        return date.fromisoformat(starts_at)
    moment = datetime.fromisoformat(starts_at)
    return (moment if moment.tzinfo is None else moment.astimezone(ZoneInfo(tz))).date()


def threshold(days_left: int, w: Window) -> float:
    """Recall threshold θ: r_start at T−days_before, rising linearly to r_end at T−1 (and on exam day)."""
    span = max(w.days_before - 1, 1)
    done = min(max(w.days_before - days_left, 0), span)
    return round(w.r_start + (w.r_end - w.r_start) * done / span, 2)


def search(course: str, lectures: tuple[int, ...], theta: float) -> str:
    """Unseen cards come in so they get seen; seen ones only while their recall is below θ."""
    tags = " OR ".join(f"tag:course::{course.lower()}::l{n:02d}*" for n in lectures)
    return f"({tags}) -is:suspended (is:new OR prop:r<{theta:.2f})"


def plan(exams: list[dict[str, Any]], today: date, tz: str, w: Window) -> Plan:
    out = Plan()
    for e in exams:
        if e.get("kind") != "exam":
            continue
        day = exam_day(str(e["starts_at"]), tz)
        name = deck_name(str(e["course"]), str(e["title"]))
        days_left = (day - today).days
        if days_left < 0:
            out.passed.append(name)
            continue
        if days_left > w.days_before:
            continue
        raw = e.get("lectures")
        lectures = tuple(sorted(json.loads(raw) if isinstance(raw, str) else (raw or [])))
        if not e.get("confirmed") or not lectures:
            out.unmapped.append(name)
            continue
        theta = threshold(days_left, w)
        out.build.append(ExamDeck(name, str(e["course"]), str(e["title"]), day, lectures, theta,
                                  search(str(e["course"]), lectures, theta)))
    return out


def ledger_exams(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute("SELECT * FROM exam ORDER BY starts_at")]


async def all_decks(core: Caller) -> dict[str, int]:
    """Every deck by name, with its id."""
    decks: dict[str, int] = {}
    offset = 0
    while True:
        page = await core.call("anki_decks_list", {"offset": offset, "limit": 100})  # anki-core caps pages at 100
        items: list[dict[str, Any]] = page.get("items") or []
        decks |= {str(d["name"]): int(d.get("id", 0)) for d in items}
        if not page.get("has_more") or not items:
            return decks
        offset += len(items)


async def existing_exam_decks(core: Caller) -> set[str]:
    return {name for name in await all_decks(core) if name.startswith(PREFIX)}


async def sync(con: sqlite3.Connection, core: Core, today: date, tz: str, w: Window) -> dict[str, Any]:
    """Build or refresh every exam deck in its window; remove the decks of exams that are over."""
    p = plan(ledger_exams(con), today, tz, w)
    built: dict[str, int] = {}
    for d in p.build:
        receipt = await core.call("anki_filtered_decks_upsert", {
            "name": d.name, "search": d.search, "limit": 9999, "order": "retrievability_ascending",
            "reschedule": True, "idempotency_key": f"mn-exam-{d.name}-{today.isoformat()}"})
        built[d.name] = int((receipt.get("result") or receipt).get("cards", 0))
    removed: dict[str, int] = {}
    for name in sorted(set(p.passed) & await existing_exam_decks(core)):
        receipt = await core.call("anki_filtered_decks_remove",
                                  {"name": name, "idempotency_key": f"mn-exam-remove-{name}-{today.isoformat()}"})
        removed[name] = int((receipt.get("result") or receipt).get("returned_cards", 0))
    out: dict[str, Any] = {"built": built, "removed": removed, "unmapped": p.unmapped,
                           "thresholds": {d.name: d.threshold for d in p.build}}
    log.info("exam_decks_synced", **out)
    return out

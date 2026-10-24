"""Leech doctor (PLAN §9): cards Aman keeps failing get a diagnosis and a proposed fix he decides on /review.

Candidates, in order: cards he marked (`*` on the controller), Anki's own leeches, then 5, 4, and 3 lapses, then
FSRS difficulty above 9 of 10 with at least two lapses (difficulty alone flagged the whole relearning backlog on
2026-09-27). Image-only cards are left out: the doctor can't see them. A card is looked at again only after it lapses again, or when he marks it again.
At most PER_NIGHT new cases a night, so the page never floods.

The LLM reads the canon (SKILL.md) plus the card, its review history, and its nearest neighbours in the collection,
and answers with one of:
- **rewrite**: same memory target, better wording. Applying it edits the note in place, so history is kept.
- **replace**: the target itself was wrong (two facts, a vague cue). New cards are written, and only then is the old
  card suspended: one card, on Aman's explicit Apply (CLAUDE.md invariant 9).
- **add**: the card is fine but leans on something missing (a prerequisite, a contrast card). New cards only.
- **keep**: nothing wrong with the card; it's just hard. Nothing changes.
Nothing is written until Aman presses Apply on the page.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from pydantic import ValidationError

from mnemosyne.anki.core_client import AnkiCoreError, Caller, Core, reads
from mnemosyne.cards import PIPELINE_OWNED, SPEECH, CardIn, Payload, Schema, Source
from mnemosyne.generate.llm import LLM, text
from mnemosyne.generate.propose import parse
from mnemosyne.inbox import Verdict, propose
from mnemosyne.learner import card_view, plain
from mnemosyne.ledger import store
from mnemosyne.ledger.db import now
from mnemosyne.merge import home_deck, note_fields, note_tags, rewrite_note
from mnemosyne.verify import dedupe
from mnemosyne.verify.quantities import verify_card

log = structlog.get_logger()

ROUTE = "leech"
ORIGIN = "leech"
PER_NIGHT = 6
TIERS = ("-is:suspended tag:marked", "-is:suspended -is:new tag:leech", "-is:suspended -is:new prop:lapses>=5",
         "-is:suspended -is:new prop:lapses=4", "-is:suspended -is:new prop:lapses=3",
         "-is:suspended -is:new prop:d>0.9 prop:lapses>=2")
DIAGNOSES = ("ambiguous_cue", "not_atomic", "interference", "missing_prerequisite", "too_long", "fine")
ACTIONS = ("rewrite", "replace", "add", "keep")
UNTAG = ["marked", "leech"]

INSTRUCTIONS = """
## Your job: the leech doctor

Below is one card Aman keeps failing, with its review history and the most similar notes in his collection.
Diagnose why, against the canon above, and propose the smallest fix that would make it stick.

Diagnoses (pick one):
- ambiguous_cue: the prompt admits more than one answer, or doesn't say what form the answer takes (canon 2).
- not_atomic: it asks for two facts, or a fact and its reason (canon 1).
- interference: it's confused with a similar card; the similar notes show which (canon 14).
- missing_prerequisite: it leans on something Aman doesn't have a card for.
- too_long: it takes longer than ~8 s to answer; it's a derivation, not a card (canon 3).
- fine: the card is well made; the fact is just hard or new. Say so; don't invent a problem.

Actions (pick one):
- rewrite: same memory target, better wording. Give "fields": the complete new content fields of the SAME note type
  (every content field, not just the changed ones; leave MnemosyneID, Source, SourceLink, and audio fields out).
  Update the Speak* fields to match, or leave them empty if the card is visual.
- replace: the memory target itself is wrong (e.g. two facts). Give "cards": new cards in the canon's payload
  schema, carrying the old note's course:: and topic:: tags, with src::chat (never src::legacy: they're new).
  The old card will be suspended.
- add: the card is fine but needs a prerequisite or a contrast card. Give "cards" as above.
- keep: no change.

Reply with exactly one fenced json block:
{"diagnosis": "...", "explanation": "one or two sentences Aman will read", "action": "...",
 "fields": {...} or null, "cards": [...] or null}
"""


@dataclass
class Case:
    id: int
    card_id: int
    note_id: int
    mnemosyne_id: str | None
    note_type: str
    deck: str
    fields: dict[str, str]
    tags: list[str]
    signals: dict[str, Any]
    lapses: int
    status: str
    diagnosis: str | None
    explanation: str | None
    action: str | None
    proposal: dict[str, Any]
    problems: list[str]

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Case":
        return cls(r["id"], r["card_id"], r["note_id"], r["mnemosyne_id"], r["note_type"], r["deck"],
                   json.loads(r["fields_json"]), json.loads(r["tags_json"]), json.loads(r["signals_json"]),
                   r["lapses"], r["status"], r["diagnosis"], r["explanation"], r["action"],
                   json.loads(r["proposal_json"] or "{}"), json.loads(r["problems_json"] or "[]"))

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "card_id": self.card_id, "note_type": self.note_type, "deck": self.deck,
                "fields": {k: v for k, v in self.fields.items() if k not in PIPELINE_OWNED},
                "tags": self.tags, "signals": self.signals, "status": self.status, "diagnosis": self.diagnosis,
                "explanation": self.explanation, "action": self.action, "proposal": self.proposal,
                "problems": self.problems, "in_ledger": self.signals.get("in_ledger", False)}


def latest_cases(con: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    rows = con.execute("SELECT * FROM leech_case WHERE id IN (SELECT MAX(id) FROM leech_case GROUP BY card_id)")
    return {int(r["card_id"]): r for r in rows}


def due_again(prev: sqlite3.Row | None, lapses: int, marked: bool) -> bool:
    """A card is looked at when it has no case, when it lapsed since its last case closed, or when marked again."""
    if prev is None:
        return True
    if prev["status"] in ("proposed", "applying"):
        return False
    return marked or lapses > int(prev["lapses"])


async def find(c: Caller, con: sqlite3.Connection, limit: int = PER_NIGHT) -> list[dict[str, Any]]:
    """Up to `limit` cards to diagnose, most urgent first, each with its card and note as anki-core returns them."""
    prev = latest_cases(con)
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for tier in TIERS:
        page = await c.call("anki_cards_search", {"query": tier, "offset": 0, "limit": 100})
        items: list[dict[str, Any]] = page.get("items") or []
        for item in items:
            cid = int(item["id"])
            if cid in seen:
                continue
            seen.add(cid)
            p = prev.get(cid)
            if p is not None and p["status"] in ("proposed", "applying"):
                continue
            card = await c.call("anki_cards_get", {"card_id": cid, "include_sections": ["review_summary", "fsrs"]})
            note = await c.call("anki_notes_get", {"note_id": int(card["note_id"])})
            first = next(iter(note_fields(note).values()), "")
            if "<img" in first.lower() and not plain(re.sub(r"<img[^>]*>", "", first, flags=re.IGNORECASE)):
                continue
            marked = "marked" in [t.lower() for t in note_tags(note)]
            sched: dict[str, Any] = card.get("scheduling") or {}
            lapses = int(sched.get("lapses", 0))
            if not due_again(p, lapses, marked):
                continue
            out.append({"card": card, "note": note, "marked": marked, "tier": tier})
            if len(out) >= limit:
                return out
    return out


async def history(c: Caller, card_id: int) -> list[str]:
    page = await c.call("anki_reviews_list", {"card_id": card_id, "limit": 20, "order": "newest",
                                              "include_fields": ["rating_label", "answer_time", "review_kind"]})
    items: list[dict[str, Any]] = page.get("items") or []
    return [f"{i.get('rating_label') or i.get('rating')} {float(i.get('answer_seconds') or 0):.0f}s"
            f" ({i.get('review_kind', '')})" for i in items]


def similar(first: str, nid: int, ids: list[int], matrix: Any, vec: Any, notes: dict[int, str], k: int = 3) -> list[str]:
    if not len(ids):
        return []
    sims = matrix @ vec
    order = [int(i) for i in np.argsort(-sims)]
    return [f"{notes[ids[i]][:160]} ({float(sims[i]):.2f})" for i in order if ids[i] != nid][:k]


def signals(found: dict[str, Any], reviews: list[str], near: list[str], in_ledger: bool) -> dict[str, Any]:
    card = found["card"]
    view = card_view(card)
    summary: dict[str, Any] = card.get("review_summary") or {}
    return {"lapses": view["lapses"], "reviews": view["reviews"], "retrievability": view["retrievability"],
            "difficulty": view["difficulty"], "stability_days": view["stability_days"],
            "average_answer_seconds": round(float(summary.get("average_answer_seconds") or 0), 1),
            "marked": found["marked"], "why_found": found["tier"], "recent_reviews": reviews,
            "similar_notes": near, "in_ledger": in_ledger}


def speech_mode(note_type: str, fields: dict[str, str]) -> str:
    return "audio" if any(fields.get(k, "").strip() for k in SPEECH.get(note_type, ())) else "visual"


def check_rewrite(case: Case, fields: dict[str, Any], schema: Schema) -> tuple[dict[str, str], list[str]]:
    """The full field set after a rewrite, and what's structurally wrong with it (tags aside: legacy notes keep theirs)."""
    names = schema.notetypes.get(case.note_type)
    if names is None:
        return case.fields, [f"note type {case.note_type} isn't one of the M-* types"]
    bad = sorted(k for k in fields if k not in names or k in PIPELINE_OWNED)
    merged = {**case.fields, **{k: str(v) for k, v in fields.items() if k in names and k not in PIPELINE_OWNED}}
    content = {k: v for k, v in merged.items() if k not in PIPELINE_OWNED}
    card = CardIn(note_type=case.note_type, deck=case.deck, fields=content, tags=["type::fundamental"],
                  mode=speech_mode(case.note_type, content))  # pyright: ignore[reportArgumentType]
    problems = [f"can't set {', '.join(bad)}"] if bad else []
    problems += [p for p in schema.problems(card) if not p.startswith("unknown deck")]
    if merged == case.fields:
        problems.append("the rewrite changes nothing")
    return merged, problems


def check_cards(raw: list[Any], schema: Schema, anchors: Path) -> tuple[list[dict[str, Any]], list[str]]:
    cards: list[dict[str, Any]] = []
    problems: list[str] = []
    for i, r in enumerate(raw):
        try:
            card = CardIn.model_validate(r)
        except ValidationError as e:
            problems.append(f"card {i + 1}: {e.errors()[0]['msg']}")
            continue
        for p in schema.problems(card):
            problems.append(f"card {i + 1}: {p}")
        v = verify_card(card.note_type, card.tags, [ch.model_dump() for ch in card.checks], anchors)
        if v.status != "pass":
            problems.append(f"card {i + 1}: number check {v.status}: {v.detail[:200]}")
        cards.append(card.model_dump(exclude_none=True))
    if not raw:
        problems.append("no cards given")
    return cards, problems


def check(doc: dict[str, Any], case: Case, schema: Schema, anchors: Path) -> tuple[dict[str, Any], list[str]]:
    diagnosis, action = str(doc.get("diagnosis", "")), str(doc.get("action", ""))
    problems: list[str] = []
    if diagnosis not in DIAGNOSES:
        problems.append(f"diagnosis must be one of {', '.join(DIAGNOSES)}")
    if action not in ACTIONS:
        problems.append(f"action must be one of {', '.join(ACTIONS)}")
    proposal: dict[str, Any] = {}
    if action == "rewrite":
        fields: dict[str, Any] = doc.get("fields") or {}
        merged, found = check_rewrite(case, fields, schema)
        proposal = {"fields": {k: v for k, v in merged.items() if k not in PIPELINE_OWNED}}
        problems += found
    elif action in ("replace", "add"):
        cards, found = check_cards(list(doc.get("cards") or []), schema, anchors)
        proposal = {"cards": cards}
        problems += found
    return {"diagnosis": diagnosis, "explanation": str(doc.get("explanation", ""))[:1000], "action": action,
            "proposal": proposal}, problems


def prompt(case: Case) -> str:
    body = {"note_type": case.note_type, "home_deck": case.deck,
            "fields": {k: v for k, v in case.fields.items() if k not in PIPELINE_OWNED and v},
            "tags": case.tags, "signals": {k: v for k, v in case.signals.items() if k != "in_ledger"}}
    return "The card:\n" + json.dumps(body, ensure_ascii=False, indent=1)


async def diagnose(llm: LLM, skill_md: str, case: Case, schema: Schema, anchors: Path) -> tuple[dict[str, Any], list[str]]:
    """One LLM pass, plus one retry with the problems if the answer doesn't check out."""
    system = ("You help Aman keep his Anki cards effective, with the mnemosyne canon below.\n\n" + skill_md
              + "\n" + INSTRUCTIONS)
    content = [text(prompt(case))]
    raw = await llm.complete(ROUTE, system, content)
    try:
        result, problems = check(parse(raw), case, schema, anchors)
    except (ValueError, TypeError) as e:
        result, problems = {}, [f"unparseable reply: {e}"]
    if problems:
        raw = await llm.complete(ROUTE, system, [*content, text(
            "Your reply had these problems; answer again in full:\n- " + "\n- ".join(problems) + "\n\nReply:\n" + raw[:20000])])
        try:
            result, problems = check(parse(raw), case, schema, anchors)
        except (ValueError, TypeError) as e:
            result, problems = result or {}, [f"unparseable reply after retry: {e}"]
    return result, problems


def insert_case(con: sqlite3.Connection, found: dict[str, Any], deck: str, sig: dict[str, Any]) -> Case:
    card, note = found["card"], found["note"]
    fields = note_fields(note)
    cur = con.execute(
        "INSERT INTO leech_case (card_id, note_id, mnemosyne_id, note_type, deck, fields_json, tags_json, signals_json,"
        " lapses, status, found_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?)",
        (int(card["id"]), int(card["note_id"]), fields.get("MnemosyneID") or None, str(note.get("note_type_name")),
         deck, json.dumps(fields, ensure_ascii=False), json.dumps(note_tags(note), ensure_ascii=False),
         json.dumps(sig, ensure_ascii=False), int(sig["lapses"]), now()))
    row = con.execute("SELECT * FROM leech_case WHERE id = ?", (cur.lastrowid,)).fetchone()
    return Case.from_row(row)


def usable(result: dict[str, Any]) -> bool:
    """A diagnosis Aman can decide on. Problems in the proposed content don't block it: Apply re-checks, and he
    can edit the proposal on the page first."""
    return result.get("diagnosis") in DIAGNOSES and result.get("action") in ACTIONS


def save_diagnosis(con: sqlite3.Connection, case: Case, result: dict[str, Any], problems: list[str]) -> None:
    status = "proposed" if usable(result) else "failed"
    con.execute("UPDATE leech_case SET status = ?, diagnosis = ?, explanation = ?, action = ?, proposal_json = ?,"
                " problems_json = ? WHERE id = ?",
                (status, result.get("diagnosis"), result.get("explanation"), result.get("action"),
                 json.dumps(result.get("proposal") or {}, ensure_ascii=False), json.dumps(problems, ensure_ascii=False),
                 case.id))


@dataclass
class Doctor:
    con: sqlite3.Connection
    core: Core
    llm: LLM
    embedder: dedupe.Embedder | None
    schema: Schema
    skill_md: str
    anchors: Path


async def run(d: Doctor, limit: int = PER_NIGHT, dry_run: bool = False) -> dict[str, Any]:
    """Find tonight's cases and diagnose them. Reads only; the proposals wait on /review."""
    async with reads(d.core) as c:
        found = await find(c, d.con, limit)
        if dry_run:
            return {"candidates": [{"card_id": f["card"]["id"], "why": f["tier"], "marked": f["marked"],
                                    **{k: card_view(f["card"])[k] for k in ("prompt", "lapses", "retrievability")}}
                                   for f in found]}
        reviews = {int(f["card"]["id"]): await history(c, int(f["card"]["id"])) for f in found}
    near: dict[int, list[str]] = {}
    if found and d.embedder is not None:
        try:
            notes = await dedupe.existing_notes(d.core)
            ids, matrix = await dedupe.index(d.con, notes, d.embedder)
            firsts = [dedupe.normalize(next(iter(note_fields(f["note"]).values()), "")) for f in found]
            vecs = np.asarray(await d.embedder.embed(firsts), dtype=np.float32)
            vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
            for f, first, vec in zip(found, firsts, vecs, strict=True):
                near[int(f["card"]["id"])] = similar(first, int(f["note"]["id"]), ids, matrix, vec, notes)
        except Exception as e:  # noqa: BLE001 -- neighbours help the diagnosis but aren't required
            log.warning("leech_neighbours_failed", error=str(e)[:200])
    out: dict[str, Any] = {"diagnosed": 0, "failed": 0, "cases": []}
    for f in found:
        cid = int(f["card"]["id"])
        fields = note_fields(f["note"])
        in_ledger = bool(fields.get("MnemosyneID")) and store.get_draft(d.con, fields["MnemosyneID"]) is not None
        case = insert_case(d.con, f, home_deck(f["card"], d.schema.decks),
                           signals(f, reviews.get(cid, []), near.get(cid, []), in_ledger))
        try:
            result, problems = await diagnose(d.llm, d.skill_md, case, d.schema, d.anchors)
        except Exception as e:  # noqa: BLE001 -- a failed diagnosis is recorded and retried after the next lapse
            result, problems = {}, [f"diagnosis failed: {str(e)[:300]}"]
        save_diagnosis(d.con, case, result, problems)
        ok = usable(result)
        out["diagnosed" if ok else "failed"] += 1
        out["cases"].append({"id": case.id, "card": plain(next(iter(case.fields.values()), ""), 80),
                             "diagnosis": result.get("diagnosis"), "action": result.get("action")})
    log.info("leech_doctor_ran", diagnosed=out["diagnosed"], failed=out["failed"])
    return out


def cases(con: sqlite3.Connection, status: str = "proposed", limit: int = 50) -> list[dict[str, Any]]:
    rows = con.execute("SELECT * FROM leech_case WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))
    return [Case.from_row(r).public() for r in rows]


def get_case(con: sqlite3.Connection, case_id: int) -> Case | None:
    row = con.execute("SELECT * FROM leech_case WHERE id = ?", (case_id,)).fetchone()
    return Case.from_row(row) if row else None


def close(con: sqlite3.Connection, case: Case, status: str, by: str, note: str, applied: dict[str, Any]) -> None:
    con.execute("UPDATE leech_case SET status = ?, decided_by = ?, decision_note = ?, applied_json = ?, decided_at = ?"
                " WHERE id = ?", (status, by, note or None, json.dumps(applied, ensure_ascii=False), now(), case.id))


async def untag(core: Core, case: Case) -> None:
    present = [t for t in UNTAG if t in [x.lower() for x in case.tags]]
    if present:
        await core.call("anki_notes_remove_tags", {"note_ids": [case.note_id], "tags": present,
                                                   "idempotency_key": f"mn-leech-untag-{case.id}"})


def new_drafts(con: sqlite3.Connection, schema: Schema, case: Case, cards: list[dict[str, Any]]) -> list[str]:
    """Replacement or added cards become drafts approved by Aman's Apply; the write job takes them from there."""
    payload = Payload(source=Source(kind="leech", ref=f"leech case {case.id}"),
                      cards=[CardIn.model_validate(c) for c in cards])
    prop = propose(con, schema, payload, ORIGIN, lambda _i, _c: Verdict(approved=True))
    if prop.batch_id is None:
        return []
    return [dft.mnemosyne_id for dft in store.batch_drafts(con, prop.batch_id)]


async def apply_rewrite(con: sqlite3.Connection, core: Core, schema: Schema, case: Case, fields: dict[str, str],
                        by: str, note: str) -> dict[str, Any]:
    """Edit the note in place: through the Ledger when mnemosyne wrote it, else a direct upsert on its MnemosyneID
    (anki-core keeps the note's deck, scheduling, and history, and only ever adds tags)."""
    merged, problems = check_rewrite(case, fields, schema)
    if problems:
        raise ValueError("; ".join(problems))
    mid = case.mnemosyne_id
    if not mid:
        raise ValueError("this note has no MnemosyneID, so it can't be edited in place")
    return await rewrite_note(con, core, mid=mid, deck=case.deck, note_type=case.note_type, fields=merged, by=by,
                              note=f"leech doctor: {note}".strip())


async def suspend_if_written(con: sqlite3.Connection, core: Core, case: Case, drafts: list[str]) -> bool:
    """Suspend the replaced card once every replacement is in Anki; until then the case stays 'applying'."""
    statuses = [store.get_draft(con, m) for m in drafts]
    if not drafts or any(s is None or s.status != "written" for s in statuses):
        return False
    await core.call("anki_cards_suspend", {"card_ids": [case.card_id], "idempotency_key": f"mn-leech-suspend-{case.id}"})
    return True


async def finish_pending(con: sqlite3.Connection, core: Core) -> int:
    """04:15: replacements that have reached Anki since Apply get their old card suspended."""
    done = 0
    for row in con.execute("SELECT * FROM leech_case WHERE status = 'applying'").fetchall():
        case = Case.from_row(row)
        applied = json.loads(row["applied_json"] or "{}")
        try:
            if await suspend_if_written(con, core, case, applied.get("drafts") or []):
                close(con, case, "fixed", row["decided_by"] or "", row["decision_note"] or "",
                      {**applied, "suspended": [case.card_id]})
                done += 1
        except AnkiCoreError as e:
            log.warning("leech_suspend_failed", case=case.id, error=str(e)[:200])
    return done


async def decide(con: sqlite3.Connection, core: Core, schema: Schema, anchors: Path, case: Case, verdict: str,
                 by: str, note: str, fields: dict[str, str] | None = None,
                 cards: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Aman's decision on a proposed case. `fields`/`cards` are his edits to the proposal, if any.
    Returns what happened and whether the write job must run before a replacement's old card can be suspended."""
    if case.status != "proposed":
        raise ValueError(f"this case is {case.status}")
    if verdict in ("keep", "dismiss"):
        if verdict == "dismiss" and not note.strip():
            raise ValueError("say why the proposal is wrong, even briefly: it tunes the doctor")
        await untag(core, case)
        close(con, case, "kept" if verdict == "keep" else "dismissed", by, note, {"untagged": True})
        return {"status": "kept" if verdict == "keep" else "dismissed", "write": False}
    if verdict != "apply":
        raise ValueError("verdict is apply, keep, or dismiss")
    if case.action == "rewrite":
        result = await apply_rewrite(con, core, schema, case, fields or case.proposal.get("fields") or {}, by, note)
        await untag(core, case)
        close(con, case, "fixed", by, note, result)
        return {"status": "fixed", "write": result["rewrite"] == "ledger", **result}
    if case.action in ("replace", "add"):
        chosen = cards if cards is not None else list(case.proposal.get("cards") or [])
        checked, problems = check_cards(chosen, schema, anchors)
        if problems:
            raise ValueError("; ".join(problems))
        drafts = new_drafts(con, schema, case, checked)
        await untag(core, case)
        applied: dict[str, Any] = {"drafts": drafts, "action": case.action}
        if case.action == "add":
            close(con, case, "fixed", by, note, applied)
            return {"status": "fixed", "write": True, **applied}
        close(con, case, "applying", by, note, applied)
        return {"status": "applying", "write": True, **applied}
    await untag(core, case)
    close(con, case, "kept", by, note, {"untagged": True})
    return {"status": "kept", "write": False}

"""In-session review sets on /review (formerly the claude.ai viewer).

Claude pushes a batch it wrote in a session. Its new cards become Ledger drafts held for Aman; cards already in
the Ledger are linked instead. Aman decides each card with a reason, adds notes, marks left-out items to promote,
and presses Finish. Claude then takes the export back (golden merge, canon proposals, follow-up cards).
Approving a new card writes it to Anki; editing a card that is already there updates the note in place.
"""

import re
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from mnemosyne.cards import CardIn, Payload, Schema, Source, content_hash
from mnemosyne.inbox import Verdict, propose
from mnemosyne.ledger import store
from mnemosyne.web.review import CardAction, resolve

REVIEW_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
ORIGIN = "review"   # Telegram skips these batches; Aman reviews them on the page


class ReviewIn(BaseModel):
    id: str = Field(pattern=REVIEW_ID.pattern)
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["lecture", "golden", "cards"] = "cards"
    source: Source
    cards: list[dict[str, Any]] = Field(min_length=1, max_length=200)   # skill cards plus key, why, golden, context
    signals: list[str] = Field(default_factory=list[str])
    skipped: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    canon: dict[str, str] = Field(default_factory=dict[str, str])


class ItemAction(BaseModel):
    key: str
    action: Literal["approve", "edit", "reject"]
    reason: str = Field(default="", max_length=2000)
    fields: dict[str, str] | None = None
    tags: list[str] | None = None
    deck: str | None = None


class ItemActions(BaseModel):
    decisions: list[ItemAction] = Field(min_length=1, max_length=200)
    by: str = "review-page"


class ReviewNotes(BaseModel):
    notes: str = Field(default="", max_length=5000)
    promote: list[int] = Field(default_factory=list[int])


Parsed = tuple[str, int, dict[str, Any], CardIn | None, list[str] | None]


def parse_cards(review: ReviewIn, schema: Schema) -> list[Parsed]:
    """Each card with its key and, when it can become a note, its CardIn; otherwise the problems."""
    out: list[Parsed] = []
    seen: set[str] = set()
    for pos, raw in enumerate(review.cards):
        try:
            card = CardIn.model_validate(raw)
        except ValidationError as e:
            key = str(raw.get("key") or f"card-{pos}")
            out.append((key, pos, raw, None, [f"not a card: {err['msg']}" for err in e.errors()][:3]))
        else:
            key = str(raw.get("key") or content_hash(card)[:10])
            problems = schema.problems(card)
            out.append((key, pos, raw, None if problems else card, problems or None))
        if key in seen:
            raise ValueError(f"duplicate card key {key!r}")
        seen.add(key)
    return out


def new_cards(con: sqlite3.Connection, parsed: list[Parsed]) -> list[CardIn]:
    """Valid cards whose content isn't in the Ledger yet (each once)."""
    cards = {content_hash(c): c for _k, _p, _r, c, _pr in parsed if c is not None}
    existing = store.drafts_by_hash(con, list(cards))
    return [c for h, c in cards.items() if h not in existing]


def propose_held(con: sqlite3.Connection, schema: Schema, source: Source, cards: list[CardIn]) -> None:
    """Without the pipeline (no anki-core): drafts held for review with no checks beyond the schema."""
    def hold(_i: int, card: CardIn) -> Verdict:
        return Verdict(approved=False, reason=f"question: {card.question}" if card.question else None)
    propose(con, schema, Payload(source=source, cards=cards), ORIGIN, hold)


def create(con: sqlite3.Connection, review: ReviewIn, parsed: list[Parsed], dropped: dict[str, str]) -> dict[str, Any]:
    """Link every card to its draft and store the set. `dropped` names notes that new cards duplicated."""
    hashes = {key: content_hash(c) for key, _p, _r, c, _pr in parsed if c is not None}
    drafts = store.drafts_by_hash(con, list(hashes.values()))
    items: list[store.ReviewItem] = []
    for key, pos, raw, _card, problems in parsed:
        h = hashes.get(key)
        mid = drafts.get(h) if h else None
        if mid is None and h in dropped:
            problems = [f"duplicate of an existing note: {dropped[h]}"]
        items.append((key, pos, raw, mid, problems))
    payload = {"source": review.source.model_dump(), "signals": review.signals, "skipped": review.skipped,
               "canon": review.canon}
    store.create_review(con, review.id, review.title, review.kind, payload, items)
    return {"id": review.id, "cards": len(items), "linked": sum(i[3] is not None for i in items),
            "without_draft": sum(i[3] is None for i in items)}


def decide(con: sqlite3.Connection, schema: Schema, review_id: str, actions: list[ItemAction],
           by: str) -> dict[str, Any]:
    """Record each verdict and carry it to the item's draft. Returns what changed, problems per key, and whether
    the write job should run."""
    review = store.get_review(con, review_id)
    if review is None:
        raise KeyError(review_id)
    items = {i["key"]: i for i in review["items"]}
    problems: dict[str, list[str]] = {}
    changed = 0
    write = False
    for a in actions:
        item = items.get(a.key)
        if item is None:
            problems[a.key] = ["not in this review"]
            continue
        if item["verdict"]:
            problems[a.key] = [f"already decided ({item['verdict']})"]
            continue
        if a.action == "reject" and not a.reason.strip():
            problems[a.key] = ["say why, even briefly: rejections without a reason can't teach the canon"]
            continue
        draft = store.get_draft(con, item["mnemosyne_id"]) if item["mnemosyne_id"] else None
        if draft is not None and (draft.status == "pending" or a.action == "edit"):
            got = resolve(draft, CardAction(mnemosyne_id=draft.mnemosyne_id, action=a.action, note=a.reason,
                                            fields=a.fields, tags=a.tags, deck=a.deck), schema)
            if isinstance(got, list):
                problems[a.key] = got
                continue
            if draft.status == "pending":
                store.decide_cards(con, draft.batch_id, [got], by)
                write = write or got.approve
            elif got.before and draft.status in ("approved", "written", "error"):
                store.revise(con, got, by)
                write = True
        edits = None if a.action != "edit" else {k: v for k, v in
                                                 (("fields", a.fields), ("tags", a.tags), ("deck", a.deck)) if v}
        changed += store.record_item(con, review_id, a.key, a.action, a.reason.strip(), edits)
    return {"changed": changed, "problems": problems, "write": write}


def edited(card: dict[str, Any], edits: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The card with Aman's edits applied, and what the edited parts said before."""
    if not edits:
        return card, {}
    fields: dict[str, str] = edits.get("fields") or {}
    before: dict[str, Any] = {k: card.get("fields", {}).get(k, "") for k, v in fields.items()
                              if card.get("fields", {}).get(k, "") != v}
    out = {**card, "fields": {**card.get("fields", {}), **fields}}
    for k in ("tags", "deck"):
        if edits.get(k) and edits[k] != card.get(k):
            before[k] = card.get(k)
            out[k] = edits[k]
    return out, before


def export(review: dict[str, Any]) -> dict[str, Any]:
    """What Claude takes back: the same shape the old viewer's `review.py apply` produced (cards, rejected,
    pending, review.notes/promote), plus what happened in Anki that Aman may need to act on."""
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    in_anki_rejected: list[dict[str, Any]] = []
    no_draft: dict[str, list[str]] = {}
    for item in review["items"]:
        card = item["card"]
        if item["problems"] and item["mnemosyne_id"] is None:
            no_draft[item["key"]] = item["problems"]
        if not item["verdict"]:
            pending.append(card)
            continue
        card, before = edited(card, item["edits"])
        verdict = "edit" if item["verdict"] == "edit" and before else (
            "approve" if item["verdict"] == "edit" else item["verdict"])
        rev: dict[str, Any] = {"verdict": verdict, "reason": item["reason"], "at": item["decided_at"] or ""}
        if before:
            rev["before"] = before
        if verdict == "reject":
            rejected.append({**card, "review": rev})
            draft = item["draft"]
            if draft and draft["status"] in ("approved", "written", "error"):
                in_anki_rejected.append({"key": item["key"], "mnemosyne_id": item["mnemosyne_id"]})
        else:
            kept.append({**card, "review": rev})
    skipped: list[dict[str, Any]] = review.get("skipped") or []
    chosen: list[int] = review["promote"]
    promote = [skipped[i] for i in chosen if 0 <= i < len(skipped)]
    return {"id": review["id"], "title": review["title"], "kind": review["kind"], "status": review["status"],
            "source": review.get("source"), "signals": review.get("signals", []), "skipped": skipped,
            "cards": kept, "rejected": rejected, "pending": pending,
            "review": {"notes": review["notes"], "promote": promote},
            "anki": {"rejected_but_in_anki": in_anki_rejected, "no_draft": no_draft}}

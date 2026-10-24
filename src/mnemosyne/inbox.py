"""Turn a proposed payload into drafts. Re-proposing the same payload, or the same card, does nothing."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from mnemosyne.cards import CardIn, Payload, Schema, content_hash, note_tags, payload_hash, uuid7
from mnemosyne.ledger import store

# Who may say a batch is already approved. The viewer counts because Aman approved every card there.
PREAPPROVED_ORIGINS = frozenset({"viewer"})


@dataclass
class Proposal:
    batch_id: str | None
    status: str
    created: int = 0
    duplicates: int = 0
    invalid: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    already_proposed: bool = False

    def summary(self) -> dict[str, object]:
        return {"batch_id": self.batch_id, "status": self.status, "created": self.created,
                "duplicates": self.duplicates, "invalid": self.invalid, "already_proposed": self.already_proposed}


@dataclass(frozen=True)
class Verdict:
    """What happens to one card: written right away (with or without probation), or held for Aman."""

    approved: bool
    reason: str | None = None
    probation: bool = False
    merge_into: int | None = None   # the Anki note dedupe matched (held cards only)
    merge_up: bool = False          # this card is the more fundamental framing of that note (canon 22)


Decide = Callable[[int, CardIn], Verdict]


# Why a proposal that skipped the verify gate and dedupe is held. With the pipeline configured, chat and Telegram
# proposals go through both (pipeline.propose_checked); these are the cases where they couldn't.
UNCHECKED = "unchecked: this server has no pipeline, so neither numbers nor duplicates were checked"


def unchecked_because(why: str) -> str:
    return f"unchecked: the checks couldn't run ({why}), so neither numbers nor duplicates were checked"


def review_reason(origin: str, unchecked: str = UNCHECKED) -> str | None:
    """Why a draft needs Aman when no checks ran: always, unless the origin is preapproved."""
    if origin in PREAPPROVED_ORIGINS:
        return None
    return unchecked


def default_decide(origin: str, unchecked: str = UNCHECKED) -> Decide:
    reason = review_reason(origin, unchecked)
    return lambda _i, _card: Verdict(approved=reason is None, reason=reason)


def propose(con: sqlite3.Connection, schema: Schema, payload: Payload, origin: str,
            decide: Decide | None = None) -> Proposal:
    phash = payload_hash(payload)
    if (source_id := store.source_by_hash(con, phash)) is not None:
        batch_id = store.batch_for_source(con, source_id)
        batch = store.get_batch(con, batch_id)
        return Proposal(batch_id, batch.status if batch else "unknown", already_proposed=True)

    invalid: list[dict[str, object]] = []
    valid: list[CardIn] = []
    for i, card in enumerate(payload.cards):
        if problems := schema.problems(card):
            invalid.append({"index": i, "problems": problems})
        else:
            valid.append(card)
    hashes = [content_hash(c) for c in valid]
    seen = store.existing_hashes(con, hashes)
    decide = decide or default_decide(origin)
    drafts: list[store.NewDraft] = []
    fresh: set[str] = set()
    for i, (card, chash) in enumerate(zip(valid, hashes, strict=True)):
        if chash in seen or chash in fresh:
            continue
        fresh.add(chash)
        mid = uuid7()
        verdict = decide(i, card)
        drafts.append((mid, chash, card, schema.note_fields(card, mid), note_tags(card, probation=verdict.probation),
                       "approved" if verdict.approved else "pending", verdict.reason, verdict.merge_into,
                       verdict.merge_up))
    batch_id = store.create_batch(con, phash, origin, payload.source, drafts)
    statuses = {d[5] for d in drafts}
    status = "empty" if not drafts else ("approved" if statuses == {"approved"} else "pending")
    return Proposal(batch_id, status, created=len(drafts), duplicates=len(valid) - len(drafts), invalid=invalid)

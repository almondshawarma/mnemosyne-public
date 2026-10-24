"""Cross-course duplicates (canon 22; Aman, 2026-10-21): one idea is one note that carries every course teaching it,
worded in the most fundamental discipline that owns it.

When dedupe matches a new card to a note already in Anki:
- if that note is at least as fundamental, the new card is dropped (or held, when only flagged) and its course,
  topic, and principle tags join the note (pipeline.adopt, or Merge on the review page);
- if the new card is the more fundamental framing (linear algebra over Calc III or statics, E&M over its lab), it's
  held as a merge-up. Aman's tap rewrites the note in place in the new wording. The note keeps its history and its
  MnemosyneID, and gains the new card's tags.

Which is more fundamental comes from config, not a model: `foundations` in topics.yaml orders the disciplines, and
`homes` in courses.yaml says which course owns which. Ties keep the existing note, since it holds the history.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml

from mnemosyne.anki.core_client import Core
from mnemosyne.cards import PIPELINE_OWNED, CardIn, content_hash
from mnemosyne.ledger import store
from mnemosyne.verify.dedupe import Match

log = structlog.get_logger()

CONNECTING = ("course::", "topic::", "principle::")
STALE_AUDIO = ("AudioFront", "AudioBack")   # rendered from the old wording; a rewrite clears them


@dataclass(frozen=True)
class Foundations:
    order: list[str]                # topic paths (no topic:: prefix), most fundamental first
    homes: dict[str, list[str]]     # course code, lowercase → the topic paths it is the home of

    @classmethod
    def load(cls, config: Path) -> "Foundations":
        topics: dict[str, Any] = yaml.safe_load((config / "topics.yaml").read_text(encoding="utf-8")) or {}
        courses: dict[str, Any] = yaml.safe_load((config / "courses.yaml").read_text(encoding="utf-8")) or {}
        order: list[str] = topics.get("foundations") or []
        homes: dict[str, list[str]] = courses.get("homes") or {}
        return cls([str(p) for p in order], {str(k).lower(): [str(t) for t in v] for k, v in homes.items()})


def note_fields(note: dict[str, Any]) -> dict[str, str]:
    items: list[dict[str, Any]] = note.get("fields") or []
    return {str(f["name"]): str(f["value"]) for f in items}


def note_tags(note: dict[str, Any]) -> list[str]:
    tags: list[Any] = note.get("tags") or []
    return [str(t) for t in tags]


def under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "::")


def course_codes(tags: list[str]) -> set[str]:
    return {t.split("::")[1] for t in tags if t.startswith("course::")}


def rank(f: Foundations, tags: list[str]) -> tuple[int, int]:
    """Lower is more fundamental: the most fundamental discipline among the card's topics, then whether one of its
    courses is that discipline's home (MATH 309 for linear algebra beats Calc III or statics using it)."""
    best, best_topic = len(f.order), None
    for topic in (t.removeprefix("topic::") for t in tags if t.startswith("topic::")):
        i = next((n for n, p in enumerate(f.order) if under(topic, p)), len(f.order))
        if i < best:
            best, best_topic = i, topic
    if best_topic is None:
        return best, 1
    home = any(under(best_topic, h) for c in course_codes(tags) for h in f.homes.get(c, []))
    return best, 0 if home else 1


def merges_up(f: Foundations, new_tags: list[str], old_tags: list[str]) -> bool:
    """The new card should give the wording: it comes from another course and is strictly more fundamental."""
    new = course_codes(new_tags)
    if new and new <= course_codes(old_tags):
        return False                          # the same course carding it again: an ordinary duplicate
    return rank(f, new_tags) < rank(f, old_tags)


async def targets(core: Core, f: Foundations, found: list[tuple[CardIn, Match]]) -> dict[str, tuple[int, bool]]:
    """content_hash → (the Anki note a duplicate or flagged card matched, whether it should merge up into it).
    A merge-up needs the same note type, since it rewrites that note's fields in place."""
    out: dict[str, tuple[int, bool]] = {}
    notes: dict[int, dict[str, Any]] = {}
    for card, match in found:
        if match.kind not in ("duplicate", "flag") or match.note_id is None or match.note_id < 0:
            continue
        if match.note_id not in notes:
            notes[match.note_id] = await core.call("anki_notes_get", {"note_id": match.note_id})
        note = notes[match.note_id]
        up = note.get("note_type_name") == card.note_type and merges_up(f, card.tags, note_tags(note))
        out[content_hash(card)] = (match.note_id, up)
    return out


def home_deck(card: dict[str, Any], decks: set[str]) -> str:
    summary: dict[str, Any] = card.get("review_summary") or {}
    name = str(summary.get("original_deck") or card.get("deck_name") or "Core")
    top = name.split("::")[0]
    return name if name in decks else top if top in decks else "Core"


async def rewrite_note(con: sqlite3.Connection, core: Core, *, mid: str, deck: str, note_type: str,
                       fields: dict[str, str], by: str, note: str, add_tags: list[str] | None = None) -> dict[str, Any]:
    """Edit a note in place: through the Ledger when mnemosyne wrote it (the next write upserts it), else a direct
    upsert on its MnemosyneID. anki-core keeps the note's deck, scheduling, and history, and only ever adds tags.
    `fields` is the complete field set after the edit."""
    draft = store.get_draft(con, mid)
    if draft is not None:
        before: dict[str, Any] = {k: draft.fields.get(k, "") for k, v in fields.items() if draft.fields.get(k, "") != v}
        tags: list[str] = list(dict.fromkeys([*draft.tags, *(add_tags or [])]))
        if tags != draft.tags:
            before["tags"] = draft.tags
        store.revise(con, store.CardDecision(mid, approve=True, note=note, fields={**draft.fields, **fields},
                                             tags=tags if "tags" in before else None, before=before), by)
        return {"rewrite": "ledger", "mnemosyne_id": mid}
    notes: list[dict[str, Any]] = [{"deck": deck, "note_type": note_type, "fields": fields, "tags": add_tags or []}]
    key = "mn-rewrite-" + hashlib.sha256(json.dumps(notes, sort_keys=True).encode()).hexdigest()[:40]
    receipt = await core.upsert(notes, key)
    return {"rewrite": "upsert", "mnemosyne_id": mid, "result": receipt.get("result")}


async def merge(con: sqlite3.Connection, core: Core, decks: set[str], draft: store.Draft, up: bool, by: str,
                note: str) -> dict[str, Any]:
    """Settle a held card into the note dedupe matched. Merge: its tags join that note. Merge up: that note is also
    rewritten in this card's wording. Returns what happened, and whether the write job must run."""
    if draft.status != "pending" or draft.merge_into is None:
        raise ValueError("only a waiting card that dedupe matched to a note can be merged")
    if up and not draft.merge_up:
        raise ValueError("this card isn't the more fundamental framing of that note; merge it instead")
    nid = draft.merge_into
    target = await core.call("anki_notes_get", {"note_id": nid})
    have = note_fields(target)
    mid = have.get("MnemosyneID")
    if not mid:
        raise ValueError(f"note {nid} has no MnemosyneID, so it can't be edited")
    tags = [t for t in draft.tags if t.startswith(CONNECTING)]
    write = store.get_draft(con, mid) is not None
    if up:
        if target.get("note_type_name") != draft.note_type:
            raise ValueError(f"note {nid} is a {target.get('note_type_name')}, this card a {draft.note_type}")
        card = await core.call("anki_cards_get", {"card_id": int((target.get("card_ids") or [0])[0])})
        mode = "mode::audio" if draft.mode == "audio" else None
        fields = {**have, **{k: "" for k in STALE_AUDIO if k in have},
                  **{k: v for k, v in draft.fields.items() if k not in PIPELINE_OWNED}}
        done = await rewrite_note(con, core, mid=mid, deck=home_deck(card, decks), note_type=draft.note_type,
                                  fields=fields, by=by, note=f"merged up from {draft.mnemosyne_id}: {note}".strip(),
                                  add_tags=[*tags, *([mode] if mode else [])])
        if mode is None and "mode::audio" in note_tags(target):
            await core.call("anki_notes_remove_tags", {"note_ids": [nid], "tags": ["mode::audio"],
                                                       "idempotency_key": f"mn-merge-mode-{draft.mnemosyne_id}"})
    elif write:
        old = store.get_draft(con, mid)
        assert old is not None
        new = list(dict.fromkeys([*old.tags, *tags]))
        done = {"tags": [t for t in new if t not in old.tags]}
        if done["tags"]:
            store.revise(con, store.CardDecision(mid, approve=True, note=f"merged from {draft.mnemosyne_id}", tags=new,
                                                 before={"tags": old.tags}), by)
        else:
            write = False
    else:
        await core.call("anki_notes_add_tags", {"note_ids": [nid], "tags": tags,
                                                "idempotency_key": f"mn-merge-{draft.mnemosyne_id}"})
        done = {"tags": tags}
    store.merge_draft(con, draft.mnemosyne_id, by, f"merged {'up ' if up else ''}into note {nid}" + (f": {note}" if note else ""))
    log.info("draft_merged", draft=draft.mnemosyne_id, note=nid, up=up)
    return {"merged_into": nid, "up": up, "write": write, **done}

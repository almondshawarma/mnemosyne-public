"""The review page (/review): per-card decisions on drafts waiting for Aman, and exam coverage.

Pure functions only. api.py owns the routes and the Ledger calls.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from mnemosyne.cards import PIPELINE_OWNED, CardIn, Schema
from mnemosyne.ledger.store import CardDecision, Draft

PAGE = "review.html"
PIPELINE_TAGS = ("gen::", "mode::", "status::")   # added by note_tags(); never edited by hand


class CardAction(BaseModel):
    mnemosyne_id: str
    action: Literal["approve", "edit", "reject", "merge", "merge_up"]   # merge: into the note dedupe matched
    note: str = Field(default="", max_length=2000)
    fields: dict[str, str] | None = None
    tags: list[str] | None = None
    deck: str | None = None


class CardActions(BaseModel):
    decisions: list[CardAction] = Field(min_length=1, max_length=200)
    by: str = "review-page"


class Revision(BaseModel):
    """An edit to a card that is already approved or written, e.g. a tag Aman asked for in a review note."""
    fields: dict[str, str] | None = None
    tags: list[str] | None = None
    deck: str | None = None
    note: str = Field(min_length=1, max_length=2000)   # why: every change to a live card leaves a reason
    by: str = "claude"


class Withdrawal(BaseModel):
    note: str = Field(default="", max_length=2000)
    by: str = "review-page"


class MapDecision(BaseModel):
    action: Literal["confirm", "reject"]
    lectures: dict[str, list[int]] | None = None   # exam title → lecture numbers, overriding the proposal


class Lectures(BaseModel):
    lectures: list[int] = Field(max_length=60)


class ExamDate(BaseModel):
    """A new day for an exam, and optionally a new time (HH:MM, Chicago); without one it keeps its time."""
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    by: str = "page"


def own_tags(tags: list[str]) -> list[str]:
    return [t for t in tags if not t.startswith(PIPELINE_TAGS)]


def resolve(draft: Draft, action: CardAction, schema: Schema) -> CardDecision | list[str]:
    """Turn one page action into a Ledger decision, or the problems that stop an edit.
    An edited card must still pass the same structural checks as a proposed one."""
    if action.action != "edit":
        return CardDecision(draft.mnemosyne_id, approve=action.action == "approve", note=action.note)
    edits = action.fields or {}
    if bad := sorted(k for k in edits if k not in draft.fields or k in PIPELINE_OWNED):
        return [f"can't edit {', '.join(bad)}"]
    fields = {**draft.fields, **edits}
    tags = action.tags if action.tags is not None else own_tags(draft.tags)
    deck = action.deck or draft.deck
    card = CardIn(note_type=draft.note_type, deck=deck, tags=tags, mode="audio" if draft.mode == "audio" else "visual",
                  fields={k: v for k, v in fields.items() if k not in PIPELINE_OWNED})
    if problems := schema.problems(card):
        return problems
    before: dict[str, Any] = {k: draft.fields[k] for k, v in edits.items() if draft.fields[k] != v}
    new_tags = list(dict.fromkeys([*tags, *(t for t in draft.tags if t.startswith(PIPELINE_TAGS))]))
    if new_tags != draft.tags:
        before["tags"] = draft.tags
    if deck != draft.deck:
        before["deck"] = draft.deck
    if not before:
        return CardDecision(draft.mnemosyne_id, approve=True, note=action.note)
    return CardDecision(draft.mnemosyne_id, approve=True, note=action.note, fields=fields,
                        tags=new_tags if "tags" in before else None, deck=deck if "deck" in before else None,
                        before=before)


LECTURE_TAG = re.compile(r"^course::([a-z]+\d+)::l(\d+)(?:-|$)")


def coverage(tag_lists: list[list[str]]) -> dict[str, dict[str, int]]:
    """{COURSE: {lecture: cards}} from course::<code>::lNN-… tags, matching what an exam deck search pulls in.
    A card tagged for two lectures counts under each."""
    out: dict[str, dict[str, int]] = {}
    for tags in tag_lists:
        for course, lec in {(m[1].upper(), str(int(m[2]))) for t in tags if (m := LECTURE_TAG.match(t))}:
            per = out.setdefault(course, {})
            per[lec] = per.get(lec, 0) + 1
    return out

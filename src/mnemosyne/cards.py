"""The card payload from SKILL.md: schema, structural checks, and how a draft becomes an Anki note.

Content rules (what makes a good card) live in SKILL.md. This module only checks form.
"""

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

PIPELINE_OWNED = frozenset({"MnemosyneID", "AudioFront", "AudioBack", "TargetAudio", "SoundAudio",
                            "ExampleAudio", "PromptAudio", "Video"})
SPEECH: dict[str, tuple[str, ...]] = {
    "M-Basic": ("SpeakFront", "SpeakBack"), "M-Estimate": ("SpeakQuestion", "SpeakAnswer"),
    "M-Error": ("SpeakSituation", "SpeakFix"), "M-Cloze": ("SpeakText",), "M-Lang": ("SpeakMeaning",),
}
TAG = re.compile(r"^(course|topic|principle|type|src|lang|ipa)(::[a-z0-9]+(-[a-z0-9]+)*)+$")


class Source(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: str = Field(min_length=1, max_length=40)
    course: str | None = None
    lecture: int | None = None
    ref: str | None = None
    link: str | None = None


class Check(BaseModel):
    model_config = ConfigDict(extra="ignore")
    quantity: str
    value: str
    tolerance: str


class CardIn(BaseModel):
    """One card as the skill emits it. Review-only keys (golden, why, question, key) are ignored."""

    model_config = ConfigDict(extra="ignore")
    note_type: str
    deck: str
    fields: dict[str, str]
    tags: list[str] = Field(default_factory=list[str])
    mode: Literal["audio", "visual"]
    checks: list[Check] = Field(default_factory=list[Check])
    confidence: float | None = None
    question: str | None = None   # something only Aman can answer; the card waits for his review


class Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: Source
    cards: list[CardIn] = Field(min_length=1, max_length=200)


class Schema:
    """Note types and decks the collection actually has, loaded from the repo."""

    def __init__(self, notetypes: dict[str, list[str]], decks: set[str]) -> None:
        self.notetypes = notetypes
        self.decks = decks

    @classmethod
    def load(cls, notetypes_dir: Path, decks_yaml: Path) -> "Schema":
        notetypes: dict[str, list[str]] = {}
        for path in sorted(notetypes_dir.glob("M-*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            notetypes[str(spec["name"])] = [str(f) for f in spec["fields"]]
        decks = yaml.safe_load(decks_yaml.read_text(encoding="utf-8"))["decks"]
        return cls(notetypes, {str(d) for d in decks})

    def problems(self, card: CardIn) -> list[str]:
        """Everything structurally wrong with a card; empty means it can become a note."""
        out: list[str] = []
        fields = self.notetypes.get(card.note_type)
        if fields is None:
            return [f"unknown note type {card.note_type}"]
        allowed = set(fields) - PIPELINE_OWNED
        if extra := set(card.fields) - allowed:
            out.append(f"unknown or pipeline-owned fields: {', '.join(sorted(extra))}")
        # The first field a card supplies itself (M-Sign's first, Video, is the pipeline's) can't be empty
        lead = next(f for f in fields if f not in PIPELINE_OWNED)
        if not card.fields.get(lead, "").strip():
            out.append(f"{lead} is empty")
        if card.deck not in self.decks:
            out.append(f"unknown deck {card.deck}")
        for name in SPEECH.get(card.note_type, ()):
            if bool(card.fields.get(name, "").strip()) != (card.mode == "audio"):
                out.append(f"{name} must be filled exactly when mode is audio")
            if "\\" in card.fields.get(name, ""):
                out.append(f"{name} must be plain speech, not TeX")
        for name, value in card.fields.items():
            if "$" in value:
                out.append(f"{name}: use \\( \\) for math, never $")
            if value.count("\\(") != value.count("\\)"):
                out.append(f"{name}: unbalanced \\( \\)")
        if bad := [t for t in card.tags if not TAG.match(t)]:
            out.append(f"bad tags: {', '.join(bad)}")
        if sum(t.startswith("type::") for t in card.tags) != 1:
            out.append("needs exactly one type:: tag")
        return out

    def note_fields(self, card: CardIn, mnemosyne_id: str) -> dict[str, str]:
        """Every field of the note type, with the card's values and its MnemosyneID."""
        return {name: card.fields.get(name, "") for name in self.notetypes[card.note_type]} | {
            "MnemosyneID": mnemosyne_id}


def note_tags(card: CardIn, probation: bool) -> list[str]:
    """The card's tags plus what the pipeline owns: gen::ai, mode::*, and status::probation."""
    extra = ["gen::ai", f"mode::{card.mode}"] + (["status::probation"] if probation else [])
    return list(dict.fromkeys([*card.tags, *extra]))


def content_hash(card: CardIn) -> str:
    """Identity of a card's content: same note type and fields means the same card."""
    body = json.dumps({"t": card.note_type, "f": {k: v.strip() for k, v in card.fields.items()}},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def payload_hash(payload: Payload) -> str:
    return hashlib.sha256(payload.model_dump_json().encode()).hexdigest()


def uuid7() -> str:
    """RFC 9562 UUIDv7: 48-bit ms timestamp, then random bits."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10))
    value = (ms << 80) | (0x7 << 76) | ((rand >> 64) & 0xFFF) << 64 | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return str(uuid.UUID(int=value))

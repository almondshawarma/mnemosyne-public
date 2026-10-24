"""Approval messages for Telegram. helios-telegram owns the bot; mnemosyne only builds messages and
handles the button presses it forwards (callback data `mn|<action>|<batch id>`)."""

import re
from dataclasses import dataclass, field

from mnemosyne.ledger.store import Batch, Draft

PREVIEW = 8
MATH = re.compile(r"\\[()\[\]]")


@dataclass
class Reply:
    toast: str
    text: str | None = None                 # replaces the batch message
    keyboard: list[list[dict[str, str]]] = field(default_factory=list[list[dict[str, str]]])
    messages: list[str] = field(default_factory=list[str])  # extra messages to send
    write: bool = False                     # the caller should run the write job


def readable(draft: Draft) -> str:
    """One line per card: the spoken form when there is one, else the fields without math delimiters."""
    f = draft.fields
    prompt = f.get("SpeakFront") or f.get("SpeakQuestion") or f.get("SpeakSituation") or ""
    answer = f.get("SpeakBack") or f.get("SpeakAnswer") or f.get("SpeakFix") or ""
    if not prompt:
        values = [v for k, v in f.items() if v and k not in ("MnemosyneID", "Source", "SourceLink")]
        prompt, answer = (values + ["", ""])[:2]
    line = f"{MATH.sub('', prompt).strip()} → {MATH.sub('', answer).strip()}"
    return line if len(line) <= 160 else line[:157] + "…"


def title(batch: Batch) -> str:
    where = " ".join(p for p in (batch.course, f"L{batch.lecture:02d}" if batch.lecture else None) if p)
    return f"{batch.size} card{'s' if batch.size != 1 else ''} · {where or batch.kind} ({batch.kind}, via {batch.origin})"


def keyboard(batch_id: str, drafts: list[Draft] | None = None) -> list[list[dict[str, str]]]:
    """Buttons only while something is waiting for Aman."""
    if drafts is not None and not any(d.status == "pending" for d in drafts):
        return []
    return [[{"text": "✅ Approve all", "callback_data": f"mn|a|{batch_id}"},
             {"text": "🗑 Reject all", "callback_data": f"mn|r|{batch_id}"}],
            [{"text": "📄 Show every card", "callback_data": f"mn|s|{batch_id}"}]]


def announcement(batch: Batch, drafts: list[Draft]) -> str:
    pending = [d for d in drafts if d.status == "pending"]
    added = len(drafts) - len(pending)
    lines = [f"🧠 Mnemosyne: {title(batch)}"]
    if added and pending:
        lines.append(f"✅ {added} added on probation")
    elif added:
        lines.append(f"✅ {added} added on probation. Nothing needs you.")
    if pending:
        if added:
            lines.append(f"👀 {len(pending)} need your review:")
        # The same for every card when no checks ran, so it's said once.
        unchecked = next((d.review_reason for d in pending if (d.review_reason or "").startswith("unchecked:")), None)
        if unchecked:
            lines.append(f"⚠ Not checked: {unchecked.removeprefix('unchecked: ')[:200]}")
        lines.append("")
        for i, d in enumerate(pending[:PREVIEW], 1):
            lines.append(f"{i}. {readable(d)}")
            if d.review_reason and not d.review_reason.startswith("unchecked:"):
                lines.append(f"   ↳ {d.review_reason[:160]}")
        if len(pending) > PREVIEW:
            lines.append(f"…and {len(pending) - PREVIEW} more")
    return "\n".join(lines)


def full_listing(drafts: list[Draft], limit: int = 3500) -> list[str]:
    pages: list[str] = []
    page = ""
    for i, d in enumerate(drafts, 1):
        line = f"{i}. [{d.note_type} · {d.deck}] {readable(d)}\n"
        if len(page) + len(line) > limit:
            pages.append(page)
            page = ""
        page += line
    return [*pages, page] if page else pages


def parse(data: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"mn\|([ars])\|([0-9a-f]{6})|mn\|([cx])\|([A-Z]{4}\d{3})", data)
    if match and match[3]:
        return match[3], match[4]
    return (match[1], match[2]) if match else None

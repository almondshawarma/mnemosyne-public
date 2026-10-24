"""Write approved drafts to Anki through anki-core. Re-running it writes nothing new (invariant 4)."""

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import structlog

from mnemosyne import audio
from mnemosyne.anki.core_client import Core, describe
from mnemosyne.ledger import store

log = structlog.get_logger()
CHUNK = 50  # anki-core's ANKI_MAX_BATCH_SIZE default
NOTE_LEVEL = ("DUPLICATE_NOTE", "INVALID_ARGUMENT")


def chunk_key(notes: list[dict[str, Any]]) -> str:
    """Same notes, same key: anki-core then replays its receipt instead of writing twice."""
    body = json.dumps(notes, sort_keys=True, ensure_ascii=False)
    return "mn-" + hashlib.sha256(body.encode()).hexdigest()[:48]


def note_level(error: str) -> bool:
    """Errors caused by one note in the chunk (anki-core refuses the whole call for it), not by the connection."""
    return any(code in error for code in NOTE_LEVEL)


async def write_part(con: sqlite3.Connection, core: Core, part: list[store.Draft], summary: Counter[str]) -> None:
    notes = [{"deck": d.deck, "note_type": d.note_type, "fields": d.fields, "tags": d.tags} for d in part]
    try:
        receipt = await core.upsert(notes, chunk_key(notes))
    except Exception as exc:  # noqa: BLE001 -- any failure is recorded; drafts stay approved and get retried
        error = describe(exc)
        if len(part) > 1 and note_level(error):
            # One bad note (say, a front that duplicates an existing note) must not hold back the others:
            # halve until the culprit writes alone and fails alone.
            half = len(part) // 2
            await write_part(con, core, part[:half], summary)
            await write_part(con, core, part[half:], summary)
            return
        store.mark_error(con, [d.mnemosyne_id for d in part], error)
        summary["errors"] += len(part)
        log.warning("write_failed", count=len(part), error=error[:300],
                    first_field=next(iter(part[0].fields.values()), "")[:80] if len(part) == 1 else None)
        return
    result: dict[str, Any] = receipt.get("result") or {}
    store.mark_written(con, {str(n["key"]): int(n["note_id"]) for n in result.get("notes", [])})
    for action in ("created", "updated", "unchanged"):
        summary[action] += int(result.get(action, 0))
    if not receipt.get("remote_synced"):
        summary["sync_pending"] += len(part)


async def write_approved(con: sqlite3.Connection, core: Core, media: list[Path] | None = None) -> dict[str, int]:
    drafts = store.approved_unwritten(con)
    if media and any(d.note_type in audio.ATTACH for d in drafts):
        try:
            drafts = await audio.attach(con, core, drafts, media)
        except Exception as exc:  # noqa: BLE001 -- cards needing audio wait for the next write; the rest go now
            log.warning("audio_attach_failed", error=str(exc)[:300])
            drafts = [d for d in drafts if d.note_type not in audio.ATTACH]
    summary: Counter[str] = Counter()
    for start in range(0, len(drafts), CHUNK):
        await write_part(con, core, drafts[start:start + CHUNK], summary)
    out = {"drafts": len(drafts), **summary}
    log.info("write_approved", **out)
    return out

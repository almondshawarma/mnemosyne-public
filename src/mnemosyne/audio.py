"""Media the pipeline owns, attached to cards as they're written (CLAUDE.md: generated media use the `mn_` prefix).

- **IPA recordings** in data/audio/ipa/ (Wikimedia Commons, scripts/ipa_audio.py): an M-IPA card's `SoundAudio`,
  found by its Symbol. They're small, so they're committed.
- **ASL sign videos** in video/asl/ under the media folder on the Obelisk (ASL Signbank, scripts/asl_videos.py): an
  M-Sign card's `Video`, found by its Gloss. They're too big for git.

Each folder has a manifest.json mapping a key to its file and attribution. Media roots are searched in order (the
Obelisk's media folder, then the repo's data/). A file is stored in the collection through anki-core once, and again
only if it changes (`media_file`). Card payloads never carry these fields (`PIPELINE_OWNED`). A card with no file is
written without one, and the card that needs it (sound → symbol, sign → meaning) then simply doesn't exist.
"""

import base64
import hashlib
import html
import json
import re
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import structlog

from mnemosyne.anki.core_client import Core
from mnemosyne.ledger import store
from mnemosyne.ledger.db import now

log = structlog.get_logger()

# note type → (field that names the media, field to fill, folder under a media root)
ATTACH = {"M-IPA": ("Symbol", "SoundAudio", "audio/ipa"), "M-Sign": ("Gloss", "Video", "video/asl")}


def key_of(field: str) -> str:
    """'<span class="ipa">[ʈ]</span>' → 'ʈ', 'Thank you' → 'thankyou': no markup, brackets, slashes, or spaces,
    and case-folded (IPA symbols have no case to lose)."""
    return re.sub(r"[\s\[\]/]", "", html.unescape(re.sub(r"<[^>]+>", "", field))).casefold()


def manifest(roots: list[Path], folder: str) -> dict[str, dict[str, Any]]:
    """key → entry (with `path` added), from the first root that has the folder."""
    for root in roots:
        path = root / folder / "manifest.json"
        if path.exists():
            raw: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
            return {key_of(k): {**v, "path": str(path.parent / v["file"])} for k, v in raw.items()}
    return {}


def find(roots: list[Path], note_type: str, key: str) -> Path | None:
    """The file a card of this note type would get, by its Symbol or Gloss: what the review page plays before the card
    is approved, so Aman can check the recording or video is the right one."""
    spec = ATTACH.get(note_type)
    entry = manifest(roots, spec[2]).get(key_of(key)) if spec else None
    path = Path(entry["path"]) if entry else None
    return path if path is not None and path.exists() else None


def source(entry: dict[str, Any]) -> str:
    """The attribution the license needs, for the card's Source field."""
    if entry.get("source", "").startswith("File:"):
        return f"Audio: Wikimedia Commons, {entry['source'].removeprefix('File:')} ({entry['author']}, {entry['license']})"
    return f"Video: {entry['source']} ({entry['author']}, {entry['license']})"


async def store_once(con: sqlite3.Connection, core: Core, path: Path) -> str:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    row = con.execute("SELECT sha256 FROM media_file WHERE filename = ?", (path.name,)).fetchone()
    if row is None or row["sha256"] != sha:
        await core.call("anki_media_store", {"filename": path.name,
                                             "content_base64": base64.b64encode(data).decode("ascii"),
                                             "idempotency_key": f"mn-media-{sha[:40]}"})
        con.execute("INSERT INTO media_file (filename, sha256, stored_at) VALUES (?, ?, ?) ON CONFLICT (filename)"
                    " DO UPDATE SET sha256 = excluded.sha256, stored_at = excluded.stored_at", (path.name, sha, now()))
        log.info("media_stored", filename=path.name, bytes=len(data))
    return path.name


async def attach(con: sqlite3.Connection, core: Core, drafts: list[store.Draft],
                 roots: list[Path]) -> list[store.Draft]:
    """Fill the media field of each draft that has a file, storing the file first. Returns the drafts as they
    should be written."""
    out: list[store.Draft] = []
    manifests: dict[str, dict[str, dict[str, Any]]] = {}
    for d in drafts:
        spec = ATTACH.get(d.note_type)
        if spec is None or d.fields.get(spec[1], "").strip():
            out.append(d)
            continue
        key_field, media_field, folder = spec
        entry = manifests.setdefault(folder, manifest(roots, folder)).get(key_of(d.fields.get(key_field, "")))
        path = Path(entry["path"]) if entry else None
        if path is None or not path.exists():
            out.append(d)
            continue
        filename = await store_once(con, core, path)
        fields = {**d.fields, media_field: f"[sound:{filename}]"}
        store.set_fields(con, d.mnemosyne_id, fields)
        out.append(replace(d, fields=fields))
    return out

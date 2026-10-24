"""Near-duplicate detection against the whole collection (PLAN §4.7): cosine similarity of prompt embeddings.

Against the collection, ≥ 0.92 is a duplicate and is dropped, and 0.88–0.92 is flagged for Aman's review. Within one
batch only near-identical prompts (≥ 0.97) count: the canon wants several cards on one idea (canon 15), and those
score 0.85–0.9 without being duplicates. Thresholds tuned on the first real batches (2026-09-25): the plan's 0.85
flagged same-topic cards ("net force at the center of a ring" vs "the integral to set up"). Existing notes are embedded
once and cached in the Ledger (note_embedding), keyed on the text, so edits re-embed.
"""

import hashlib
import html
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
import numpy as np
from numpy.typing import NDArray

from mnemosyne.anki.core_client import Core
from mnemosyne.ledger.db import now

DUPLICATE, FLAG, SAME_BATCH = 0.92, 0.88, 0.97
Kind = Literal["duplicate", "flag", "new"]


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(self, url: str = "http://ollama:11434", model: str = "nomic-embed-text") -> None:
        self.url, self.model = url.rstrip("/"), model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=120) as http:
            for start in range(0, len(texts), 64):
                batch = [f"clustering: {t}" for t in texts[start:start + 64]]  # nomic's symmetric-task prefix
                resp = await http.post(f"{self.url}/api/embed", json={"model": self.model, "input": batch})
                resp.raise_for_status()
                out += resp.json()["embeddings"]
        return out


@dataclass(frozen=True)
class Match:
    kind: Kind
    score: float
    note_id: int | None
    text: str


def normalize(field: str) -> str:
    """What a prompt says, without HTML, math delimiters, or spacing differences."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", field))
    text = re.sub(r"\\[()\[\]]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def existing_notes(core: Core) -> dict[int, str]:
    """note id → normalized first field, for the whole collection (paged through anki-core)."""
    notes: dict[int, str] = {}
    offset = 0
    while True:
        page = await core.call("anki_notes_search", {"query": "deck:*", "offset": offset, "limit": 100})
        for item in page["items"]:
            notes[int(item["id"])] = normalize(str(item["first_field"]))
        if not page.get("has_more"):
            return notes
        offset += 100


async def index(con: sqlite3.Connection, notes: dict[int, str], embedder: Embedder) -> tuple[list[int], NDArray[np.float32]]:
    """Unit vectors for every note, embedding only what's new or changed since the last run."""
    cached = {int(r["note_id"]): (r["text_hash"], r["vector"]) for r in con.execute("SELECT * FROM note_embedding")}
    todo = [(nid, text) for nid, text in notes.items() if text and cached.get(nid, ("",))[0] != _hash(text)]
    if todo:
        vectors = await embedder.embed([t for _, t in todo])
        stamp = now()
        con.execute("BEGIN IMMEDIATE")
        for (nid, text), vec in zip(todo, vectors, strict=True):
            blob = np.asarray(vec, dtype=np.float32).tobytes()
            con.execute("INSERT OR REPLACE INTO note_embedding VALUES (?, ?, ?, ?)", (nid, _hash(text), blob, stamp))
            cached[nid] = (_hash(text), blob)
        con.execute("COMMIT")
    ids = [nid for nid in notes if nid in cached and notes[nid]]
    if not ids:
        return [], np.zeros((0, 1), dtype=np.float32)
    matrix = np.stack([np.frombuffer(cached[nid][1], dtype=np.float32) for nid in ids])
    return ids, matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


UNAVAILABLE = "similarity check unavailable (the embedding model is down): only exact repeats were caught"


def exact(prompts: list[str], notes: dict[int, str]) -> list[Match]:
    """When the embedding model is down: exact repeats (after normalizing) are still duplicates, and everything else
    is flagged, so it waits for Aman instead of going in unchecked."""
    by_text = {t: nid for nid, t in notes.items() if t}
    seen: set[str] = set()
    out: list[Match] = []
    for p in prompts:
        t = normalize(p)
        if t in by_text:
            out.append(Match("duplicate", 1.0, by_text[t], t))
        elif t in seen:
            out.append(Match("duplicate", 1.0, None, t))
        else:
            out.append(Match("flag", 0.0, None, UNAVAILABLE))
        seen.add(t)
    return out


async def classify(prompts: list[str], ids: list[int], matrix: NDArray[np.float32], notes: dict[int, str],
                   embedder: Embedder) -> list[Match]:
    """Compare each new prompt with the collection and with the prompts before it in the same batch."""
    texts = [normalize(p) for p in prompts]
    if not texts:
        return []
    new = np.asarray(await embedder.embed(texts), dtype=np.float32)
    new = new / np.linalg.norm(new, axis=1, keepdims=True)
    out: list[Match] = []
    for i, vec in enumerate(new):
        best, best_id, best_text = 0.0, None, ""
        if len(ids):
            sims = matrix @ vec
            j = int(np.argmax(sims))
            best, best_id, best_text = float(sims[j]), ids[j], notes[ids[j]]
        kind: Kind = "duplicate" if best >= DUPLICATE else "flag" if best >= FLAG else "new"
        if i and kind != "duplicate":
            within = new[:i] @ vec
            k = int(np.argmax(within))
            if float(within[k]) >= SAME_BATCH:
                kind, best, best_id, best_text = "duplicate", float(within[k]), None, texts[k]
        out.append(Match(kind, best, best_id, best_text))
    return out

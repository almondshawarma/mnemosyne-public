"""Near-duplicate detection against the whole collection (PLAN §4.7): cosine similarity of whole-card embeddings.

What's compared is the whole card (neighbors.note_text: what it asks, what it answers, its topics), not the first
field. E1 (docs/embeddings.md, 2026-10-29) showed first-field scores separated the cards Aman rejected as repeats from
the ones he kept no better than chance (AUC 0.54): they can't see the one token that makes a fact distinct (±1σ vs ±2σ,
n_e→0 vs T_e→0), and every IPA glyph scored 1.00 against घ. Whole-card text lifts that to 0.72 with the same model.

Against the collection, ≥ 0.93 is a duplicate and ≥ 0.90 is flagged for Aman's review. At 0.90 the flag rate on kept
lecture cards stays near the old one (~10%), and ≥ 0.93 drops about 2% of them; no threshold is precise, so every drop
is recorded with its card (dedupe_match) and the weekly audit reviews the week's drops, restoring any that weren't
repeats. Within one batch only near-identical cards (≥ 0.97) count: the canon wants several cards on one idea (canon
15). Existing notes are embedded once and cached in the Ledger (note_embedding), keyed on the text, so edits re-embed.
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

from mnemosyne import neighbors
from mnemosyne.anki.core_client import Core
from mnemosyne.cards import CardIn
from mnemosyne.ledger.db import now

DUPLICATE, FLAG, SAME_BATCH = 0.93, 0.90, 0.97
REPR = "full"   # what dedupe_match records alongside each score; the model is `embeddings.dedupe_model` in llm.yaml
Kind = Literal["duplicate", "flag", "new"]


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(self, url: str = "http://ollama:11434", model: str = "nomic-embed-text") -> None:
        self.url, self.model = url.rstrip("/"), model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        pre = neighbors.prompt(self.model, "same")   # the model's symmetric-task prompt (nomic: "clustering: ")
        async with httpx.AsyncClient(timeout=120) as http:
            for start in range(0, len(texts), 64):
                batch = [pre + t for t in texts[start:start + 64]]
                resp = await http.post(f"{self.url}/api/embed", json={"model": self.model, "input": batch})
                resp.raise_for_status()
                out += resp.json()["embeddings"]
        return out


def model_of(embedder: Embedder) -> str:
    return str(getattr(embedder, "model", ""))


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


def _hash(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}{text}".encode()).hexdigest()[:16]


def card_text(card: CardIn) -> str:
    """What a new card is compared by: the same whole-card text as the notes."""
    return neighbors.note_text(card.note_type, card.fields, card.tags)


async def existing_notes(core: Core, con: sqlite3.Connection) -> dict[int, str]:
    """note id → whole-card text, for the whole collection (cached in the Ledger, refreshed incrementally)."""
    return {nid: neighbors.note_text(n.note_type, n.fields, n.tags) for nid, n in (await neighbors.fetch(con, core)).items()}


async def index(con: sqlite3.Connection, notes: dict[int, str], embedder: Embedder) -> tuple[list[int], NDArray[np.float32]]:
    """Unit vectors for every note, embedding only what's new or changed since the last run."""
    # Keyed on the model as well as the text, so changing `embeddings.dedupe_model` re-embeds everything instead of
    # comparing new vectors with old ones.
    model = model_of(embedder)
    cached = {int(r["note_id"]): (r["text_hash"], r["vector"]) for r in con.execute("SELECT * FROM note_embedding")}
    todo = [(nid, text) for nid, text in notes.items() if text and cached.get(nid, ("",))[0] != _hash(model, text)]
    if todo:
        vectors = await embedder.embed([t for _, t in todo])
        stamp = now()
        con.execute("BEGIN IMMEDIATE")
        for (nid, text), vec in zip(todo, vectors, strict=True):
            blob = np.asarray(vec, dtype=np.float32).tobytes()
            con.execute("INSERT OR REPLACE INTO note_embedding VALUES (?, ?, ?, ?)", (nid, _hash(model, text), blob, stamp))
            cached[nid] = (_hash(model, text), blob)
        con.execute("COMMIT")
    ids = [nid for nid in notes if nid in cached and notes[nid]]
    if not ids:
        return [], np.zeros((0, 1), dtype=np.float32)
    matrix = np.stack([np.frombuffer(cached[nid][1], dtype=np.float32) for nid in ids])
    return ids, matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


UNAVAILABLE = "similarity check unavailable (the embedding model is down): only exact repeats were caught"


def prompt_of(text: str) -> str:
    """The prompt part of a whole-card text (neighbors.note_text joins fields with " | " and ends with the topics)."""
    return normalize(re.sub(r" \(topics: [^)]*\)$", "", text).split(" | ")[0])


def exact(texts: list[str], notes: dict[int, str]) -> list[Match]:
    """When the embedding model is down: a card asking exactly what a note asks (after normalizing) is still a
    duplicate, and everything else is flagged, so it waits for Aman instead of going in unchecked."""
    by_prompt = {prompt_of(t): nid for nid, t in notes.items() if t}
    seen: set[str] = set()
    out: list[Match] = []
    for text in texts:
        p = prompt_of(text)
        if p in by_prompt:
            out.append(Match("duplicate", 1.0, by_prompt[p], notes[by_prompt[p]]))
        elif p in seen:
            out.append(Match("duplicate", 1.0, None, text))
        else:
            out.append(Match("flag", 0.0, None, UNAVAILABLE))
        seen.add(p)
    return out


async def classify(texts: list[str], ids: list[int], matrix: NDArray[np.float32], notes: dict[int, str],
                   embedder: Embedder) -> list[Match]:
    """Compare each new card (card_text) with the collection and with the cards before it in the same batch."""
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

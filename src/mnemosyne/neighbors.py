"""What a card means, as a vector: the text each note is embedded from, and the prompts each embedding model wants
(docs/embeddings.md).

Dedupe embeds only a note's first field. That misses the one token that makes a fact distinct (±1σ vs ±2σ, n_e→0 vs
T_e→0) and collapses glyph-only prompts (every IPA symbol scored 1.00 against घ). `note_text` embeds the whole idea
instead: what the card asks, what it answers, and the topics it belongs to. Media-only fields drop out, so an IPA or
ASL note is embedded from its description or gloss.
"""

import hashlib
import html
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
import numpy as np
import structlog
import yaml
from numpy.typing import NDArray

from mnemosyne import learner
from mnemosyne.anki.core_client import Caller, Core, reads
from mnemosyne.ledger.db import now

log = structlog.get_logger()

# The fields that carry meaning, by note type, prompt first. Speak*, Audio*, Video, Source*, and MnemosyneID never do.
CONTENT: dict[str, tuple[str, ...]] = {
    "M-Basic": ("Front", "Back", "Elaboration"),
    "M-Cloze": ("Text", "Extra"),
    "M-Error": ("Situation", "Trap", "Fix", "Why"),
    "M-Estimate": ("Question", "Answer", "Anchor"),
    "M-IPA": ("Description", "ExampleWord", "Symbol"),
    "M-Lang": ("Target", "Meaning", "Notes"),
    "M-Sign": ("Gloss", "Notes"),
    "M-Ear": ("Answer",),
}
SKIP = re.compile(r"^(Speak|Audio)|^(MnemosyneID|Source|SourceLink|Video|SoundAudio|ExampleAudio|PromptAudio|"
                  r"TargetAudio|Reverse|Produce|Read|Lang)$")
CLOZE = re.compile(r"\{\{c\d+::(.*?)(?:::[^}]*)?\}\}", re.DOTALL)
TEX_COMMAND = re.compile(r"\\([A-Za-z]+)")
LIMIT = 1500   # characters; every model here takes far more, and a card's meaning is in its first lines

Repr = Literal["front", "full", "texwords"]


def clean(value: str) -> str:
    """Field HTML as plain text: no tags, media, or MathJax delimiters; a cloze shows its answer."""
    value = re.sub(r"\[sound:[^\]]*\]", " ", value)
    value = re.sub(r"<img[^>]*>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<br\s*/?>|</(?:div|p|li)>", " ", value, flags=re.IGNORECASE)
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = CLOZE.sub(r"\1", value)
    value = re.sub(r"\\[()\[\]]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def texwords(text: str) -> str:
    """TeX read as words, for models that tokenize backslash commands badly: \\lambda_D → lambda_D,
    \\frac{a}{b} → frac a b."""
    text = TEX_COMMAND.sub(r" \1 ", text)
    return re.sub(r"\s+", " ", re.sub(r"[{}]", " ", text)).strip()


def topics(tags: Iterable[str]) -> list[str]:
    """Topic and course tags as words: topic::physics::em::gauss-law → "physics em gauss law"."""
    out: list[str] = []
    for tag in tags:
        head, _, rest = tag.partition("::")
        if head in ("topic", "principle") and rest:
            out.append(rest.replace("::", " ").replace("-", " ").replace("_", " "))
        elif head == "course" and rest:
            out.append(rest.split("::")[0].upper())
    return list(dict.fromkeys(out))


def content(note_type: str, fields: Mapping[str, str]) -> list[str]:
    names = CONTENT.get(note_type) or tuple(k for k in fields if not SKIP.search(k))
    return [t for t in (clean(fields.get(n, "")) for n in names) if t]


def note_text(note_type: str, fields: Mapping[str, str], tags: Iterable[str], repr: Repr = "full") -> str:
    """The text a note is embedded from. "front" is dedupe's (the first field alone); "full" is the whole idea;
    "texwords" is "full" with TeX read as words."""
    if repr == "front":
        return clean(next(iter(fields.values()), "")).lower()
    parts = content(note_type, fields)
    about = topics(tags)
    text = " | ".join(parts) + (f" (topics: {'; '.join(about)})" if about else "")
    text = texwords(text) if repr == "texwords" else text
    return text[:LIMIT]


# Each model's prompt for a symmetric comparison (dedupe), a query, and a document (search). Model IDs live in config;
# these are keyed on the model family, which is what decides the prompt.
Role = Literal["same", "query", "doc"]
PROMPTS: dict[str, dict[Role, str]] = {
    "nomic-embed-text": {"same": "clustering: ", "query": "search_query: ", "doc": "search_document: "},
    "qwen3-embedding": {"same": "", "query": "Instruct: Given a study question, retrieve the flashcards that answer "
                        "it\nQuery: ", "doc": ""},
    "embeddinggemma": {"same": "task: sentence similarity | query: ", "query": "task: search result | query: ",
                       "doc": "title: none | text: "},
    "bge-m3": {"same": "", "query": "", "doc": ""},
}


def prompt(model: str, role: Role) -> str:
    family = model.split(":")[0]
    return PROMPTS.get(family, {}).get(role, "")


# ---------- the index (Ledger note_vector) and the search over it ----------

@dataclass(frozen=True)
class Config:
    """`embeddings` in config/llm.yaml. Off unless `neighbors: true`: the endpoint refuses and 04:15 skips the index."""
    enabled: bool = False
    model: str = "nomic-embed-text"
    reranker: str | None = None
    pool: int = 25                 # candidates the reranker re-scores
    dedupe_model: str = "nomic-embed-text"   # verify/dedupe.py's thresholds are calibrated for this one

    @classmethod
    def load(cls, path: Path) -> "Config":
        doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw: dict[str, Any] = doc.get("embeddings") or {}
        reranker = raw.get("reranker")
        return cls(bool(raw.get("neighbors", False)), str(raw.get("model", cls.model)),
                   str(reranker) if reranker else None, int(raw.get("pool", cls.pool)),
                   str(raw.get("dedupe_model", cls.dedupe_model)))


@dataclass(frozen=True)
class Note:
    note_type: str
    fields: dict[str, str]
    tags: list[str]
    card_ids: list[int]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def embed(url: str, model: str, texts: list[str], role: Role) -> NDArray[np.float32]:
    """Unit vectors from Ollama, with the model's prompt for this role."""
    out: list[list[float]] = []
    pre = prompt(model, role)
    async with httpx.AsyncClient(timeout=300) as http:
        for start in range(0, len(texts), 32):
            batch = [pre + (t or "(empty)") for t in texts[start:start + 32]]
            resp = await http.post(f"{url.rstrip('/')}/api/embed", json={"model": model, "input": batch})
            resp.raise_for_status()
            out += resp.json()["embeddings"]
    m = np.asarray(out, dtype=np.float32).reshape(len(texts), -1)
    return (m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)).astype(np.float32)


FULL_EVERY = timedelta(days=7)   # read every note again weekly, in case an edit slipped past `edited:`


async def _note_ids(c: Caller, query: str) -> list[int]:
    ids: list[int] = []
    offset = 0
    while True:
        page = await c.call("anki_notes_search", {"query": query, "offset": offset, "limit": 100})
        ids += [int(i["id"]) for i in page["items"]]
        if not page.get("has_more"):
            return ids
        offset += 100


async def fetch(con: sqlite3.Connection, core: Core) -> dict[int, Note]:
    """Every note with all its fields, from the Ledger's note_text cache. Only new notes and the ones Anki says were
    edited since the last look are read again (a full read is ~15 s; this is a page of ids plus a few reads), and
    deleted notes leave the cache. Every note is re-read once a week."""
    sync = con.execute("SELECT checked_at, full_at FROM note_text_sync WHERE id = 1").fetchone()
    stamp = datetime.now(UTC)
    checked = datetime.fromisoformat(str(sync[0])) if sync is not None else None
    full = sync is None or stamp - datetime.fromisoformat(str(sync[1])) > FULL_EVERY
    cached = {int(r[0]) for r in con.execute("SELECT note_id FROM note_text")}
    async with reads(core) as c:
        ids = await _note_ids(c, "deck:*")
        if full or checked is None:
            stale = set(ids)
        else:
            days = (stamp - checked).days + 2   # edited:n counts whole days back
            stale = {n for n in ids if n not in cached} | set(await _note_ids(c, f"edited:{days}"))
        got: dict[int, Note] = {}
        for nid in sorted(stale & set(ids)):
            n = await c.call("anki_notes_get", {"note_id": nid})
            fields: list[dict[str, Any]] = n.get("fields") or []
            tags: list[Any] = n.get("tags") or []
            cards: list[Any] = n.get("card_ids") or []
            got[nid] = Note(str(n.get("note_type_name") or ""), {str(f["name"]): str(f["value"]) for f in fields},
                            [str(t) for t in tags], [int(x) for x in cards])
    gone = cached - set(ids)
    when = stamp.isoformat(timespec="seconds")
    con.execute("BEGIN IMMEDIATE")
    con.executemany("INSERT OR REPLACE INTO note_text VALUES (?, ?, ?, ?, ?, ?)",
                    [(nid, n.note_type, json.dumps(n.fields, ensure_ascii=False), json.dumps(n.tags),
                      json.dumps(n.card_ids), when) for nid, n in got.items()])
    con.executemany("DELETE FROM note_text WHERE note_id = ?", [(n,) for n in gone])
    con.execute("INSERT INTO note_text_sync VALUES (1, ?, ?) ON CONFLICT (id) DO UPDATE SET checked_at = excluded.checked_at,"
                " full_at = CASE WHEN ? THEN excluded.full_at ELSE note_text_sync.full_at END", (when, when, full))
    con.execute("COMMIT")
    return {int(r[0]): Note(str(r[1]), json.loads(r[2]), json.loads(r[3]), json.loads(r[4]))
            for r in con.execute("SELECT note_id, note_type, fields_json, tags_json, card_ids FROM note_text")}


async def refresh(con: sqlite3.Connection, core: Core, url: str, model: str, repr: Repr = "full") -> dict[str, int]:
    """Bring the index up to date: embed new and edited notes, drop deleted ones. Idempotent; reads the collection only."""
    notes = await fetch(con, core)
    stored = {int(r[0]): (str(r[1]), str(r[2])) for r in con.execute(
        "SELECT note_id, text_hash, card_ids FROM note_vector WHERE model = ? AND repr = ?", (model, repr))}
    texts = {nid: note_text(n.note_type, n.fields, n.tags, repr) for nid, n in notes.items()}
    todo = [nid for nid in notes if stored.get(nid, ("", ""))[0] != _hash(texts[nid])]
    moved = [nid for nid in notes if nid not in todo and nid in stored
             and json.loads(stored[nid][1]) != notes[nid].card_ids]
    gone = [nid for nid in stored if nid not in notes]
    vectors = await embed(url, model, [texts[n] for n in todo], "doc") if todo else None
    stamp = now()
    con.execute("BEGIN IMMEDIATE")
    for i, nid in enumerate(todo):
        assert vectors is not None
        con.execute("INSERT OR REPLACE INTO note_vector VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (nid, model, repr, _hash(texts[nid]), texts[nid], json.dumps(notes[nid].card_ids),
                     vectors[i].tobytes(), stamp))
    for nid in moved:
        con.execute("UPDATE note_vector SET card_ids = ? WHERE note_id = ? AND model = ? AND repr = ?",
                    (json.dumps(notes[nid].card_ids), nid, model, repr))
    con.executemany("DELETE FROM note_vector WHERE note_id = ? AND model = ? AND repr = ?",
                    [(nid, model, repr) for nid in gone])
    con.execute("COMMIT")
    summary = {"created": sum(n not in stored for n in todo), "updated": sum(n in stored for n in todo),
               "skipped": len(notes) - len(todo), "rejected": 0, "pruned": len(gone)}
    log.info("neighbors_refreshed", model=model, repr=repr, **summary)
    return summary


@dataclass(frozen=True)
class Index:
    note_ids: list[int]
    card_ids: list[list[int]]
    texts: list[str]
    matrix: NDArray[np.float32]

    def by_card(self) -> dict[int, int]:
        return {cid: i for i, cids in enumerate(self.card_ids) for cid in cids}


def load(con: sqlite3.Connection, model: str, repr: Repr = "full") -> Index:
    rows = con.execute("SELECT note_id, card_ids, text, vector FROM note_vector WHERE model = ? AND repr = ?"
                       " ORDER BY note_id", (model, repr)).fetchall()
    if not rows:
        return Index([], [], [], np.zeros((0, 1), dtype=np.float32))
    return Index([int(r[0]) for r in rows], [json.loads(r[1]) for r in rows], [str(r[2]) for r in rows],
                 np.stack([np.frombuffer(r[3], dtype=np.float32) for r in rows]))


def fuse(rankings: list[list[int]], k: int = 60) -> list[int]:
    """Reciprocal rank fusion (k = 60, as the vault's semantic_search): one ranking from several."""
    score: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            score[item] = score.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score, key=lambda i: -score[i])


async def rerank(url: str, query: str, texts: list[str]) -> list[float]:
    async with httpx.AsyncClient(timeout=60) as http:
        resp = await http.post(f"{url.rstrip('/')}/rerank", json={"query": query, "texts": texts, "truncate": True})
        resp.raise_for_status()
        scores = [0.0] * len(texts)
        for item in resp.json():
            scores[int(item["index"])] = float(item["score"])
        return scores


async def search(con: sqlite3.Connection, core: Core, cfg: Config, url: str, text: str | None = None,
                 card_id: int | None = None, k: int = 10) -> dict[str, Any]:
    """Cards that mean what `text` says (or what card `card_id` asks), with Aman's memory of each: the same shape as
    learner.search. Dense neighbours and Anki's word search, fused, then re-scored by the reranker when there is one."""
    idx = load(con, cfg.model)
    if not idx.note_ids:
        raise LookupError("the neighbours index is empty: it builds at 04:15, or on POST /neighbors/refresh")
    by_card = idx.by_card()
    own: int | None = None
    if card_id is not None:
        own = by_card.get(card_id)
        if own is None:
            raise LookupError(f"card {card_id} isn't in the index yet (it builds at 04:15)")
        query, vec = idx.texts[own], idx.matrix[own]
    elif text and text.strip():
        query = text.strip()
        vec = (await embed(url, cfg.model, [query], "query"))[0]
    else:
        raise ValueError("give text or a card_id")
    sims = idx.matrix @ vec
    dense = [int(i) for i in np.argsort(-sims)[:50] if int(i) != own]
    async with reads(core) as c:
        words: list[int] = []
        if card_id is None:
            found, _ = await learner.ids(c, learner.anki_query(query), 50)
            words = list(dict.fromkeys(by_card[cid] for cid in found if cid in by_card and by_card[cid] != own))
        order = fuse([dense, words]) if words else dense
        reranked = False
        if cfg.reranker:
            top = order[:cfg.pool]
            try:
                scores = await rerank(cfg.reranker, query, [idx.texts[i] for i in top])
                order = [i for _, i in sorted(zip(scores, top, strict=True), key=lambda x: -x[0])] + order[cfg.pool:]
                reranked = True
            except httpx.HTTPError as e:   # the fused order still stands
                log.warning("neighbors_rerank_failed", error=str(e)[:200])
        chosen = [i for i in order if idx.card_ids[i]][:k]
        cards = await learner.views(c, [idx.card_ids[i][0] for i in chosen])
    for view, i in zip(cards, chosen, strict=True):
        view["similarity"] = round(float(sims[i]), 3)
    seen = [v for v in cards if v["state"] not in ("new", "suspended")]
    rs = [v["retrievability"] for v in seen if v["retrievability"] is not None]
    return {"query": query if card_id is None else f"card {card_id}", "model": cfg.model, "reranked": reranked,
            "shown": len(cards), "cards": cards,
            "summary": {"new": sum(v["state"] == "new" for v in cards), "seen": len(seen),
                        "mean_retrievability": round(sum(rs) / len(rs), 2) if rs else None,
                        "weak": sum(1 for v in seen if (v["retrievability"] or 1) < 0.8 or v["lapses"] >= 3)}}

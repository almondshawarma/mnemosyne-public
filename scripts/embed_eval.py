"""E1 retro and the embedding-model check (docs/embeddings.md). Read-only: the Ledger opened read-only, anki-core
through its read tools, Ollama and the reranker over HTTP. Card text stays on the Obelisk (in the container's /tmp);
only numbers are printed.

Run inside the api container:
    docker compose exec -T api python - collect < scripts/embed_eval.py   # notes, labeled pairs, queries → /tmp
    docker compose exec -T api python - score < scripts/embed_eval.py     # every model × text, against both sets

E1's labels are Aman's own decisions on cards dedupe scored: a flagged or held "duplicate" he kept is a negative,
one he rejected as a repeat (or merged) is a positive, and one he rejected for another reason is left out.
"""

import asyncio
import json
import random
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from mnemosyne import neighbors
from mnemosyne.anki.core_client import CoreClient
from mnemosyne.config import Settings, read_secret
from mnemosyne.verify.dedupe import normalize

OUT = Path("/tmp/embed_eval")
MODELS = ["nomic-embed-text", "bge-m3", "qwen3-embedding:0.6b", "embeddinggemma"]
RERANKER = "http://reranker:8094"
REASON = re.compile(r"(dedupe_flagged|dedupe_duplicate|merge_up) \(([0-9.]+)\): (.*)", re.DOTALL)
REPEAT = re.compile(r"dup|already|same as|repeat|covered by|exists|redundant", re.IGNORECASE)


# ---------- collect ----------

async def all_notes(s: Settings) -> dict[int, dict[str, Any]]:
    core = CoreClient(s.anki_core_url, read_secret(s.anki_core_token_file) or "")
    notes: dict[int, dict[str, Any]] = {}
    async with core.session() as c:
        ids: list[int] = []
        offset = 0
        while True:
            page = await c.call("anki_notes_search", {"query": "deck:*", "offset": offset, "limit": 100})
            ids += [int(i["id"]) for i in page["items"]]
            if not page.get("has_more"):
                break
            offset += 100
        for nid in ids:
            n = await c.call("anki_notes_get", {"note_id": nid})
            notes[nid] = {"type": n.get("note_type_name"), "tags": n.get("tags") or [],
                          "fields": {str(f["name"]): str(f["value"]) for f in n.get("fields") or []}}
    return notes


def matched_note(rest: str, by_front: dict[str, list[int]], own: int | None) -> int | None:
    """The note dedupe matched, from the reason's text: its normalized first field, cut at 120 characters, then
    maybe "; <next reason>"."""
    cuts = [rest[:120]] + [rest[:m.start()] for m in re.finditer(r"; ", rest)]
    for cut in cuts:
        for nid in by_front.get(cut.strip()[:120], []):
            if nid != own:
                return nid
    return None


def labeled_pairs(con: sqlite3.Connection, notes: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_front: dict[str, list[int]] = {}
    for nid, n in notes.items():
        by_front.setdefault(normalize(next(iter(n["fields"].values()), ""))[:120], []).append(nid)
    pairs: list[dict[str, Any]] = []
    tally = {"decided": 0, "other_rejection": 0, "unmatched": 0, "unscored": 0}
    rows = con.execute("SELECT id, status, note_type, fields_json, tags_json, review_reason, decision_note, note_id,"
                       " merge_into FROM card_draft WHERE status IN ('written', 'rejected', 'merged')"
                       " AND (review_reason LIKE '%dedupe_flagged (%' OR review_reason LIKE '%dedupe_duplicate (%'"
                       " OR review_reason LIKE '%merge_up (%')")
    for r in rows:
        tally["decided"] += 1
        m = REASON.search(r["review_reason"] or "")
        if m is None or float(m.group(2)) == 0.0:
            tally["unscored"] += 1      # Ollama was down: nothing was compared
            continue
        if r["status"] == "rejected" and r["decision_note"] and not REPEAT.search(r["decision_note"]):
            tally["other_rejection"] += 1
            continue
        target = r["merge_into"] if r["merge_into"] in notes else matched_note(m.group(3), by_front, r["note_id"])
        if target is None:
            tally["unmatched"] += 1
            continue
        pairs.append({"draft": r["id"], "kind": m.group(1), "score": float(m.group(2)),
                      "repeat": r["status"] != "written", "unexplained": r["status"] == "rejected"
                      and not r["decision_note"], "type": r["note_type"],
                      "card": {"type": r["note_type"], "fields": json.loads(r["fields_json"]),
                               "tags": json.loads(r["tags_json"])}, "note": target})
    return pairs, tally


def queries(con: sqlite3.Connection, notes: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Retrieval queries with a known answer. Aman's own questions that became cards (his words → that card), and
    formula and description queries (a card's answer → the card), across note types."""
    by_front: dict[str, int] = {normalize(next(iter(n["fields"].values()), "")): nid for nid, n in notes.items()}
    out: list[dict[str, Any]] = []
    for asked, card_json in con.execute("SELECT asked, card_json FROM question WHERE status = 'carded'"
                                        " AND card_json IS NOT NULL"):
        card = json.loads(card_json)
        first = next(iter((card.get("fields") or {}).values()), "")
        nid = by_front.get(normalize(first))
        if nid is not None and asked.strip():
            out.append({"kind": "question", "q": asked.strip(), "target": nid})
    rng = random.Random(29)
    pools: dict[str, list[tuple[str, int]]] = {"formula": [], "ipa": [], "sign": [], "error": [], "lang": []}
    for nid, n in notes.items():
        f, t = n["fields"], n["type"]
        if t == "M-Basic" and "\\(" in f.get("Back", "") and len(neighbors.clean(f["Back"])) > 12:
            pools["formula"].append((neighbors.clean(f["Back"]), nid))
        elif t == "M-IPA" and neighbors.clean(f.get("Description", "")):
            pools["ipa"].append((neighbors.clean(f["Description"]), nid))
        elif t == "M-Sign" and neighbors.clean(f.get("Notes", "")):
            pools["sign"].append((neighbors.clean(f["Notes"]), nid))
        elif t == "M-Error" and neighbors.clean(f.get("Fix", "")):
            pools["error"].append((neighbors.clean(f["Fix"]), nid))
        elif t == "M-Lang" and neighbors.clean(f.get("Meaning", "")):
            pools["lang"].append((neighbors.clean(f["Meaning"]), nid))
    for kind, pool in pools.items():
        for q, nid in rng.sample(pool, min(len(pool), 25 if kind == "formula" else 12)):
            out.append({"kind": kind, "q": q, "target": nid})
    return out


def collect() -> None:
    s = Settings()
    OUT.mkdir(exist_ok=True)
    notes = asyncio.run(all_notes(s))
    con = sqlite3.connect(f"file:{s.ledger}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    pairs, tally = labeled_pairs(con, notes)
    qs = queries(con, notes)
    (OUT / "notes.json").write_text(json.dumps({str(k): v for k, v in notes.items()}))
    (OUT / "pairs.json").write_text(json.dumps(pairs))
    (OUT / "queries.json").write_text(json.dumps(qs))
    types: dict[str, int] = {}
    for n in notes.values():
        types[n["type"]] = types.get(n["type"], 0) + 1
    print(json.dumps({"notes": len(notes), "note_types": types, "pairs": len(pairs),
                      "repeats": sum(p["repeat"] for p in pairs),
                      "repeats_unexplained": sum(p["unexplained"] for p in pairs), **tally,
                      "pairs_by_type": {t: sum(p["type"] == t for p in pairs) for t in {p["type"] for p in pairs}},
                      "queries": {k: sum(q["kind"] == k for q in qs) for k in {q["kind"] for q in qs}}}, indent=1))


# ---------- score ----------

def embed(url: str, model: str, texts: list[str]) -> np.ndarray:
    out: list[list[float]] = []
    with httpx.Client(timeout=600) as http:
        for start in range(0, len(texts), 32):
            resp = http.post(f"{url}/api/embed", json={"model": model, "input": texts[start:start + 32],
                                                       "keep_alive": "2m"})
            resp.raise_for_status()
            out += resp.json()["embeddings"]
    m = np.asarray(out, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def rerank(query: str, texts: list[str]) -> list[float]:
    with httpx.Client(timeout=120) as http:
        resp = http.post(f"{RERANKER}/rerank", json={"query": query, "texts": texts, "truncate": True})
        resp.raise_for_status()
        scores = [0.0] * len(texts)
        for item in resp.json():
            scores[int(item["index"])] = float(item["score"])
        return scores


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    if not len(pos) or not len(neg):
        return float("nan")
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def separation(scores: np.ndarray, labels: np.ndarray, boot: int = 1000) -> dict[str, Any]:
    pos, neg = scores[labels], scores[~labels]
    rng = np.random.default_rng(29)
    draws = [auc(rng.choice(pos, len(pos)), rng.choice(neg, len(neg))) for _ in range(boot)]
    curve = []
    for t in np.unique(scores):
        flagged = scores >= t
        tp = int((flagged & labels).sum())
        curve.append({"t": round(float(t), 4), "precision": tp / max(1, int(flagged.sum())),
                      "recall": tp / max(1, int(labels.sum()))})
    best = max(curve, key=lambda c: 2 * c["precision"] * c["recall"] / max(1e-9, c["precision"] + c["recall"]))
    at90 = max((c["recall"] for c in curve if c["precision"] >= 0.9), default=0.0)
    return {"auc": round(auc(pos, neg), 3), "auc_ci": [round(float(np.nanpercentile(draws, q)), 3) for q in (2.5, 97.5)],
            "best_f1": best, "recall_at_precision_90": round(at90, 3), "curve": curve}


def score() -> None:
    s = Settings()
    notes = {int(k): v for k, v in json.loads((OUT / "notes.json").read_text()).items()}
    pairs = json.loads((OUT / "pairs.json").read_text())
    qs = json.loads((OUT / "queries.json").read_text())
    labels = np.array([p["repeat"] for p in pairs])
    results: dict[str, Any] = {"n_pairs": len(pairs), "n_repeats": int(labels.sum()),
                               "stored_score": separation(np.array([p["score"] for p in pairs]), labels)}

    def text(item: dict[str, Any], repr: neighbors.Repr) -> str:
        return neighbors.note_text(item["type"], item["fields"], item["tags"], repr)

    nids = list(notes)
    for model in MODELS:
        for repr in ("front", "full", "texwords"):
            pre = neighbors.prompt(model, "same")
            a = embed(s.ollama_url, model, [pre + (text(p["card"], repr) or "(empty)") for p in pairs])
            b = embed(s.ollama_url, model, [pre + (text(notes[p["note"]], repr) or "(empty)") for p in pairs])
            sims = (a * b).sum(axis=1)
            results[f"e1/{model}/{repr}"] = separation(sims, labels)
        # retrieval: every note as a document, each query against all of them
        for repr in ("front", "full"):
            docs = embed(s.ollama_url, model, [neighbors.prompt(model, "doc") + (text(notes[n], repr) or "(empty)")
                                               for n in nids])
            qv = embed(s.ollama_url, model, [neighbors.prompt(model, "query") + q["q"] for q in qs])
            ranks = []
            for q, v in zip(qs, qv, strict=True):
                order = np.argsort(-(docs @ v))
                ranks.append(int(np.where(np.array(nids)[order] == q["target"])[0][0]) + 1)
            results[f"retrieval/{model}/{repr}"] = summarize(qs, ranks)
            if repr == "full":
                results[f"retrieval/{model}/{repr}+rerank"] = summarize(qs, reranked(qs, qv, docs, nids, notes))
        print(f"done {model}", file=sys.stderr)
    for repr in ("front", "full"):
        sims = np.array([rerank(text(p["card"], repr) or "(empty)", [text(notes[p["note"]], repr) or "(empty)"])[0]
                         for p in pairs])
        results[f"e1/reranker/{repr}"] = separation(sims, labels)
    results["e1_by_type"] = by_type(pairs, labels, results)
    (OUT / "results.json").write_text(json.dumps(results))
    print(json.dumps(brief(results), indent=1))


def reranked(qs: list[dict[str, Any]], qv: np.ndarray, docs: np.ndarray, nids: list[int],
             notes: dict[int, dict[str, Any]], pool: int = 25) -> list[int]:
    ranks = []
    for q, v in zip(qs, qv, strict=True):
        order = [nids[i] for i in np.argsort(-(docs @ v))]
        top = order[:pool]
        scores = rerank(q["q"], [neighbors.note_text(notes[n]["type"], notes[n]["fields"], notes[n]["tags"])
                                 for n in top])
        new = [n for _, n in sorted(zip(scores, top, strict=True), key=lambda x: -x[0])] + order[pool:]
        ranks.append(new.index(q["target"]) + 1)
    return ranks


def summarize(qs: list[dict[str, Any]], ranks: list[int]) -> dict[str, Any]:
    r = np.array(ranks)
    kinds = sorted({q["kind"] for q in qs})
    by = {k: round(float((r[[q["kind"] == k for q in qs]] <= 5).mean()), 3) for k in kinds}
    return {"recall@5": round(float((r <= 5).mean()), 3), "mrr": round(float((1 / r).mean()), 3),
            "recall@5_by_kind": by}


def by_type(pairs: list[dict[str, Any]], labels: np.ndarray, results: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for t in sorted({p["type"] for p in pairs}):
        mask = np.array([p["type"] == t for p in pairs])
        out[t] = {"n": int(mask.sum()), "repeats": int(labels[mask].sum()),
                  "stored_mean": round(float(np.mean([p["score"] for p, m in zip(pairs, mask) if m])), 3)}
    return out


def brief(results: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in results.items():
        if isinstance(v, dict) and "auc" in v:
            out[k] = {"auc": v["auc"], "ci": v["auc_ci"], "best_f1_t": v["best_f1"]["t"],
                      "p": round(v["best_f1"]["precision"], 2), "r": round(v["best_f1"]["recall"], 2),
                      "recall@p90": v["recall_at_precision_90"]}
        elif isinstance(v, dict) and "recall@5" in v:
            out[k] = v
        else:
            out[k] = v
    return out


if __name__ == "__main__":
    {"collect": collect, "score": score}[sys.argv[1]]()

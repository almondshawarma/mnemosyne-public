# /// script
# requires-python = ">=3.12"
# ///
"""Move golden cards through the review page (see .claude/skills/mnemosyne-cards/review.md).

    uv run scripts/golden_review.py pending OUT.json
        Payload of every golden card that has no review yet, ready for `review.py prepare`.
    uv run scripts/golden_review.py merge REVIEWED.json
        Write the output of `review.py apply` back into the golden files, matched by card key.
        A file whose cards are all reviewed becomes "status": "reviewed".
"""
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / ".claude" / "skills" / "mnemosyne-cards" / "golden"

Doc = dict[str, Any]


def load() -> dict[Path, Doc]:
    return {p: json.loads(p.read_text(encoding="utf-8")) for p in sorted(GOLDEN.glob("*.json"))}


def context(doc: Doc) -> dict[str, Doc]:
    """For a card that replaced an earlier one: the earlier card and what Aman said about it."""
    out = {}
    for old in doc.get("superseded", []):
        ctx = {"label": "Replaces this card", "card": {k: old[k] for k in ("note_type", "fields")},
               "reason": old["review"].get("reason", "")}
        out |= {key: ctx for key in old["replaced_by"]}
    return out


def pending(docs: dict[Path, Doc]) -> Doc:
    cards = []
    for doc in docs.values():
        ctx = context(doc)
        cards += [{**c, "context": ctx[c["key"]]} if c["key"] in ctx else c for c in doc["cards"] if "review" not in c]
    return {"source": {"kind": "golden", "ref": "Golden set: cards awaiting review"}, "cards": cards}


def merge(docs: dict[Path, Doc], reviewed: Doc) -> dict[str, int]:
    def strip(c: Doc) -> Doc:
        return {k: v for k, v in c.items() if k != "context"}

    kept = {c["key"]: strip(c) for c in reviewed["cards"]}
    dropped = {c["key"]: strip(c) for c in reviewed["rejected"]}
    counts = {"reviewed": 0, "rejected": 0}
    for doc in docs.values():
        cards = []
        for c in doc["cards"]:
            if c["key"] in dropped:
                doc.setdefault("rejected", []).append(dropped[c["key"]])
                counts["rejected"] += 1
                continue
            if c["key"] in kept:
                c = kept[c["key"]]
                counts["reviewed"] += 1
            cards.append(c)
        doc["cards"] = cards
        if all("review" in c for c in cards):
            doc["status"], doc["reviewed_by"] = "reviewed", "aman"
    return counts


def main(argv: list[str]) -> None:
    docs = load()
    match argv:
        case ["pending", out]:
            payload = pending(docs)
            Path(out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{len(payload['cards'])} cards awaiting review")
        case ["merge", reviewed]:
            counts = merge(docs, json.loads(Path(reviewed).read_text(encoding="utf-8")))
            for path, doc in docs.items():
                path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"merged: {counts['reviewed']} reviewed, {counts['rejected']} rejected")
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

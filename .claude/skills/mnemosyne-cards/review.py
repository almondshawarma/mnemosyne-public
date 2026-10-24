# /// script
# requires-python = ">=3.12"
# ///
"""Card batches for Aman's review page (mnemosyne /review). See review.md next to this file.

    review.py hold PAYLOAD --id ID [--title TITLE] [--kind KIND] OUT
    review.py summary EXPORT

`hold` turns a mnemosyne-cards payload into the body for mnemosyne's POST /reviews: every card gets a stable
`key` (what Aman's decisions refer to), plus the canon titles the page shows next to each card's rule numbers.

`summary` prints one line for the export Claude takes back (GET /reviews/ID/export).
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CANON = re.compile(r"^(\d+)\. \*\*(.+?)\*\*")
REVIEW_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
KINDS = ("lecture", "golden", "cards")

Card = dict[str, Any]


def canon_titles(skill_md: str) -> dict[str, str]:
    return {m[1]: m[2].rstrip(".") for m in map(CANON.match, skill_md.splitlines()) if m}


def card_key(card: Card) -> str:
    body = json.dumps({k: card.get(k) for k in ("note_type", "fields")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()[:10]


def keyed(cards: list[Card]) -> list[Card]:
    """Keep existing keys; derive the rest from content. Keys are unique within a batch."""
    seen: set[str] = set()
    out = []
    for c in cards:
        key = c.get("key") or card_key(c)
        while key in seen:
            key = hashlib.sha256(key.encode()).hexdigest()[:10]
        seen.add(key)
        out.append({**c, "key": key})
    return out


def hold(payload: dict[str, Any], review_id: str, title: str, kind: str) -> dict[str, Any]:
    """The POST /reviews body. Review-only keys on cards (why, canon, question, golden, context) travel along."""
    if not REVIEW_ID.match(review_id):
        raise ValueError(f"review id must be lowercase kebab-case: {review_id!r}")
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    if not payload.get("cards"):
        raise ValueError("payload has no cards")
    return {"id": review_id, "title": title, "kind": kind, "source": payload.get("source", {}),
            "cards": keyed(payload["cards"]), "signals": payload.get("signals", []),
            "skipped": payload.get("skipped", []),
            "canon": canon_titles((HERE / "SKILL.md").read_text(encoding="utf-8"))}


def summary(export: dict[str, Any]) -> str:
    verdicts = [c["review"]["verdict"] for c in export["cards"]]
    line = (f"approved {verdicts.count('approve')} · edited {verdicts.count('edit')} · rejected {len(export['rejected'])}"
            f" · pending {len(export['pending'])} · promoted {len(export['review']['promote'])}")
    anki = export.get("anki", {})
    if n := len(anki.get("rejected_but_in_anki", [])):
        line += f" · {n} rejected but already in Anki"
    if n := len(anki.get("no_draft", {})):
        line += f" · {n} never became notes"
    return line


def read(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    ph = sub.add_parser("hold")
    ph.add_argument("payload")
    ph.add_argument("--id", required=True)
    ph.add_argument("--title")
    ph.add_argument("--kind", default="cards", choices=KINDS)
    ph.add_argument("out")
    ps = sub.add_parser("summary")
    ps.add_argument("export")
    a = p.parse_args()
    if a.cmd == "hold":
        body = hold(read(a.payload), a.id, a.title or a.id, a.kind)
        Path(a.out).write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"review {a.id}: {len(body['cards'])} cards → {a.out}")
    else:
        print(summary(read(a.export)))


if __name__ == "__main__":
    main()

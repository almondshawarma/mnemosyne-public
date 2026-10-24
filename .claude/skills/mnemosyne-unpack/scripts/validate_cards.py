#!/usr/bin/env python3
"""Check a mnemosyne-cards payload before proposing it: structure, note types, decks, and tag vocabulary.

Uses mnemosyne's own validator (src/mnemosyne/cards.py) and config/topics.yaml, so it never drifts from the
canon. Run it with the repo's environment:

    uv run --project <mnemosyne-repo> python <skill-dir>/scripts/validate_cards.py payload.json [--repo <mnemosyne-repo>]

Exit status 0 means every card would become a note; anything else prints what's wrong, card by card.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def vocabulary(topics_yaml: Path) -> set[str]:
    import yaml

    topics = yaml.safe_load(topics_yaml.read_text(encoding="utf-8"))

    def walk(node, prefix):
        if isinstance(node, dict):
            for key, child in node.items():
                yield f"{prefix}::{key}"
                yield from walk(child, f"{prefix}::{key}")
        elif isinstance(node, list):
            for leaf in node:
                yield f"{prefix}::{leaf}"

    return set(walk(topics["domains"], "topic")) | {f"principle::{p}" for p in topics.get("principles", [])}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("payload")
    ap.add_argument("--repo", help="mnemosyne repo root (default: the repo this skill ships in)")
    args = ap.parse_args(argv)

    repo = Path(args.repo) if args.repo else Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(repo / "src"))
    from mnemosyne.cards import Payload, Schema

    payload = Payload.model_validate(json.loads(Path(args.payload).read_text(encoding="utf-8")))
    schema = Schema.load(repo / "desktop" / "notetypes", repo / "config" / "decks.yaml")
    vocab = vocabulary(repo / "config" / "topics.yaml")

    bad = 0
    for i, card in enumerate(payload.cards, 1):
        problems = schema.problems(card)
        unknown = [t for t in card.tags if t.startswith(("topic::", "principle::")) and t not in vocab]
        if unknown:
            problems.append(f"not in config/topics.yaml: {', '.join(unknown)}")
        lead = next(iter(card.fields.values()), "")[:60]
        print(f"{i:>2} {card.note_type:<10} {'OK' if not problems else 'FAIL'}  {lead}")
        for p in problems:
            print(f"      - {p}")
        bad += bool(problems)
    print(f"{len(payload.cards) - bad}/{len(payload.cards)} cards valid")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

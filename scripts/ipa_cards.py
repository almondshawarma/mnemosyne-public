"""Build the M-IPA card payload from data/ipa.yaml and the fetched recordings (data/audio/ipa/manifest.json).

    uv run scripts/ipa_cards.py > ipa.json

One note per symbol: symbol → sound + description, and (with its recording) sound → symbol. Audio isn't in the
payload: the writer attaches it (mnemosyne/audio.py). Sounds in English, Spanish, Hindi, and French come first, so
they're the first new IPA cards on walks; rare ones come last.
"""

import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from mnemosyne.audio import key_of, manifest, source

AHEAD = ("English", "American English", "British English", "Scottish English", "Spanish", "Castilian Spanish",
         "Hindi", "Hindi/Urdu", "French")


def description(e: dict[str, Any]) -> str:
    if "manner" in e:
        return f"{e['voicing']} {e['place']} {e['manner'].replace('-', ' ')}"
    return str(e["article"]).lower()


def tags(e: dict[str, Any]) -> list[str]:
    keys = ("voicing", "place", "manner") if "manner" in e else ("height", "backness", "rounding")
    return ["type::definition", "src::chat", *(f"ipa::{k}::{e[k]}" for k in keys)]


def rank(e: dict[str, Any]) -> int:
    ex: list[str] = e.get("examples") or []
    return -sum(1 for x in ex if x.split(" <")[0].split(" ")[0] in AHEAD or x.startswith(AHEAD))


def main() -> None:
    data = yaml.safe_load((ROOT / "data" / "ipa.yaml").read_text(encoding="utf-8"))
    sounds = manifest([ROOT / "data"], "audio/ipa")
    entries = sorted([*data["consonants"], *data["vowels"]], key=rank)
    cards: list[dict[str, Any]] = []
    for e in entries:
        rec = sounds.get(key_of(str(e["symbol"])))
        cards.append({
            "note_type": "M-IPA", "deck": "Lang::IPA", "mode": "audio",
            "fields": {"Symbol": str(e["symbol"]), "Description": description(e),
                       "ExampleWord": " · ".join(e.get("examples") or []),
                       "Source": source(rec) if rec else "IPA chart (no recording)",
                       "SourceLink": rec["page"] if rec else ""},
            "tags": tags(e),
        })
    payload = {"source": {"kind": "chat", "ref": "IPA chart: pulmonic consonants, vowels, common affricates"},
               "cards": cards}
    sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

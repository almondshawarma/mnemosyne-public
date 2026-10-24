"""Build the M-Sign card payload from data/asl.yaml, for the signs that have a video (scripts/asl_videos.py).

    uv run scripts/asl_cards.py MANIFEST [--first N] > asl.json

MANIFEST is the media folder's video/asl/manifest.json, copied from the Obelisk. One note per sign: meaning → sign
it (you sign, then compare with the video), and video → meaning. The video isn't in the payload: the writer attaches
it by meaning. Cards go to Lang::ASL, which has to exist on Desktop first (docs/desktop-sessions/2026-10-asl.md).
"""

import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from mnemosyne.audio import key_of, source


def main(args: list[str]) -> None:
    videos = {key_of(k): v for k, v in json.loads(Path(args[0]).read_text(encoding="utf-8")).items()}
    first = int(args[args.index("--first") + 1]) if "--first" in args else None
    signs: list[dict[str, Any]] = yaml.safe_load((ROOT / "data" / "asl.yaml").read_text(encoding="utf-8"))["signs"]
    cards: list[dict[str, Any]] = []
    for s in signs[:first]:
        v = videos.get(key_of(str(s["meaning"])))
        if v is None:
            continue
        also = [a.strip() for a in str(s.get("also") or "").split(",") if a.strip() and a.strip() != s["meaning"]]
        cards.append({
            "note_type": "M-Sign", "deck": "Lang::ASL", "mode": "visual",
            "fields": {"Gloss": str(s["meaning"]),
                       "Notes": (f"Also: {', '.join(also[:6])}. " if also else "") + f"Signbank gloss {v['gloss']}.",
                       "Source": source(v), "SourceLink": v["page"]},
            "tags": ["type::definition", "src::chat", "lang::asl"],
        })
    sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
    json.dump({"source": {"kind": "chat", "ref": "ASL starter vocabulary (ASL-LEX frequency, ASL Signbank video)"},
               "cards": cards}, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])

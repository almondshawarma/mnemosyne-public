"""Fetch ASL Signbank videos for data/asl.yaml into the media folder (default ~/helios/mnemosyne/media/video/asl/).

ASL Signbank (Hochgesang, Crasborn & Lillo-Martin, 2017-2026; https://aslsignbank.com) is CC BY-NC-SA 4.0, and its
conditions of use invite saving its videos. Each sign's citation-form video is read from its public gloss page,
downloaded once, and re-encoded small (360p, no audio, H.264, under anki-core's 1 MB media limit). It's recorded in
manifest.json with the attribution the license needs. Requests are spaced out: it's an academic server.

Run on the Obelisk (ffmpeg, network, and the media folder the api mounts read-only):
    python3 scripts/asl_videos.py              # every sign in data/asl.yaml without a video yet
    python3 scripts/asl_videos.py --first 50   # just the first 50 of the list
Then build the cards with scripts/asl_cards.py. The writer attaches each video by its meaning (mnemosyne/audio.py).
"""

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
MEDIA = Path.home() / "helios" / "mnemosyne" / "media" / "video" / "asl"
BASE = "https://aslsignbank.com"
UA = "mnemosyne/1.0 (personal study; https://github.com/almondshawarma)"
CREDIT = {"source": "ASL Signbank", "author": "Hochgesang, Crasborn & Lillo-Martin (2017-2026)",
          "license": "CC BY-NC-SA 4.0"}
MAX_BYTES = 1_000_000


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
        return r.read()


def gloss_page(sid: int) -> tuple[str, str, str]:
    """(video path, ID gloss, English equivalents) from a sign's public page."""
    page = get(f"{BASE}/dictionary/gloss/{sid}.html").decode("utf-8", errors="replace")
    video = re.search(r'src="(/dictionary/protected_media/glossvideo/[^"]+\.mp4)"', page)
    if not video:
        raise LookupError(f"no video on gloss {sid}")
    gloss = re.search(r"Sign for ([^<\n]+)", page)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    trans = re.search(r"Translation equivalents\s*(.+?)\s{2,}|Translation equivalents\s*([^.]+?)\s*//", text)
    return video[1], (gloss[1].strip() if gloss else str(sid)), (next(g for g in trans.groups() if g) if trans else "")


def encode(raw: bytes, dest: Path) -> None:
    # Signbank's MP4s keep their index at the end, which ffmpeg can't seek to in a pipe: go through a file
    src = dest.with_suffix(".download")
    src.write_bytes(raw)
    try:
        for crf in (28, 32, 36):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-an", "-vf", "scale=-2:360",
                            "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", str(dest)], check=True)
            if dest.stat().st_size <= MAX_BYTES:
                return
        raise ValueError(f"{dest.name} is still over {MAX_BYTES} bytes")
    finally:
        src.unlink(missing_ok=True)


def main(args: list[str]) -> None:
    first = int(args[args.index("--first") + 1]) if "--first" in args else None
    signs: list[dict[str, Any]] = yaml.safe_load((ROOT / "data" / "asl.yaml").read_text(encoding="utf-8"))["signs"]
    MEDIA.mkdir(parents=True, exist_ok=True)
    manifest_path = MEDIA / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for s in signs[:first]:
        key = str(s["meaning"])
        if key in manifest and (MEDIA / manifest[key]["file"]).exists():
            continue
        try:
            path, gloss, equivalents = gloss_page(int(s["signbank"]))
            dest = MEDIA / ("mn_asl_" + re.sub(r"[^a-z0-9]+", "-", gloss.lower()).strip("-") + f"-{s['signbank']}.mp4")
            encode(get(BASE + path), dest)
        except Exception as e:  # noqa: BLE001 -- one missing sign doesn't stop the rest
            print(f"  {key:16} skipped: {e}")
            continue
        manifest[key] = {"file": dest.name, "signbank": s["signbank"], "gloss": gloss, "equivalents": equivalents,
                         "page": f"{BASE}/dictionary/gloss/{s['signbank']}.html", **CREDIT}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
        print(f"  {key:16} {gloss:20} {dest.stat().st_size // 1024} KB")
        time.sleep(2)
    print(f"{len(manifest)} signs have videos in {MEDIA}")


if __name__ == "__main__":
    main(sys.argv[1:])

"""Fetch the IPA recordings for data/ipa.yaml from Wikimedia Commons into data/audio/ipa/ (PLAN §7).

Each sound is the recording on its Wikipedia article (or the `commons:` file named in data/ipa.yaml). Files become
loudness-normalized mono mp3, which AnkiMobile plays (it doesn't reliably play Ogg). The author, license, and file
page go in data/audio/ipa/manifest.json, and from there into each card's Source field.

Needs Python 3.10+, PyYAML, ffmpeg, and the network; the Obelisk has all of them:
    python3 scripts/ipa_audio.py            # everything missing (a rerun continues where the last one stopped)
    python3 scripts/ipa_audio.py ʈ ɖ        # just these symbols (refetch)
Commit the mp3 files and manifest.json afterwards. The card writer attaches them (mnemosyne/audio.py).
"""

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "audio" / "ipa"
UA = "mnemosyne/1.0 (personal flashcards; https://github.com/almondshawarma)"
AUDIO = (".ogg", ".oga", ".wav", ".flac", ".mp3")
SYNONYMS = {"plosive": {"plosive", "stop"}, "tap": {"tap", "flap"}, "postalveolar": {"postalveolar", "palatoalveolar"}}


def fetch(url: str) -> bytes:
    """GET with Wikimedia's etiquette: a real User-Agent, and backing off when it answers 429."""
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            wait = int(e.headers.get("Retry-After") or 0) or 20 * (attempt + 1)
            print(f"    rate-limited; waiting {wait} s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def api(host: str, **params: str) -> dict[str, Any]:
    return json.loads(fetch(f"https://{host}/w/api.php?" + urllib.parse.urlencode({"format": "json", **params})))


def article_files(article: str) -> list[str]:
    got = api("en.wikipedia.org", action="query", titles=article, redirects="1", prop="images", imlimit="100")
    pages = got.get("query", {}).get("pages", {})
    return [i["title"] for p in pages.values() for i in p.get("images", []) if i["title"].lower().endswith(AUDIO)]


def words(s: str) -> set[str]:
    out: set[str] = set()
    for w in re.findall(r"[a-z]+", s.lower().replace("-", " ").replace("–", " ")):
        out |= SYNONYMS.get(w, {w})
    return out


def target(e: dict[str, Any]) -> str:
    if "manner" in e:
        return f"{e['voicing']} {e['place']} {e['manner'].replace('-', ' ')}"
    return str(e["article"])


def choose(e: dict[str, Any], files: list[str]) -> str | None:
    """The article's recording whose name best matches the sound: an article can cover several (dental and
    alveolar plosives), and lists recordings of words too."""
    want = words(target(e))
    scored = sorted(((len(want & words(f)) - 0.1 * len(words(f) - want), f) for f in files), reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def author(artist_html: str, user: str | None) -> str:
    a = re.sub(r"<[^>]+>", "", artist_html)
    a = re.sub(r"^No machine-readable author provided\.\s*", "", a)
    a = re.sub(r"\s*assumed \(based on copyright claims\)\.?", "", a)
    a = re.sub(r"^Recorded by\s*", "", a).strip(" .")
    if a and a.lower() != "unknown":
        return a
    return f"uploaded by {user}" if user else "unknown"


def info(title: str) -> dict[str, str]:
    got = api("commons.wikimedia.org", action="query", titles=title, prop="imageinfo",
              iiprop="url|user|extmetadata", iiextmetadatafilter="Artist|LicenseShortName")
    page = next(iter(got["query"]["pages"].values()))
    ii = page["imageinfo"][0]
    meta = ii.get("extmetadata", {})
    return {"source": page["title"], "url": ii["url"].split("?")[0], "page": ii["descriptionurl"],
            "author": author(meta.get("Artist", {}).get("value", ""), ii.get("user")),
            "license": meta.get("LicenseShortName", {}).get("value", "unknown")}


def slug(title: str) -> str:
    """From the recording's own name, so two symbols never share a file (l and ɫ are both alveolar laterals)."""
    stem = title.removeprefix("File:").rsplit(".", 1)[0]
    return "mn_ipa_" + "-".join(re.findall(r"[a-z0-9]+", stem.lower())) + ".mp3"


def convert(url: str, dest: Path) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", "pipe:0", "-ac", "1", "-ar", "44100",
                    "-af", "loudnorm=I=-18:TP=-2:LRA=11", "-b:a", "64k", str(dest)], input=fetch(url), check=True)


def main(only: list[str]) -> None:
    data = yaml.safe_load((ROOT / "data" / "ipa.yaml").read_text(encoding="utf-8"))
    entries = [*data["consonants"], *data["vowels"]]
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    missing: list[str] = []
    for e in entries:
        sym = str(e["symbol"])
        if (only and sym not in only) or (not only and sym in manifest and (OUT / manifest[sym]["file"]).exists()):
            continue
        title = f"File:{e['commons']}" if e.get("commons") else choose(e, article_files(str(e["article"])))
        if not title:
            missing.append(sym)
            print(f"  {sym:4} no recording found on '{e['article']}'")
            continue
        meta = info(title)
        dest = OUT / slug(meta["source"])
        convert(meta["url"], dest)
        manifest[sym] = {"file": dest.name, **meta}
        print(f"  {sym:4} {meta['source'][5:]:52} {meta['license']:14} {meta['author'][:30]}")
        # Saved after each one, so a rerun picks up where this stopped
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
        time.sleep(3)   # Wikimedia rate-limits bursts
    print(f"{len(manifest)} sounds in the manifest; missing: {' '.join(missing) or 'none'}")


if __name__ == "__main__":
    main(sys.argv[1:])

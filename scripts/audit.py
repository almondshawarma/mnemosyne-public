# /// script
# requires-python = ">=3.12"
# dependencies = ["zstandard"]
# ///
"""Read-only audit of an Anki .colpkg export. Never writes to the package.

Usage: uv run scripts/audit.py path/to/collection.colpkg [--out audit.json]
"""
import argparse
import collections
import html
import json
import re
import sqlite3
import statistics
import sys
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path

import zstandard


def open_colpkg(path: Path) -> sqlite3.Connection:
    tmp = Path(tempfile.mkdtemp()) / "col.db"
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "collection.anki21b" in names:
            raw = zstandard.ZstdDecompressor().stream_reader(z.open("collection.anki21b")).read()
        else:
            raw = z.read("collection.anki21" if "collection.anki21" in names else "collection.anki2")
    tmp.write_bytes(raw)
    return sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)


# --- prompt canonicalization (dedupe) ---------------------------------------
# Formatting-only differences collapse; anything that changes meaning (signs,
# exponents, digits, combining marks like the nukta in ज़) is kept.
IMG = re.compile(r"<img[^>]*?src=\"([^\"]*)\"[^>]*>")
TEX_NOISE = re.compile(r"\\(?:displaystyle|left|right|quad|qquad)(?![A-Za-z])|\\[,;:! ]")
FUNC_PARENS = re.compile(r"\\(sin|cos|tan|sec|csc|cot|ln|log|exp|arcsin|arccos|arctan|sinh|cosh|tanh)\s*\(\s*(\\?\w+)\s*\)")
FILLER = re.compile(r"\b(?:what is|what are|the|of|for|about|series|an?)\b|[:?]")


def images(field: str) -> list[str]:
    return IMG.findall(field)


def canon_text(field: str) -> str:
    """Field text with markup and TeX formatting stripped; images removed."""
    s = re.sub(r"<[^>]+>", " ", IMG.sub(" ", field))
    s = html.unescape(s).replace("\u200a", " ").replace("\xa0", " ")
    for d in (r"\(", r"\)", r"\[", r"\]"):
        s = s.replace(d, " ")
    s = TEX_NOISE.sub(" ", s)
    s = re.sub(r"\\[dt]frac", r"\\frac", s)
    s = re.sub(r"\\(?:mathrm|text|operatorname)\{(\w+)\}", r"\1", s)
    s = FUNC_PARENS.sub(r"\\\1 \2", s)
    s = re.sub(r"\{(\\?\w)\}", r"\1", s)
    return unicodedata.normalize("NFC", s).casefold()


def canon(field: str) -> str:
    return re.sub(r"\s+", "", canon_text(field))


def loose(field: str) -> str:
    return re.sub(r"\s+", "", FILLER.sub(" ", canon_text(field)))


ARITH = re.compile(r"^(\d+)\\times(\d+)$")


def dedupe(notes: list[dict]) -> dict:
    """Group notes whose prompts collide. Every group lists full note rows so
    the P1 merge is actionable without re-querying."""
    def groups(key) -> list[list[dict]]:
        g: dict[str, list[dict]] = collections.defaultdict(list)
        for n in notes:
            if k := key(n):
                g[k].append(n)
        return [v for v in g.values() if len(v) > 1]

    exact = groups(lambda n: canon(n["front"]) + "|" + ",".join(images(n["front"])))
    exact_ids = {n["nid"] for grp in exact for n in grp}
    likely = [grp for grp in groups(lambda n: loose(n["front"]) + "|" + ",".join(images(n["front"])))
              if not {n["nid"] for n in grp} <= exact_ids]
    # Same words, different picture: canon #5 violation, unusable in audio.
    ambiguous = [grp for grp in groups(lambda n: canon(n["front"]) if images(n["front"]) else "")
                 if len({",".join(images(n["front"])) for n in grp}) > 1]
    products = {}
    for n in notes:
        if m := ARITH.match(canon(n["front"])):
            products[(int(m[1]), int(m[2]))] = n["nid"]
    commutative = sorted([products[(a, b)], products[(b, a)]] for (a, b) in products if a < b and (b, a) in products)
    return {"duplicates": exact, "likely_duplicates": likely, "ambiguous_prompts": ambiguous,
            "commutative_pairs": {"count": len(commutative), "nids": commutative}}


def audit(c: sqlite3.Connection) -> dict:
    q = lambda s, *a: c.execute(s, a).fetchall()
    crt = q("select crt from col")[0][0]
    today = int((time.time() - crt) // 86400)
    decks = {i: n.replace("\x1f", "::") for i, n in q("select id, name from decks")}
    out: dict = {"today_index": today}
    out["totals"] = dict(zip(
        ["notes", "cards", "new", "review", "learning", "suspended_or_buried", "revlog"],
        [q("select count(*) from notes")[0][0], *q(
            "select count(*), sum(queue=0), sum(type=2), sum(queue in (1,3)), sum(queue<0) from cards")[0],
         q("select count(*) from revlog")[0][0]]))
    overdue = [today - d for (d,) in q("select due from cards where queue=2 and due<=?", today)]
    out["overdue_buckets"] = {f"{lo}-{hi}": sum(lo <= x <= hi for x in overdue)
                              for lo, hi in [(0, 7), (8, 30), (31, 90), (91, 180), (181, 365), (366, 99999)]}
    out["decks"] = {}
    for did, name in decks.items():
        n = q("select count(*), sum(queue=0), sum(queue=2 and due<=?) from cards where did=?", today, did)[0]
        if n[0]:
            out["decks"][name] = {"cards": n[0], "new": n[1], "due": n[2]}
    out["empty_decks"] = sorted(n for d, n in decks.items() if not q("select 1 from cards where did=? limit 1", d))
    out["notetypes"] = {name: q("select count(*) from notes where mid=?", i)[0][0]
                        for i, name in q("select id, name from notetypes")}
    tags = collections.Counter(t for (s,) in q("select tags from notes") for t in s.split())
    out["tags"] = dict(sorted(tags.items()))
    months = collections.Counter(time.strftime("%Y-%m", time.localtime(i / 1000)) for (i,) in q("select id from revlog"))
    out["reviews_by_month"] = dict(sorted(months.items()))
    times = [t for (t,) in q("select time from revlog")] or [0]
    out["answer_time_s"] = {"median": statistics.median(times) / 1000, "p90": sorted(times)[int(0.9 * len(times))] / 1000}
    rev = [e for (e,) in q("select ease from revlog where type=1")]
    out["review_pass_rate"] = round(sum(e > 1 for e in rev) / len(rev), 3) if rev else None
    notes = [{"nid": nid, "deck": decks.get(did), "front": f.split("\x1f")[0],
              "back": (f.split("\x1f") + [""])[1], "tags": t.split()}
             for nid, f, t, did in q("select n.id, n.flds, n.tags, (select did from cards where nid=n.id"
                                     " order by ord limit 1) from notes n order by n.id")]
    flds = [f for (f,) in q("select flds from notes")]
    out["features"] = {
        "mathjax": sum("\\(" in f or "\\[" in f for f in flds),
        "img": sum("<img" in f for f in flds),
        "sound": sum("[sound:" in f for f in flds),
    }
    out["dedupe"] = dedupe(notes)
    out["config"] = {k: v.decode() for k, v in q("select key, val from config") if k in (
        "fsrs", "rollover", "loadBalancerEnabled", "lastFsrsOptimize", "newCardsIgnoreReviewLimit")}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    ap.add_argument("colpkg", type=Path)
    ap.add_argument("--out", type=Path, help="write JSON here (UTF-8) instead of stdout")
    args = ap.parse_args()
    text = json.dumps(audit(open_colpkg(args.colpkg)), indent=2, ensure_ascii=False, default=str)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore  # Windows consoles default to cp1252
        print(text)

# /// script
# requires-python = ">=3.12"
# dependencies = ["zstandard", "pyyaml"]
# ///
"""P1 migration plan (PLAN §3.4). Reads a .colpkg, never writes to it.

Usage: uv run scripts/migrate.py path/to/collection.colpkg --out docs/runs/<date>-p1.json

Writes every step as a reviewable dry-run diff: JSON for scripts/anki_apply.py, and
Markdown next to it for a human. The steps and their apply order are listed in STEPS.
The retag is a separate plan (scripts/retag.py) and runs between move_cards and set_fields.
"""
import argparse
import collections
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import yaml

from audit import dedupe, open_colpkg
from speakify import speak, speak_ion, speak_si_prefix

ROOT = Path(__file__).resolve().parent.parent
STEPS = ["setup", "delete_notes", "change_notetype", "move_cards", "suspend_cards", "reposition_new",
         "set_fields", "add_tags", "delete_decks"]
AUG_2026 = 1787000000000  # nids of the Aug 2026 AI batch start here
# Duplicate pairs the heuristic misses (audit "Found by hand"): (legacy, aug-2026).
MANUAL_DUPES = [(1764044983609, 1787686570005), (1764044351250, 1787686570003), (1764043987782, 1787686569998)]
REVERSE_DECKS = {"Science::Metrology::SI Symbols & Prefixes", "Science::Chemistry::Polyatomic Ions",
                 "Math::Arithmetic::Fractions and Decimals", "Languages::Random Foreign Phrases"}
TERM_DECKS = ("Science::Chemistry", "Science::Physics")
MATRIX_2X2 = re.compile(r"\\\(\\begin\{bmatrix\}[-\d]+&amp;[-\d]+\\\\[-\d]+&amp;[-\d]+\\end\{bmatrix\}\\\)")
SPEAK_DECKS = ("Math::Arithmetic", "Math::Trigonometry::Unit Circle",
               "Science::Metrology::SI Symbols & Prefixes", "Science::Chemistry::Polyatomic Ions")
PRODUCT = re.compile(r"^\\\((\d+)\\times(\d+)\\\)$")


def under(deck: str, prefix: str) -> bool:
    return deck == prefix or deck.startswith(prefix + "::")


def uuid7(nid: int) -> str:
    """UUIDv7 with the note's creation time (nids are epoch ms). The rest is derived
    from the nid, so regenerating the plan gives the same IDs."""
    h = int.from_bytes(hashlib.sha256(f"mnemosyne:{nid}".encode()).digest()[:10], "big")
    rand_a, rand_b = h >> 68 & 0xFFF, h & ((1 << 62) - 1)
    n = (nid & ((1 << 48) - 1)) << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b
    s = f"{n:032x}"
    return f"{s[:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:]}"


def keep_reverse(deck: str, front: str, back: str) -> bool:
    """Decided 2026-09-23: SI prefixes, ions, fractions, phrases, term↔definition, named 2×2 matrices."""
    if deck in REVERSE_DECKS:
        return True
    if deck.startswith(TERM_DECKS) and "\\(" not in back:
        return True
    return deck == "Math::Linear Algebra" and bool(MATRIX_2X2.fullmatch(front.strip()))


def speak_note(deck: str, front: str, back: str) -> tuple[str, str] | None:
    if not deck.startswith(SPEAK_DECKS):
        return None
    sf = speak_si_prefix(front) if "SI Symbols" in deck else speak(front)
    sb = speak_ion(back) if "Polyatomic" in deck else speak(back)
    return (sf, sb) if sf and sb else None


def load(c: sqlite3.Connection) -> dict:
    q = lambda s, *a: c.execute(s, a).fetchall()
    decks = {i: n.replace("\x1f", "::") for i, n in q("select id, name from decks")}
    ntypes = {i: n for i, n in q("select id, name from notetypes")}
    revs = collections.Counter({cid: n for cid, n in q("select cid, count(*) from revlog group by cid")})
    cards = collections.defaultdict(list)
    for cid, nid, ord_, did, queue, type_, due in q("select id, nid, ord, did, queue, type, due from cards order by ord"):
        cards[nid].append({"cid": cid, "ord": ord_, "deck": decks[did], "queue": queue, "type": type_,
                           "due": due, "reviews": revs[cid]})
    notes = []
    for nid, mid, flds, tags in q("select id, mid, flds, tags from notes order by id"):
        f = flds.split("\x1f")
        notes.append({"nid": nid, "notetype": ntypes[mid], "fields": f, "front": f[0], "back": (f + [""])[1],
                      "tags": tags.split(), "cards": cards[nid], "deck": cards[nid][0]["deck"],
                      "reviews": sum(cd["reviews"] for cd in cards[nid])})
    return {"notes": notes, "decks": sorted(set(decks.values()))}


def plan(c: sqlite3.Connection, cfg: dict) -> dict:
    col = load(c)
    notes = {n["nid"]: n for n in col["notes"]}
    steps: dict[str, list] = {s: [] for s in STEPS}

    # setup: presets + target decks (config/decks.yaml)
    steps["setup"] = [{"preset": name, **vals} for name, vals in cfg["presets"].items()] + \
                     [{"deck": deck, "preset": preset} for deck, preset in cfg["decks"].items()]

    # delete_notes: keep the copy with review history; if none has any, keep the Aug 2026 copy
    d = dedupe([{**n, "deck": n["deck"]} for n in col["notes"]])
    groups = [[n["nid"] for n in g] for g in d["duplicates"] + d["likely_duplicates"]]
    groups += [[nid for nid in pair if nid in notes] for pair in MANUAL_DUPES]  # absent once deleted
    for g in (g for g in groups if len(g) > 1):
        keep = max(g, key=lambda nid: (notes[nid]["reviews"] > 0, nid >= AUG_2026, notes[nid]["reviews"]))
        for nid in g:
            if nid != keep:
                n = notes[nid]
                steps["delete_notes"].append({"nid": nid, "front": n["front"], "reviews": n["reviews"], "keep": keep,
                                              "keep_front": notes[keep]["front"]})
    deleted = {r["nid"] for r in steps["delete_notes"]}
    live = [n for n in col["notes"] if n["nid"] not in deleted]

    # change_notetype
    basic_fields = {"Front": "Front", "Back": "Back"}
    by_group: dict[str, list[int]] = collections.defaultdict(list)
    dropped_card2: set[int] = set()
    reversed_keep: set[int] = set()
    for n in live:
        if n["notetype"] == "Basic":
            by_group["basic"].append(n["nid"])
        elif n["notetype"] == "Basic (and reversed card)":
            if keep_reverse(n["deck"], n["front"], n["back"]):
                by_group["reversed_keep"].append(n["nid"])
                reversed_keep.add(n["nid"])
            else:
                by_group["reversed_drop"].append(n["nid"])
                dropped_card2 |= {cd["cid"] for cd in n["cards"] if cd["ord"] == 1}
        elif n["notetype"] == "Cloze":
            by_group["cloze"].append(n["nid"])
        else:
            raise SystemExit(f"no notetype rule for {n['notetype']!r} (nid {n['nid']})")
    specs = {
        "basic": ("Basic", "M-Basic", basic_fields, {"Card 1": "Card 1"}),
        "reversed_keep": ("Basic (and reversed card)", "M-Basic", basic_fields, {"Card 1": "Card 1", "Card 2": "Card 2"}),
        "reversed_drop": ("Basic (and reversed card)", "M-Basic", basic_fields, {"Card 1": "Card 1"}),
        "cloze": ("Cloze", "M-Cloze", {"Text": "Text", "Back Extra": "Extra"}, {}),
    }
    for key, (old, new, fmap, tmap) in specs.items():
        steps["change_notetype"].append({"group": key, "from": old, "to": new, "fields": fmap, "templates": tmap,
                                         "nids": by_group[key],
                                         "fronts": [notes[nid]["front"] for nid in by_group[key]]})  # for review

    # move_cards (per config migration rules)
    def target(deck: str) -> str | None:
        return next((r["to"] for r in cfg["migration"] if under(deck, r["from"])), None)
    unmapped = collections.Counter()
    for n in live:
        for cd in n["cards"]:
            if cd["cid"] in dropped_card2:
                continue
            to = target(cd["deck"])
            if to is None:
                unmapped[cd["deck"]] += 1
            elif to != cd["deck"]:
                steps["move_cards"].append({"cid": cd["cid"], "nid": n["nid"], "from": cd["deck"], "to": to})

    # suspend_cards + reposition_new: one order per multiplication pair
    products: dict[tuple[int, int], dict] = {}
    for n in live:
        if (m := PRODUCT.match(n["front"])) and under(n["deck"], "Math::Arithmetic::Multiplication"):
            products[(int(m[1]), int(m[2]))] = n
    for (a, b), n in sorted(products.items()):
        if a >= b or (b, a) not in products:
            continue
        small_first, large_first = n, products[(b, a)]
        reviewed = [x for x in (large_first, small_first) if x["reviews"]]
        keep = reviewed[0] if len(reviewed) == 1 else large_first
        drop = small_first if keep is large_first else large_first
        steps["suspend_cards"] += [{"cid": cd["cid"], "nid": drop["nid"], "front": drop["front"], "keep": keep["front"]}
                                   for cd in drop["cards"]]
    suspended = {r["cid"] for r in steps["suspend_cards"]}
    mult_new = [(max(a, b), min(a, b), n) for (a, b), n in products.items()]
    for _, _, n in sorted(mult_new, key=lambda x: (x[0], x[1])):
        for cd in n["cards"]:
            if cd["type"] == 0 and cd["cid"] not in suspended:
                steps["reposition_new"].append({"cid": cd["cid"], "front": n["front"], "due_before": cd["due"]})

    # set_fields: MnemosyneID for every note, Reverse flag, speech
    spoken: set[int] = set()
    for n in live:
        steps["set_fields"].append({"nid": n["nid"], "field": "MnemosyneID", "before": "", "after": uuid7(n["nid"])})
        if n["nid"] in reversed_keep:
            steps["set_fields"].append({"nid": n["nid"], "field": "Reverse", "before": "", "after": "y"})
        if n["notetype"] != "Cloze" and (said := speak_note(n["deck"], n["front"], n["back"])):
            spoken.add(n["nid"])
            steps["set_fields"] += [{"nid": n["nid"], "field": "SpeakFront", "before": "", "after": said[0]},
                                    {"nid": n["nid"], "field": "SpeakBack", "before": "", "after": said[1]}]

    # add_tags: mode::audio where fully spoken, mode::visual where an image carries meaning
    for n in live:
        if n["nid"] in spoken:
            steps["add_tags"].append({"nid": n["nid"], "add": ["mode::audio"]})
        elif any("<img" in f for f in n["fields"]):
            steps["add_tags"].append({"nid": n["nid"], "add": ["mode::visual"]})

    # delete_decks: every legacy deck; the apply step refuses any deck that still has cards
    keep_decks = set(cfg["decks"]) | {"Default"}
    steps["delete_decks"] = [{"deck": dk} for dk in sorted(col["decks"], key=lambda x: (-x.count("::"), x))
                             if dk not in keep_decks and not any(under(k, dk) for k in keep_decks)]

    audio_cards = [cd for n in live if n["nid"] in spoken for cd in n["cards"] if cd["cid"] not in dropped_card2]
    summary = {
        "notes": len(col["notes"]), "delete_notes": len(steps["delete_notes"]),
        "change_notetype": {r["group"]: len(r["nids"]) for r in steps["change_notetype"]},
        "card2_dropped": len(dropped_card2),
        "card2_dropped_reviews": sum(cd["reviews"] for n in live for cd in n["cards"] if cd["cid"] in dropped_card2),
        "move_cards": len(steps["move_cards"]), "unmapped_decks": dict(unmapped),
        "suspend_cards": len(steps["suspend_cards"]), "reposition_new": len(steps["reposition_new"]),
        "set_fields": dict(collections.Counter(r["field"] for r in steps["set_fields"])),
        "mode_audio_notes": len(spoken), "mode_audio_cards": len(audio_cards),
        "mode_audio_cards_active": sum(cd["cid"] not in suspended for cd in audio_cards),
        "mode_visual_notes": sum(r["add"] == ["mode::visual"] for r in steps["add_tags"]),
        "delete_decks": len(steps["delete_decks"]),
    }
    return {"kind": "p1-migration", "version": 1, "steps_in_order": STEPS, "summary": summary, "steps": steps}


def dump(p: dict) -> str:
    """Indented header; one row per line inside each step so diffs stay reviewable."""
    head = json.dumps({k: v for k, v in p.items() if k != "steps"}, indent=1, ensure_ascii=False)
    blocks = []
    for name, rows in p["steps"].items():
        body = ",\n".join("   " + json.dumps(r, ensure_ascii=False) for r in rows)
        blocks.append(f'  "{name}": [\n{body}\n  ]' if rows else f'  "{name}": []')
    return head[:-2] + ',\n "steps": {\n' + ",\n".join(blocks) + "\n }\n}\n"


def markdown(p: dict, source: str) -> str:
    s, st = p["summary"], p["steps"]
    esc = lambda x: x.replace("|", "\\|").replace("\n", " ")[:90]
    out = [f"# P1 migration dry run — {source}", "",
           ("Generated by `scripts/migrate.py`. Nothing was written to any collection. Apply order and the exact "
            "debug-console calls are in `docs/desktop-sessions/2026-09-p1-migration.md`."), "",
           "| Step | Rows | Notes |", "|---|---:|---|",
           f"| setup | {len(st['setup'])} | presets + target decks from `config/decks.yaml` |",
           f"| delete_notes | {s['delete_notes']} | duplicates; keeps the copy with review history, else the Aug 2026 copy |",
           (f"| change_notetype | {sum(s['change_notetype'].values())} | {s['change_notetype']}; "
            f"{s['card2_dropped']} formula→name Card 2s removed ({s['card2_dropped_reviews']} reviews) |"),
           f"| move_cards | {s['move_cards']} | unmapped decks: {s['unmapped_decks'] or 'none'} |",
           "| (retag) | — | `docs/runs/2026-09-23-retag.json` |",
           f"| suspend_cards | {s['suspend_cards']} | one order per multiplication pair |",
           f"| reposition_new | {s['reposition_new']} | kept multiplication facts to the front, easiest first |",
           f"| set_fields | {sum(s['set_fields'].values())} | {s['set_fields']} |",
           (f"| add_tags | {s['mode_audio_notes'] + s['mode_visual_notes']} | mode::audio {s['mode_audio_notes']} notes, "
            f"mode::visual {s['mode_visual_notes']} |"),
           f"| delete_decks | {s['delete_decks']} | refuses any deck that still has cards |", "",
           (f"**Walk-ready:** {s['mode_audio_cards']} cards tagged `mode::audio`, "
            f"{s['mode_audio_cards_active']} of them active (not suspended)."), "",
           "## Duplicates deleted", "", "| Delete | Reviews | Keep |", "|---|---:|---|"]
    out += [f"| `{r['nid']}` {esc(r['front'])} | {r['reviews']} | `{r['keep']}` {esc(r['keep_front'])} |"
            for r in st["delete_notes"]]
    groups = {r["group"]: r for r in st["change_notetype"]}
    for key, title in (("reversed_keep", "Reverse card kept"), ("reversed_drop", "Reverse card removed")):
        g = groups[key]
        out += ["", f"## {title} ({len(g['nids'])} notes)", ""]
        out += [f"- `{nid}` {esc(front)}" for nid, front in zip(g["nids"], g["fronts"], strict=True)]
    fronts = {r["nid"]: r["after"] for r in st["set_fields"] if r["field"] == "SpeakFront"}
    out += ["", "## Speech samples", "", "| Front speech | Back speech |", "|---|---|"]
    backs = {r["nid"]: r["after"] for r in st["set_fields"] if r["field"] == "SpeakBack"}
    seen: set[str] = set()
    for nid, f in fronts.items():
        kind = re.sub(r"\d+", "#", f)[:18]
        if kind not in seen:
            seen.add(kind)
            out.append(f"| {esc(f)} | {esc(backs[nid])} |")
    out += ["", "## Multiplication: suspended order (first 20)", "", "| Suspend | Keep |", "|---|---|"]
    out += [f"| {esc(r['front'])} | {esc(r['keep'])} |" for r in st["suspend_cards"][:20]]
    out += ["", "## Decks deleted when empty", ""] + [f"- {r['deck']}" for r in st["delete_decks"]]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    ap.add_argument("colpkg", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config" / "decks.yaml").read_text(encoding="utf-8"))
    p = plan(open_colpkg(args.colpkg), cfg)
    p["source"] = {"colpkg": args.colpkg.name, "sha256": hashlib.sha256(args.colpkg.read_bytes()).hexdigest()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dump(p), encoding="utf-8")
    args.out.with_suffix(".md").write_text(markdown(p, args.colpkg.name), encoding="utf-8")
    print(json.dumps(p["summary"], ensure_ascii=False))

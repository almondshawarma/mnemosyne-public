"""Build data/asl.yaml: a starter ASL vocabulary, most frequent signs first (PLAN §7).

The list comes from ASL-LEX 2.0 (Caselli et al.; CC BY-NC 4.0, https://osf.io/zpha4/). It rates each sign's frequency
as Deaf signers judged it, and cross-references ASL Signbank, whose sign videos the cards use
(scripts/asl_videos.py). Signs without a Signbank entry are left out, and two ASL-LEX entries for one Signbank sign
become one.

    python3 scripts/asl_vocab.py [N]        # N most frequent (default 150); rewrites data/asl.yaml

Edit data/asl.yaml by hand afterwards as you like: reorder, delete, or add signs by their Signbank id (the number in
https://aslsignbank.com/dictionary/gloss/<id>.html).
"""

import csv
import io
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SIGNDATA = "https://osf.io/download/9nygd/"   # ASL-LEX 2.0 signdata.csv
UA = "mnemosyne/1.0 (personal study; https://github.com/almondshawarma)"


def meaning(entry_id: str) -> str:
    """ASL-LEX EntryIDs: 'thank_you' → 'thank you', 'eat_1' → 'eat', 'this/it' stays."""
    base = entry_id.rsplit("_", 1)[0] if entry_id.rsplit("_", 1)[-1].isdigit() else entry_id
    return base.replace("_", " ")


def main(n: int) -> None:
    req = urllib.request.Request(SIGNDATA, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8", errors="replace"))))
    rows = [r for r in rows if r["SignBankReferenceID"].strip() not in ("", "NA")]
    rows.sort(key=lambda r: -float(r["SignFrequency(M)"] or 0))
    seen: set[str] = set()
    signs: list[dict[str, object]] = []
    for r in rows:
        sid, said = r["SignBankReferenceID"].strip(), meaning(r["EntryID"])
        if sid in seen or said in seen:   # one card per sign, and per meaning (the more frequent sign wins)
            continue
        seen |= {sid, said}
        signs.append({"meaning": meaning(r["EntryID"]), "signbank": int(float(sid)),
                      "also": r["SignBankEnglishTranslations"].strip(), "frequency": round(float(r["SignFrequency(M)"]), 2)})
        if len(signs) >= n:
            break
    head = ("# Starter ASL vocabulary, most frequent first (scripts/asl_vocab.py). From ASL-LEX 2.0 (CC BY-NC 4.0):\n"
            "# frequency is Deaf signers' rating, 1-7. `signbank` is the ASL Signbank gloss id; its video is the card's.\n"
            "# `meaning` is what the card asks for; `also` lists Signbank's other English equivalents.\n")
    body = yaml.safe_dump({"signs": signs}, allow_unicode=True, sort_keys=False, width=120)
    (ROOT / "data" / "asl.yaml").write_text(head + body, encoding="utf-8")
    print(f"{len(signs)} signs written to data/asl.yaml")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 150)

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml
from anki.collection import Collection
from anki.sound import TTSTag

from anki_apply import notetypes, retag, step
from migrate import ROOT, STEPS, dump, plan, uuid7
from retag import dump as retag_dump
from retag import plan as retag_plan

CFG = yaml.safe_load((ROOT / "config" / "decks.yaml").read_text(encoding="utf-8"))


def test_uuid7_is_versioned_deterministic_and_time_ordered() -> None:
    a, b = uuid7(1759278017600), uuid7(1787686569943)
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", a)
    assert a == uuid7(1759278017600) and a < b


def _plans(db: sqlite3.Connection, tmp_path: Path) -> tuple[dict[str, Any], Path, Path]:
    p = plan(db, CFG)
    p1, rt = tmp_path / "p1.json", tmp_path / "retag.json"
    p1.write_text(dump(p), encoding="utf-8")
    rt.write_text(retag_dump(retag_plan(db)), encoding="utf-8")
    assert json.loads(p1.read_text(encoding="utf-8")) == p
    db.close()
    return p, p1, rt


def test_plan_decisions(legacy_db: sqlite3.Connection, tmp_path: Path) -> None:
    p, _, _ = _plans(legacy_db, tmp_path)
    st = p["steps"]
    assert sorted(r["front"] for r in st["delete_notes"]) == ["Ideal gas law", r"\(\dfrac{d}{dx}\,\sin x\)"]
    # one order per product: the reviewed order wins, else larger × smaller
    assert sorted(r["front"] for r in st["suspend_cards"]) == [r"\(4\times3\)", r"\(8\times17\)"]
    groups = {r["group"]: len(r["nids"]) for r in st["change_notetype"]}
    assert groups == {"basic": 10, "reversed_keep": 3, "reversed_drop": 2, "cloze": 1}
    speech = {r["after"] for r in st["set_fields"] if r["field"] == "SpeakFront"}
    assert {"2 to the 12th", "17 times 8", "one quarter", "kilo, lowercase K"} <= speech
    assert p["summary"]["mode_visual_notes"] == 1


def test_full_migration_on_fixture(legacy_db: sqlite3.Connection, legacy_col_path: Path, tmp_path: Path) -> None:
    _, p1, rt = _plans(legacy_db, tmp_path)
    col = Collection(str(legacy_col_path))
    try:
        assert notetypes(col, dry_run=False)["created"]
        for name in STEPS[:4]:
            step(col, p1, name, dry_run=False)
        assert retag(col, rt)["verify_failed"] == []
        for name in STEPS[4:]:
            step(col, p1, name, dry_run=False)

        # re-running everything is a no-op
        assert notetypes(col)["to_create"] == []
        assert retag(col, rt, dry_run=True)["to_update"] == 0
        for name in STEPS[1:]:
            s = step(col, p1, name)
            assert not any(v for k, v in s.items() if k.startswith("to_") and k != "to_reposition"), s
            assert not s.get("drifted"), s

        assert {d.name for d in col.decks.all_names_and_ids()} <= set(CFG["decks"]) | {"Default", "Lang"}
        assert col.find_notes("MnemosyneID:") == []
        assert len(col.find_cards("note:M-Basic card:2")) == 3            # phrase, fraction, SI prefix
        assert len(col.find_notes("tag:mode::visual")) == 1
        assert all(t == t.lower() for t in col.tags.all())
        card = col.get_card(col.find_cards(r'"Front:\\(17\\times8\\)"')[0])
        spoken = lambda tags: [t.field_text for t in tags if isinstance(t, TTSTag)]
        assert spoken(card.question_av_tags()) == ["17 times 8"]
        assert spoken(card.answer_av_tags()) == ["136"]    # the back never replays the question
        assert col.get_empty_cards().notes == []
    finally:
        col.close()

import json
import sqlite3
from pathlib import Path

import pytest
from anki.collection import Collection

from anki_apply import retag
from retag import LEGACY, dump, map_tag, plan, retag_note


@pytest.mark.parametrize(("tag", "expected"), [
    ("Course::CHEM107::Exam2", ("course::chem107::exam2",)),
    ("Course::CHEM-107", ("course::chem107",)),
    ("MATH-251", ("course::math251",)),
    ("PHYS206", ("course::phys206",)),
    ("ai-generated", ("gen::ai",)),
    ("Math::Arithmetic::FractionsandDecimals", ("topic::math::arithmetic::fractions-and-decimals",)),
    ("Science::Metrology::SI-Symbols-&-Prefixes", ("topic::metrology::si-symbols-and-prefixes",)),
    ("Science::Physics::Modern-Physics::Quantum-&-Atomic-Physics",
     ("topic::physics::modern-physics::quantum-and-atomic-physics",)),
    ("Languages::Hindi::Devanagari-Script::Irregular-Ligatures", ("lang::hi::script::irregular-ligatures",)),
    ("Languages::Phonetics::Occlusive", ("ipa::manner::plosive",)),
    ("Languages::Phonetics::Retroflex", ("ipa::place::retroflex",)),
    ("Languages::Phonetics", ()),
    ("Languages::Random-Foreign-Phrases", ("topic::idioms",)),
    ("type::missed-question", ("type::trap",)),
    ("type::fundamental", ("type::fundamental",)),
    ("topic::math::linear-algebra", ("topic::math::linear-algebra",)),
    ("Music::Theory", None),
])
def test_map_tag(tag: str, expected: tuple[str, ...] | None) -> None:
    assert map_tag(tag) == expected


def test_retag_note_keeps_unmapped_and_adds_legacy() -> None:
    after, unmapped = retag_note(["Music::Theory", "Math::Algebra"])
    assert after == sorted([LEGACY, "Music::Theory", "topic::math::algebra"])
    assert unmapped == ["Music::Theory"]


def test_retag_is_idempotent() -> None:
    once, _ = retag_note(["Course::CHEM107::Exam2", "Languages::Phonetics", "ai-generated"])
    assert retag_note(once) == (once, [])


def _plan_file(db: sqlite3.Connection, tmp_path: Path) -> Path:
    p = plan(db)
    path = tmp_path / "retag.json"
    path.write_text(dump(p), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == p
    return path


def test_apply_plan_to_fixture(legacy_db: sqlite3.Connection, legacy_col_path: Path, tmp_path: Path) -> None:
    plan_path = _plan_file(legacy_db, tmp_path)
    legacy_db.close()
    rows = json.loads(plan_path.read_text(encoding="utf-8"))["notes"]
    col = Collection(str(legacy_col_path))
    try:
        assert retag(col, plan_path, dry_run=True)["to_update"] == len(rows)
        result = retag(col, plan_path)
        assert result["updated"] == len(rows) and result["verify_failed"] == []
        # Case survives even though Course::… was registered before (Anki re-cases otherwise).
        assert "course::chem107::exam2" in col.tags.all()
        assert all(t == t.lower() for t in col.tags.all())
        assert retag(col, plan_path)["to_update"] == 0  # re-running is a no-op
        col.undo()
        assert all(sorted(col.get_note(r["nid"]).tags) == r["before"] for r in rows)
    finally:
        col.close()


def test_apply_skips_drifted_notes(legacy_db: sqlite3.Connection, legacy_col_path: Path, tmp_path: Path) -> None:
    plan_path = _plan_file(legacy_db, tmp_path)
    legacy_db.close()
    nid = json.loads(plan_path.read_text(encoding="utf-8"))["notes"][0]["nid"]
    col = Collection(str(legacy_col_path))
    try:
        note = col.get_note(nid)
        note.tags = [*note.tags, "hand-edited"]
        col.update_note(note)
        result = retag(col, plan_path)
        assert result["drifted"] == [nid]
        assert "hand-edited" in col.get_note(nid).tags
    finally:
        col.close()

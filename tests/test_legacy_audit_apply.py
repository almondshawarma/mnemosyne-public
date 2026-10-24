import json
from pathlib import Path

from anki.collection import Collection

from anki_apply import step

STEPS = ["set_fields", "add_tags", "remove_tags", "suspend_cards", "suspend_replaced"]


def _plan(col: Collection, tmp_path: Path) -> Path:
    gas = col.get_note(col.find_notes("tag:Course::CHEM107::Exam2")[0])
    sin = col.get_note(col.find_notes("tag:Math::Calculus::Derivatives")[0])
    plan = {"kind": "legacy-audit", "version": 1, "steps_in_order": STEPS, "steps": {
        "set_fields": [{"nid": gas.id, "field": "Front", "before": "Ideal Gas Law", "after": "Ideal gas law, in moles"},
                       {"nid": sin.id, "field": "Back", "before": "not what it says", "after": "x"}],
        "add_tags": [{"nid": gas.id, "add": ["topic::chemistry::gas-laws"]}],
        "remove_tags": [{"nid": gas.id, "remove": ["Science::Chemistry::Gas-Laws"]}],
        "suspend_cards": [{"cid": cid} for cid in sin.card_ids()],
        "suspend_replaced": [],
    }}
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_legacy_audit_steps_apply_once_and_leave_drift(legacy_col_path: Path, tmp_path: Path) -> None:
    col = Collection(str(legacy_col_path))
    try:
        path = _plan(col, tmp_path)
        assert step(col, path, "set_fields")["to_update_notes"] == 1        # dry run by default
        assert col.find_notes('"Front:Ideal gas law, in moles"') == []
        for name in STEPS:
            step(col, path, name, dry_run=False)

        gas = col.get_note(col.find_notes('"Front:Ideal gas law, in moles"')[0])
        assert gas.has_tag("topic::chemistry::gas-laws") and not gas.has_tag("Science::Chemistry::Gas-Laws")
        assert all(c.queue == -1 for c in col.get_note(col.find_notes("tag:Math::Calculus::Derivatives")[0]).cards())

        again = {name: step(col, path, name) for name in STEPS}
        assert again["set_fields"]["to_update_notes"] == 0 and len(again["set_fields"]["drifted"]) == 1
        assert again["add_tags"]["to_tag"] == 0 and again["remove_tags"]["to_untag"] == 0
        assert again["suspend_cards"]["to_suspend"] == 0
    finally:
        col.close()

"""Structural checks on the golden set. Content quality is Aman's review; this keeps the form honest."""
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / ".claude" / "skills" / "mnemosyne-cards" / "golden"
NOTETYPES = ROOT / "desktop" / "notetypes"
PIPELINE_OWNED = {"MnemosyneID", "AudioFront", "AudioBack", "TargetAudio", "SoundAudio", "ExampleAudio", "PromptAudio", "Video"}
SPEECH = {"M-Basic": ("SpeakFront", "SpeakBack"), "M-Estimate": ("SpeakQuestion", "SpeakAnswer"),
          "M-Error": ("SpeakSituation", "SpeakFix"), "M-Cloze": ("SpeakText",), "M-Lang": ("SpeakMeaning",)}
DECKS = {"Now", "Core", "Lang::Spanish", "Lang::Hindi", "Lang::French", "Lang::IPA", "Reservoir"}
TAG = re.compile(r"^(course|topic|type|src|lang|ipa)(::[a-z0-9]+(-[a-z0-9]+)*)+$")
FILES = sorted(GOLDEN.glob("*.json"))


def fields_of(note_type: str) -> set[str]:
    spec = json.loads((NOTETYPES / f"{note_type}.json").read_text(encoding="utf-8"))
    return set(spec["fields"]) - PIPELINE_OWNED


def cards() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    out = []
    for f in FILES:
        doc = json.loads(f.read_text(encoding="utf-8"))
        out += [(f"{f.stem}#{i}", doc, c) for i, c in enumerate(doc["cards"])]
    return out


def test_golden_files_exist() -> None:
    assert len(FILES) >= 7


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_file_shape(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    assert not any(ord(ch) < 32 and ch not in "\n\r" for ch in raw), "control character (a mangled \\t or \\a?)"
    doc = json.loads(raw)
    assert doc["status"] in ("draft", "reviewed")
    assert {"kind", "course", "lecture", "ref"} <= doc["source"].keys()
    assert 5 <= len(doc["cards"]) <= 25, "canon 10: typical yield is 8–25"
    assert doc["skipped"], "calibration needs the negative space too"


@pytest.mark.parametrize(("cid", "doc", "card"), cards(), ids=lambda x: x if isinstance(x, str) else "")
def test_card(cid: str, doc: dict[str, Any], card: dict[str, Any]) -> None:
    nt, fields = card["note_type"], card["fields"]
    assert set(fields) <= fields_of(nt), f"unknown fields: {set(fields) - fields_of(nt)}"
    assert card["deck"] in DECKS
    assert card["mode"] in ("audio", "visual")
    for name in SPEECH.get(nt, ()):
        spoken = fields.get(name, "")
        assert bool(spoken) == (card["mode"] == "audio"), f"{name} must be filled iff mode is audio"
        assert "\\" not in spoken, "speech is plain text"
    for value in fields.values():
        assert "$" not in value, "canon 12: MathJax \\( \\) only"
        assert value.count("\\(") == value.count("\\)")
    assert all(TAG.match(t) for t in card["tags"]), card["tags"]
    course = f"course::{doc['source']['course'].lower()}::l{doc['source']['lecture']:02d}-"
    assert any(t.startswith(course) for t in card["tags"]), f"needs a {course}<slug> tag"
    assert sum(t.startswith("type::") for t in card["tags"]) == 1
    if nt == "M-Estimate":
        assert card["checks"], "canon 7: numeric cards carry checks"
    assert card["golden"]["canon"] and card["golden"]["from"]
    assert card["key"]
    if "review" in card:
        assert card["review"]["verdict"] in ("approve", "edit")


def test_keys_unique() -> None:
    keys = [c["key"] for f in FILES for doc in [json.loads(f.read_text(encoding="utf-8"))]
            for c in doc["cards"] + doc.get("rejected", []) + doc.get("superseded", [])]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_review_bookkeeping(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    for c in doc.get("rejected", []):
        assert c["review"]["verdict"] == "reject" and c["review"]["reason"], "a rejection teaches only with a reason"
    live = {c["key"] for c in doc["cards"]}
    for c in doc.get("superseded", []):
        assert c["replaced_by"] and set(c["replaced_by"]) <= live
    reviewed = all("review" in c for c in doc["cards"])
    assert (doc["status"] == "reviewed") == reviewed, "status tracks whether every card has a review"

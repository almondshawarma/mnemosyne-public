"""The clip toolkit (clips.toolkit): what a chat reads before writing a scene, live from the Obelisk's copies."""
from pathlib import Path

from mnemosyne import clips

ROOT = Path(__file__).resolve().parent.parent


def amanim_like(tmp: Path) -> Path:
    repo = tmp / "amanim"
    (repo / "amanim").mkdir(parents=True)
    (repo / "amanim" / "__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")
    (repo / "amanim" / "motion.py").write_text(
        'def lorentz_path(q_over_m, E=None, *, dt=0.002):\n    """A charged particle\'s path.\n\n    More."""\n\n'
        'def _private():\n    pass\n', encoding="utf-8")
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / ".git" / "refs" / "heads" / "main").write_text("ba56d4a1234567890\n", encoding="utf-8")
    return repo


def test_the_toolkit_reads_amanim_and_the_house_style_live(tmp_path: Path) -> None:
    kit = clips.toolkit(ROOT / "scenes", amanim_like(tmp_path), [])
    assert kit["amanim"]["version"] == "0.2.0" and kit["amanim"]["commit"] == "ba56d4a123"
    assert kit["amanim"]["api"] == [{"module": "amanim.motion", "signature": "lorentz_path(q_over_m, E=None, *, dt=0.002)",
                                     "doc": "A charged particle's path."}]
    names = {a["signature"].split("(")[0] for a in kit["mn_style"]}
    assert {"tex", "words", "backed", "class MnScene"} <= names
    assert "ACCENT" in kit["palette"]["roles"] and "hex" in kit["palette"]["note"]


def test_harvest_marks_are_collected_from_published_and_repo_scenes(tmp_path: Path) -> None:
    pub = tmp_path / "render" / "one-circuit"
    pub.mkdir(parents=True)
    (pub / "scene.py").write_text("# harvest: a battery symbol\ndef cell(a, b):\n    pass\n", encoding="utf-8")
    (pub / "render.json").write_text("{}", encoding="utf-8")
    found = clips.harvest([tmp_path], ROOT / "scenes")
    assert {"in": "one-circuit", "what": "a battery symbol", "code": "def cell(a, b):"} in found
    assert any(f["in"].startswith("phys207/") for f in found)        # the repo's own scenes carry marks too


def test_without_amanim_the_toolkit_still_answers(tmp_path: Path) -> None:
    kit = clips.toolkit(None, None, [])
    assert kit["amanim"] is None and "mn_style" not in kit and kit["harvest"] == []


def test_the_gallery_is_listed_and_each_entry_returns_its_code_and_stills() -> None:
    kit = clips.toolkit(ROOT / "scenes", None, [])
    names = {g["name"]: g for g in kit["gallery"]}
    assert {"gallery-palette", "gallery-plane-and-line", "gallery-circuit"} <= set(names)
    assert all(g["technique"] and g["stills"] >= 1 for g in kit["gallery"])       # every demo says what it shows
    entry = clips.report([], None, "gallery-plane-and-line", scenes=ROOT / "scenes")
    assert entry["state"] == "gallery" and "class PlaneAndLine(MnScene3D)" in entry["code"]
    assert entry["images"] and entry["images"][0]["label"].startswith("still: end of section")


def test_an_unknown_gallery_entry_is_not_found() -> None:
    import pytest
    with pytest.raises(LookupError):
        clips.report([], None, "gallery-nope", scenes=ROOT / "scenes")

"""Clips a chat submits as code (clips.submit, scripts/render_runner.py): validation, limits, status, and the
runner's checks on what a sandboxed render leaves behind before anything is published."""
import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mnemosyne import clips
from mnemosyne.api import create_app
from mnemosyne.config import Settings
from mnemosyne.ledger import db
from test_service import APPROVE, PROPOSE, FakeCore, bearer

ROOT = Path(__file__).resolve().parent.parent
SCENE = 'from mn_style import *\n\nclass OneCircuit(MnScene):\n    def construct(self):\n        self.wait(1)\n'


def runner() -> Any:  # the script module: its names are checked by running it, not by pyright
    spec = importlib.util.spec_from_file_location("render_runner", ROOT / "scripts" / "render_runner.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def place(tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path]:
    con = db.connect(tmp_path / "helios.db")
    db.migrate(con)
    media, queue = tmp_path / "media", tmp_path / "queue"
    (media / "render").mkdir(parents=True)
    queue.mkdir()
    return con, media, queue


def test_a_scene_is_queued_with_its_code_and_trusted_metadata(place: tuple[sqlite3.Connection, Path, Path]) -> None:
    con, media, queue = place
    made = clips.submit(con, media, queue, SCENE, "Ch26 #4: one circuit, one matrix", ["PHYS207", "math309"],
                        ["physics::em::circuits"], "the tree, then the loops")
    assert made == {"slug": "ch26-4-one-circuit-one-matrix", "scene": "OneCircuit", "state": "waiting",
                    "next": made["next"]}
    sub = media / "render" / ".submitted" / made["slug"]
    assert (sub / "scene.py").read_text(encoding="utf-8") == SCENE
    meta = json.loads((sub / "submission.json").read_text(encoding="utf-8"))
    assert meta["courses"] == ["phys207", "math309"] and meta["origin"] == "chat" and meta["scene"] == "OneCircuit"
    assert (queue / f"{made['slug']}.json").exists()
    again = clips.submit(con, media, queue, SCENE, "Ch26 #4: one circuit, one matrix", [], [], "")
    assert again["slug"] == "ch26-4-one-circuit-one-matrix-2"       # a new title-alike gets its own name


def test_bad_submissions_are_refused_with_a_reason(place: tuple[sqlite3.Connection, Path, Path]) -> None:
    con, media, queue = place
    for code, title, courses, why in [
        ("def broken(:\n", "A title", [], "doesn't parse"),
        ("x = 1\n", "A title", [], "no Scene class"),
        (SCENE, "ab", [], "title"),
        (SCENE, "A title", ["physics 207"], "courses"),
        ("#" * (clips.MAX_CODE + 1), "A title", [], "KB"),
    ]:
        with pytest.raises(clips.SubmitError, match=why):
            clips.submit(con, media, queue, code, title, courses, [], "")
    with pytest.raises(clips.SubmitError, match="isn't set up"):
        clips.submit(con, media, None, SCENE, "A title", [], [], "")


def test_replace_only_touches_a_clip_a_chat_made(place: tuple[sqlite3.Connection, Path, Path]) -> None:
    con, media, queue = place
    hand = media / "render" / "gauss-flux"
    hand.mkdir()
    (hand / "render.json").write_text(json.dumps({"title": "Gauss"}), encoding="utf-8")   # made in a session
    with pytest.raises(clips.SubmitError, match="replace only"):
        clips.submit(con, media, queue, SCENE, "Gauss again", [], [], "", replace="gauss-flux")
    made = clips.submit(con, media, queue, SCENE, "Mine", [], [], "")
    with pytest.raises(clips.SubmitError, match="already rendering"):
        clips.submit(con, media, queue, SCENE, "Mine", [], [], "", replace=made["slug"])
    (queue / f"{made['slug']}.json").rename(queue / f"{made['slug']}.failed")
    assert clips.submit(con, media, queue, SCENE + "# fixed\n", "Mine", [], [], "", replace=made["slug"])["slug"] \
        == made["slug"]
    assert not (queue / f"{made['slug']}.failed").exists()


def test_limits_per_day_and_in_the_queue(place: tuple[sqlite3.Connection, Path, Path]) -> None:
    con, media, queue = place
    for k in range(clips.QUEUE_MAX):
        clips.submit(con, media, queue, SCENE, f"Clip number {k}", [], [], "")
    with pytest.raises(clips.SubmitError, match="already waiting"):
        clips.submit(con, media, queue, SCENE, "One more", [], [], "")
    for p in queue.glob("*.json"):
        p.unlink()
    for k in range(clips.PER_DAY - clips.QUEUE_MAX):
        clips.submit(con, media, queue, SCENE, f"Later clip {k}", [], [], "")
        for p in queue.glob("*.json"):
            p.unlink()
    with pytest.raises(clips.SubmitError, match="24 hours"):
        clips.submit(con, media, queue, SCENE, "Too many", [], [], "")


def published(media: Path, slug: str, origin: str = "chat") -> Path:
    d = media / "render" / slug
    (d / "stills").mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000)
    (d / "scene.py").write_text(SCENE, encoding="utf-8")
    (d / "stills" / "00-count.jpg").write_bytes(b"\xff\xd8jpg")
    (d / "render.json").write_text(json.dumps({
        "title": "One circuit", "origin": origin, "duration": 12.0, "layout_warnings": ["MN-LAYOUT [count] x"],
        "sections": [{"name": "count", "start": 0, "duration": 6, "still": "00-count.png"},
                     {"name": "tree", "start": 6, "duration": 6, "still": "01-tree.png"}],
        "rendered_at": "2026-10-13T20:00:00Z"}), encoding="utf-8")
    return d


def test_status_says_what_to_do_next(place: tuple[sqlite3.Connection, Path, Path]) -> None:
    con, media, queue = place
    made = clips.submit(con, media, queue, SCENE, "One circuit", [], [], "")
    assert clips.report([media], queue, made["slug"])["state"] == "waiting"
    (queue / f"{made['slug']}.json").rename(queue / f"{made['slug']}.failed")
    (queue / f"{made['slug']}.failed").write_text("NameError: name 'Axes2' is not defined", encoding="utf-8")
    failed = clips.report([media], queue, made["slug"])
    assert failed["state"] == "failed" and "Axes2" in failed["error"] and "replace" in failed["next"]
    (queue / f"{made['slug']}.failed").unlink()
    published(media, made["slug"])
    done = clips.report([media], queue, made["slug"], "http://<obelisk>:8110")
    assert done["state"] == "published" and done["watch"] == f"http://<obelisk>:8110/watch#{made['slug']}"
    assert done["layout_warnings"] and len(done["images"]) == 1 and "count" in done["images"][0]["label"]
    with pytest.raises(LookupError):
        clips.report([media], queue, "../x")


def test_the_runner_publishes_only_what_checks_out(tmp_path: Path) -> None:
    rr = runner()
    out = tmp_path / "out"
    (out / "stills").mkdir(parents=True)
    (out / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000)
    (out / "stills" / "00-count.png").write_bytes(b"png")
    (out / "render.json").write_text(json.dumps({"title": "<script>evil</script>", "duration": 5,
                                                 "sections": [{"name": "count", "start": 0, "duration": 5,
                                                               "still": "00-count.png"},
                                                              {"name": "x", "still": "../../etc/passwd"}]}),
                                     encoding="utf-8")
    rendered = rr.checked(out)
    src = tmp_path / "src"
    src.mkdir()
    (src / "scene.py").write_text(SCENE, encoding="utf-8")
    rr.RENDER = tmp_path / "render"
    rr.RENDER.mkdir()
    rr.publish("one-circuit", src, {"title": "One circuit", "scene": "OneCircuit", "origin": "chat"}, out, rendered)
    meta = json.loads((rr.RENDER / "one-circuit" / "render.json").read_text(encoding="utf-8"))
    assert meta["title"] == "One circuit"                          # the submission's title, not the container's
    assert [s["still"] for s in meta["sections"]] == ["00-count.png"]
    assert (rr.RENDER / "one-circuit" / "scene.py").exists() and (rr.RENDER / "one-circuit" / "mn_style.py").exists()
    (out / "stills" / "evil.sh").write_bytes(b"#!")
    with pytest.raises(ValueError, match="stills"):
        rr.checked(out)
    (out / "stills" / "evil.sh").unlink()
    (out / "clip.mp4").write_bytes(b"<html>" + b"\x00" * 2000)
    with pytest.raises(ValueError, match="MP4"):
        rr.checked(out)


def test_the_routes_submit_and_report_with_the_propose_token(tmp_path: Path) -> None:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    media, queue = tmp_path / "media", tmp_path / "queue"
    (media / "render").mkdir(parents=True)
    queue.mkdir()
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve", media_dir=media, render_queue=queue,
                        public_url="http://<obelisk>:8110")
    client = TestClient(create_app(settings, core=FakeCore(), schedule=False))
    h = bearer(PROPOSE)
    r = client.post("/clips", json={"code": SCENE, "title": "One circuit", "courses": ["phys207"]}, headers=h)
    assert r.status_code == 200 and r.json()["slug"] == "one-circuit"
    assert client.post("/clips", json={"code": "x=1", "title": "One circuit"}, headers=h).status_code == 422
    assert client.get("/clips/one-circuit", headers=h).json()["state"] == "waiting"
    assert client.get("/clips/nope", headers=h).status_code == 404
    assert client.get("/clips", headers=h).json()["watch"] == "http://<obelisk>:8110/watch"
    assert client.post("/clips", json={"code": SCENE, "title": "x y z"}).status_code == 401


def test_the_house_scene_classes_are_found_without_a_clip_block() -> None:
    for base in ("MnScene", "MnScene3D", "MnZoomScene", "ThreeDScene", "manim.Scene"):
        assert clips.scene_class(f"from mn_style import *\n\nclass Planes({base}):\n    pass\n") == "Planes"

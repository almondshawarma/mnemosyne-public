"""/watch: animated clips rendered by scripts/render.py, listed and served with the approve token, opens counted."""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemosyne import clips
from mnemosyne.api import create_app
from mnemosyne.config import Settings
from mnemosyne.ledger import db
from test_service import APPROVE, PROPOSE, FakeCore, bearer

ROOT = Path(__file__).resolve().parent.parent


def clip(media: Path, slug: str, *, rendered_at: str = "2026-10-13T12:00:00Z", scene: bool = True) -> Path:
    d = media / "render" / slug
    (d / "stills").mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (d / "render.json").write_text(json.dumps({
        "title": f"Clip {slug}", "courses": ["phys207"], "topics": ["physics::em::gauss"], "watch_for": "the count",
        "duration": 30.5, "sections": [{"name": "inside", "start": 0.0, "duration": 6.8, "still": "00-inside.png"}],
        "rendered_at": rendered_at}), encoding="utf-8")
    if scene:
        (d / "scene.py").write_text("# why this order\nclass S: ...\n", encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path: Path) -> tuple[TestClient, Path, Settings]:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    media = tmp_path / "media"
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve", media_dir=media)
    return TestClient(create_app(settings, core=FakeCore(), schedule=False)), media, settings


def test_drafts_half_written_folders_and_bad_names_are_not_listed(tmp_path: Path) -> None:
    clip(tmp_path, "gauss-flux", rendered_at="2026-10-13T12:00:00Z")
    clip(tmp_path, "linear-map", rendered_at="2026-10-13T13:00:00Z")
    clip(tmp_path / "render" / ".drafts", "mesh-matrix")       # a draft: render/.drafts/render/mesh-matrix
    (tmp_path / "render" / "no-video").mkdir()
    (tmp_path / "render" / "no-video" / "render.json").write_text("{}", encoding="utf-8")
    (tmp_path / "render" / "Bad Name").mkdir()
    assert [c["slug"] for c in clips.library([tmp_path])] == ["linear-map", "gauss-flux"]


def test_a_slug_cannot_climb_out_of_the_media_folder(tmp_path: Path) -> None:
    clip(tmp_path, "gauss-flux")
    assert clips.folder([tmp_path], "gauss-flux") is not None
    for bad in ("../gauss-flux", "..", ".drafts", "Gauss", "a/b", ""):
        assert clips.folder([tmp_path], bad) is None


def test_the_page_is_open_but_clips_need_the_approve_token(env: tuple[TestClient, Path, Settings]) -> None:
    client, media, _ = env
    clip(media, "gauss-flux")
    assert client.get("/watch").status_code == 200
    assert client.get("/watch/clips").status_code == 401
    assert client.get("/watch/clips", headers=bearer(PROPOSE)).status_code == 401
    listed = client.get("/watch/clips", headers=bearer(APPROVE)).json()["clips"]
    assert [c["slug"] for c in listed] == ["gauss-flux"] and listed[0]["has_scene"] is True
    scene = client.get("/watch/clips/gauss-flux/scene", headers=bearer(APPROVE))
    assert scene.status_code == 200 and "why this order" in scene.text
    assert client.get("/watch/clips/nope/video", headers=bearer(APPROVE)).status_code == 404


def test_each_open_is_counted_with_where_it_came_from(env: tuple[TestClient, Path, Settings]) -> None:
    client, media, settings = env
    clip(media, "gauss-flux")
    for via in ("card", "card", "page", "somewhere-odd"):
        r = client.get("/watch/clips/gauss-flux/video", params={"via": via}, headers=bearer(APPROVE))
        assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    con = db.connect(settings.ledger)
    week = clips.views(con, "2000-01-01", "2100-01-01")
    assert week["opens"] == 4
    assert week["clips"] == [{"slug": "gauss-flux", "opens": 4, "card": 2, "page": 2}]   # an unknown 'via' is 'page'
    assert clips.views(con, "2100-01-01", "2100-01-02") == {"opens": 0, "clips": []}


def test_views_need_the_migration(tmp_path: Path) -> None:
    con = db.connect(tmp_path / "helios.db")
    db.migrate(con)
    assert con.execute("SELECT name FROM sqlite_master WHERE name = 'clip_view'").fetchone() is not None
    assert isinstance(con, sqlite3.Connection)


# ---------- keeping, clearing, and rendering again (Aman, 2026-10-13) ----------

def migrated(tmp_path: Path) -> sqlite3.Connection:
    con = db.connect(tmp_path / "helios.db")
    db.migrate(con)
    return con


def test_a_video_goes_days_after_its_last_open_and_the_code_stays(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    con = migrated(tmp_path)
    d = clip(tmp_path, "gauss-flux", rendered_at="2026-10-01T12:00:00Z")
    clip(tmp_path, "linear-map", rendered_at="2026-10-01T12:00:00Z")
    con.execute("INSERT INTO clip_view (slug, via, viewed_at) VALUES ('linear-map', 'card', '2026-10-10T08:00:00+00:00')")
    con.commit()
    by = {c["slug"]: c for c in clips.library([tmp_path], con, None, 14)}
    assert by["gauss-flux"]["expires_at"].startswith("2026-10-15T12:00")    # 14 days after rendering
    assert by["linear-map"]["expires_at"].startswith("2026-10-24T08:00")    # 14 days after the last open
    assert clips.expire(con, [tmp_path], 14, datetime(2026, 10, 16, tzinfo=UTC)) == ["gauss-flux"]
    assert not (d / "clip.mp4").exists() and (d / "scene.py").exists() and (d / "render.json").exists()
    after = {c["slug"]: c for c in clips.library([tmp_path], con, None, 14)}
    assert after["gauss-flux"]["has_video"] is False and after["gauss-flux"]["cleared_at"]
    assert after["linear-map"]["has_video"] is True
    assert clips.expire(con, [tmp_path], 14, datetime(2026, 10, 16, tzinfo=UTC)) == []   # nothing twice


def test_his_choice_per_clip_beats_the_default_and_forever_never_goes(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    con = migrated(tmp_path)
    clip(tmp_path, "gauss-flux", rendered_at="2026-10-01T12:00:00Z")
    clip(tmp_path, "linear-map", rendered_at="2026-10-01T12:00:00Z")
    clips.set_keep(con, "gauss-flux", 30, False)
    clips.set_keep(con, "linear-map", None, True)
    by = {c["slug"]: c for c in clips.library([tmp_path], con, None, 14)}
    assert by["gauss-flux"]["keep"]["days"] == 30 and by["gauss-flux"]["keep"]["default"] is False
    assert by["linear-map"]["expires_at"] is None and by["linear-map"]["keep"]["forever"] is True
    assert clips.expire(con, [tmp_path], 14, datetime(2027, 6, 1, tzinfo=UTC)) == ["gauss-flux"]
    clips.set_keep(con, "gauss-flux", None, False)                           # back to the default
    assert clips.library([tmp_path], con, None, 14)[0]["keep"]["default"] is True
    with pytest.raises(ValueError):
        clips.set_keep(con, "gauss-flux", 0, False)


def test_the_default_comes_from_config(tmp_path: Path) -> None:
    assert clips.default_keep(ROOT / "config") == 14
    (tmp_path / "clips.yaml").write_text("keep_days: 30\n", encoding="utf-8")
    assert clips.default_keep(tmp_path) == 30
    assert clips.default_keep(tmp_path / "missing") == clips.DEFAULT_KEEP


def test_rendering_again_needs_the_code_and_queues_once(tmp_path: Path) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    clip(tmp_path, "gauss-flux")
    clip(tmp_path, "no-code", scene=False)
    assert clips.request_render([tmp_path], queue, "gauss-flux") == "waiting"
    assert clips.request_render([tmp_path], queue, "gauss-flux") == "waiting"
    assert [p.name for p in queue.iterdir()] == ["gauss-flux.json"]
    assert json.loads((queue / "gauss-flux.json").read_text(encoding="utf-8"))["slug"] == "gauss-flux"
    with pytest.raises(LookupError):
        clips.request_render([tmp_path], queue, "no-code")
    with pytest.raises(LookupError):
        clips.request_render([tmp_path], queue, "../etc")
    (queue / "gauss-flux.json").rename(queue / "gauss-flux.failed")
    assert clips.render_state(queue, "gauss-flux") == "failed"
    assert clips.request_render([tmp_path], queue, "gauss-flux") == "waiting" and not (queue / "gauss-flux.failed").exists()


def test_the_page_routes_keep_clear_and_render(env: tuple[TestClient, Path, Settings]) -> None:
    client, media, _ = env
    d = clip(media, "gauss-flux")
    h = bearer(APPROVE)
    r = client.put("/watch/clips/gauss-flux/keep", json={"days": 30, "forever": False}, headers=h)
    assert r.status_code == 200 and r.json()["keep"]["days"] == 30
    assert client.put("/watch/clips/gauss-flux/keep", json={"days": 0}, headers=h).status_code == 422
    assert client.put("/watch/clips/nope/keep", json={"days": 7}, headers=h).status_code == 404
    assert client.post("/watch/clips/gauss-flux/clear", headers=bearer(PROPOSE)).status_code == 401
    r = client.post("/watch/clips/gauss-flux/clear", headers=h)
    assert r.status_code == 200 and r.json()["has_video"] is False and not (d / "clip.mp4").exists()
    assert client.get("/watch/clips/gauss-flux/video", headers=h).status_code == 404
    assert client.post("/watch/clips/gauss-flux/render", headers=h).status_code == 503     # no renderer here
    assert [c["slug"] for c in client.get("/watch/clips", headers=h).json()["clips"]] == ["gauss-flux"]

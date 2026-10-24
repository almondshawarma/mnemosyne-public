"""Watched lecture videos (videos.py): search, spans with contact sheets, frames, and the watcher's queue."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemosyne import videos
from mnemosyne.api import create_app
from mnemosyne.config import Settings
from test_service import APPROVE, PROPOSE, FakeCore, bearer

ROOT = Path(__file__).resolve().parent.parent
VID = "6-wh6yvk6uc"


def watched(media: Path, vid: str = VID) -> Path:
    """A watched video as the watcher leaves it: 12 minutes, captions every 30 s, a frame a minute, 2 sheets."""
    d = media / "watch" / vid
    (d / "frames").mkdir(parents=True)
    (d / "sheets").mkdir()
    (d / "meta.json").write_text(json.dumps({
        "id": vid, "title": "12. Graphs, Networks, Incidence Matrices", "channel": "MIT OpenCourseWare",
        "duration": 720, "license": "Creative Commons BY-NC-SA", "captions": "human",
        "chapters": [{"start": 0, "title": "Intro"}, {"start": 300, "title": "Rank of the Matrix"}]}), encoding="utf-8")
    cues = [{"start": t, "end": t + 30, "text": f"cue at {t}"} for t in range(0, 720, 30)]
    cues[11]["text"] = "the rank is n minus one because of the constant vector in the null space"
    (d / "transcript.json").write_text(json.dumps(cues), encoding="utf-8")
    frames = []
    for t in range(0, 720, 60):
        name = f"f-{t:05d}.jpg"
        (d / "frames" / name).write_bytes(b"\xff\xd8frame" + str(t).encode())
        frames.append({"t": float(t), "at": videos.mmss(t), "file": name, "spoken": f"said near {t}"})
    (d / "frames.json").write_text(json.dumps(frames), encoding="utf-8")
    sheets = [{"file": "sheet-01.jpg", "from": 0.0, "to": 300.0}, {"file": "sheet-02.jpg", "from": 360.0, "to": 660.0}]
    for s in sheets:
        (d / "sheets" / s["file"]).write_bytes(b"\xff\xd8sheet")
    (d / "sheets.json").write_text(json.dumps(sheets), encoding="utf-8")
    return d


def test_links_and_ids_are_youtube_only() -> None:
    for good in (VID, f"https://www.youtube.com/watch?v={VID}&t=30", f"https://youtu.be/{VID}",
                 f"https://m.youtube.com/shorts/{VID}"):
        assert videos.video_id(good) == VID
    for bad in ("https://evil.example/watch?v=6-wh6yvk6uc", "not a video", "", "6-wh6yvk6u"):
        with pytest.raises(videos.VideoError):
            videos.video_id(bad)


def test_search_finds_the_paragraph_and_links_its_moment(tmp_path: Path) -> None:
    watched(tmp_path)
    found = videos.search([tmp_path], "rank constant")
    assert found["total"] == 1
    hit = found["hits"][0]
    assert hit["video"] == VID and "rank is n minus one" in hit["text"]
    assert hit["link"].startswith(f"https://youtu.be/{VID}?t=")
    assert videos.search([tmp_path], "eigenvalue")["total"] == 0
    with pytest.raises(videos.VideoError):
        videos.search([tmp_path], "x")


def test_a_short_span_sends_its_sheets_and_a_whole_lecture_does_not(tmp_path: Path) -> None:
    watched(tmp_path)
    short = videos.span([tmp_path], VID, "4:00", "9:00")
    assert short["span"] == "4:00-9:00" and short["chapters"] == [{"at": "5:00", "title": "Rank of the Matrix"}]
    assert [s["file"] for s in short["sheets"]] == ["sheet-01.jpg", "sheet-02.jpg"] and len(short["images"]) == 2
    assert all(p["t"] < 540 for p in short["transcript"]) and short["transcript"][0]["t"] >= 210
    whole = videos.span([tmp_path], VID, images="no")
    assert whole["images"] == [] and len(whole["transcript"]) > len(short["transcript"])
    assert videos.span([tmp_path], VID)["images"] != []        # 12 minutes is short enough for "auto"
    with pytest.raises(videos.VideoError):
        videos.span([tmp_path], VID, "9:00", "4:00")
    with pytest.raises(videos.VideoError):
        videos.span([tmp_path], "aaaaaaaaaaa")


def test_frames_are_the_nearest_kept_ones_and_capped(tmp_path: Path) -> None:
    watched(tmp_path)
    got = videos.frames([tmp_path], VID, ["1:58", "2:01", "10:00"])
    assert [f["at"] for f in got["frames"]] == ["2:00", "10:00"]       # 1:58 and 2:01 are the same kept frame
    assert len(got["images"]) == 2 and got["frames"][0]["spoken"] == "said near 120"
    assert len(videos.frames([tmp_path], VID, [str(60 * k) for k in range(10)])["images"]) == videos.FRAMES


def test_requests_queue_once_retry_failures_and_stop_at_the_limit(tmp_path: Path) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    watched(tmp_path)
    assert videos.request([tmp_path], queue, VID)["state"] == "watched"
    other = "abcdefghijk"
    assert videos.request([tmp_path], queue, f"https://youtu.be/{other}")["state"] == "waiting"
    assert videos.request([tmp_path], queue, other)["state"] == "waiting"
    assert len(list(queue.glob("*.json"))) == 1
    (queue / f"{other}.json").rename(queue / f"{other}.failed")
    (queue / f"{other}.failed").write_text("yt-dlp couldn't read the video", encoding="utf-8")
    assert videos.status([tmp_path], queue, other) == "failed"
    assert videos.library([tmp_path], queue)["waiting"][0]["error"].startswith("yt-dlp")
    assert videos.request([tmp_path], queue, other)["state"] == "waiting"
    assert not (queue / f"{other}.failed").exists()
    for k in range(videos.QUEUE_MAX - 1):
        videos.request([tmp_path], queue, f"queued{k:05d}")
    with pytest.raises(videos.VideoError):
        videos.request([tmp_path], queue, "onetoomany1")
    with pytest.raises(videos.VideoError):
        videos.request([tmp_path], None, "onetoomany1")


def test_the_routes_read_and_request_with_the_propose_token(tmp_path: Path) -> None:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    media, queue = tmp_path / "media", tmp_path / "queue"
    queue.mkdir()
    watched(media)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve", media_dir=media, watch_queue=queue)
    client = TestClient(create_app(settings, core=FakeCore(), schedule=False))
    h = bearer(PROPOSE)
    assert client.get("/videos").status_code == 401
    assert client.get("/videos", headers=h).json()["videos"][0]["chapters"][1]["at"] == "5:00"
    assert client.get("/videos/search", params={"q": "rank"}, headers=h).json()["total"] == 1
    span = client.get(f"/videos/{VID}/watch", params={"start": "4:00", "end": "6:00"}, headers=h).json()
    assert span["images"] and span["transcript"]
    assert client.get(f"/videos/{VID}/watch", params={"start": "x"}, headers=h).status_code == 422
    assert len(client.get(f"/videos/{VID}/frames", params={"at": "2:00,3:00"}, headers=h).json()["images"]) == 2
    r = client.post("/videos/watch", json={"video": "https://www.youtube.com/watch?v=abcdefghijk"}, headers=h)
    assert r.status_code == 200 and r.json()["state"] == "waiting" and (queue / "abcdefghijk.json").exists()
    assert client.post("/videos/watch", json={"video": "https://evil.example/x"}, headers=h).status_code == 422


def studied(media: Path, vid: str = VID, name: str = "80-110") -> Path:
    """A finished study as watch_job.study_main leaves it: two shots, three filmstrips, two dense frames."""
    d = media / "watch" / vid / "study" / name
    (d / "sheets").mkdir(parents=True)
    (d / "frames").mkdir()
    for f in ("motion.png", "shots-01.jpg", "sheets/strip-01.jpg", "sheets/strip-02.jpg", "sheets/strip-03.jpg",
              "frames/s-0081.00.jpg", "frames/s-0095.50.jpg"):
        (d / f).write_bytes(b"img " + f.encode())
    shot = {"n": 1, "at": "1:20.00", "duration": 15.5, "holds": 2, "moves": 1, "longest_hold": 6.0,
            "tone": {"lightness": 4.0, "contrast": 10.0, "dark": 0.0, "light": 25.0}, "ink_contrast": 9.3,
            "spoken": "watch every point move", "palette": [{"hex": "#000000", "share": 0.8}],
            "composition": {"coverage": 0.1, "nearest": "center"}}
    (d / "study.json").write_text(json.dumps({
        "video": {"id": vid, "title": "Linear transformations"},
        "attribution": {"credit": "3Blue1Brown, “Linear transformations”, 1:20–1:50"},
        "span": {"start": 80, "end": 110, "at": "1:20.00-1:50.00", "seconds": 30}, "rates": {"frames": 6, "curve": 30},
        "summary": {"shots": 2}, "limits": "frames, not video", "shots": [shot, {**shot, "n": 2, "at": "1:35.50"}],
        "segments": [{"kind": "hold", "start": 80, "end": 86, "duration": 6}],
        "transcript": [{"start": 81.0, "end": 84.0, "text": "watch every point move"}],
        "files": {"shot_sheets": ["shots-01.jpg"], "strips": [
            {"file": "strip-01.jpg", "from": 80.0, "to": 89.8, "frames": 60},
            {"file": "strip-02.jpg", "from": 90.0, "to": 99.8, "frames": 60},
            {"file": "strip-03.jpg", "from": 100.0, "to": 109.8, "frames": 60}]}}), encoding="utf-8")
    (d / "frames.json").write_text(json.dumps([{"t": 81.0, "at": "1:21.00", "file": "s-0081.00.jpg", "shot": 1},
                                               {"t": 95.5, "at": "1:35.50", "file": "s-0095.50.jpg", "shot": 2}]),
                                   encoding="utf-8")
    return d


def test_a_study_is_queued_once_then_read_with_its_plot_and_shots(tmp_path: Path) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    got = videos.study([tmp_path], queue, f"https://youtu.be/{VID}", "1:20", "1:50", fps=30)
    assert got["state"] == "waiting" and got["span"] == "80-110"
    ask = json.loads((queue / f"{VID}@80-110.json").read_text(encoding="utf-8"))
    assert (ask["start"], ask["end"], ask["fps"]) == (80, 110, 12.0)      # fps capped
    assert videos.study([tmp_path], queue, VID, "1:20", "1:50")["state"] == "waiting"
    assert videos.library([tmp_path], queue)["waiting"] == [{"id": VID, "state": "waiting", "study": "80-110"}]
    (queue / f"{VID}@80-110.json").unlink()
    studied(tmp_path)
    read = videos.study([tmp_path], queue, VID, "80", "110")
    assert read["state"] == "studied" and read["attribution"]["credit"].startswith("3Blue1Brown")
    assert [s["palette"] for s in read["shots"]] == [["#000000 80%"]] * 2
    assert [im.get("format") for im in read["images"]] == ["png", None]   # the motion plot, the shot sheet
    assert videos.library([tmp_path])["studies"][0]["span"] == "1:20.00-1:50.00"


def test_a_study_looks_closer_at_a_stretch_or_at_times(tmp_path: Path) -> None:
    studied(tmp_path)
    stretch = videos.study([tmp_path], None, VID, "1:20", "1:50", look="1:32-1:41")
    assert [im["label"][:23] for im in stretch["images"][2:]] == ["filmstrip 1:30-1:39 (ea", "filmstrip 1:40-1:49 (ea"]
    times = videos.study([tmp_path], None, VID, "1:20", "1:50", look="1:21,1:22,1:35")
    assert [im["label"] for im in times["images"][2:]] == ["frame at 1:21.00 (shot 1)", "frame at 1:35.50 (shot 2)"]


def test_a_study_span_is_checked(tmp_path: Path) -> None:
    for start, end in (("1:50", "1:20"), ("0:00", "2:01"), ("", "1:00")):
        with pytest.raises(videos.VideoError):
            videos.study([tmp_path], tmp_path, VID, start, end)
    with pytest.raises(videos.VideoError):
        videos.study([tmp_path], tmp_path, "https://vimeo.com/123", "0:00", "0:10")   # the service is YouTube-only


def test_the_study_route_queues_with_the_propose_token(tmp_path: Path) -> None:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    media, queue = tmp_path / "media", tmp_path / "queue"
    queue.mkdir()
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve", media_dir=media, watch_queue=queue)
    client = TestClient(create_app(settings, core=FakeCore(), schedule=False))
    body = {"video": VID, "start": "1:20", "end": "1:50"}
    assert client.post("/videos/study", json=body).status_code == 401
    assert client.post("/videos/study", json=body, headers=bearer(PROPOSE)).json()["state"] == "waiting"
    assert (queue / f"{VID}@80-110.json").exists()
    studied(media)
    assert client.post("/videos/study", json=body, headers=bearer(PROPOSE)).json()["state"] == "studied"
    assert client.post("/videos/study", json={**body, "end": "9:00"}, headers=bearer(PROPOSE)).status_code == 422

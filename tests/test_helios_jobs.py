"""Clip renders on Helios's Jobs & Renders page (scripts/helios_jobs.py): the page's own job format, one record per
request from waiting to done, and nothing touched but its own records."""

import json
import os
import time
from pathlib import Path

import pytest

import helios_jobs


@pytest.fixture
def jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(helios_jobs, "JOBS", tmp_path)
    return tmp_path


def meta(jobs: Path, job_id: str) -> dict[str, object]:
    return json.loads((jobs / job_id / "meta.json").read_text(encoding="utf-8"))


def test_a_request_goes_waiting_running_done_with_its_stills(jobs: Path, tmp_path: Path) -> None:
    queued = time.time() - 90
    job_id = helios_jobs.record_id("four-subspaces-3d", queued)
    helios_jobs.waiting(job_id, "clip: The four subspaces", "mnemosyne clip ...", queued, "http://x/watch#four")
    m = meta(jobs, job_id)
    assert m["state"] == "waiting" and m["link"] == "http://x/watch#four" and m["started"] == helios_jobs.stamp(queued)

    helios_jobs.waiting(job_id, "again", "again", queued)            # a second pass doesn't overwrite it
    assert meta(jobs, job_id)["name"] == "clip: The four subspaces"

    helios_jobs.start(job_id, "clip: The four subspaces", "mnemosyne clip ...", "http://x/watch#four")
    helios_jobs.log(job_id, "Animation 0 : Partial movie file written")
    assert meta(jobs, job_id)["state"] == "running"
    assert "Animation 0" in (jobs / job_id / "output.log").read_text(encoding="utf-8")

    stills = tmp_path / "stills"
    stills.mkdir()
    (stills / "00-setup.jpg").write_bytes(b"jpg")
    (stills / "00-setup.png").write_bytes(b"png")                   # only the small JPEGs go to the page
    helios_jobs.finish(job_id, True, stills)
    m = meta(jobs, job_id)
    assert m["state"] == "done" and m["exit_code"] == 0 and m["ended"]
    assert sorted(p.name for p in (jobs / job_id / "out").iterdir()) == ["00-setup.jpg"]


def test_a_failure_is_recorded_as_failed(jobs: Path) -> None:
    helios_jobs.start("20261024-010000-clip-bad", "clip: bad", "cmd")
    helios_jobs.finish("20261024-010000-clip-bad", False)
    assert meta(jobs, "20261024-010000-clip-bad")["state"] == "failed"


def test_without_the_jobs_folder_nothing_happens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helios_jobs, "JOBS", tmp_path / "missing")
    helios_jobs.start("x-clip-y", "n", "c")
    helios_jobs.log("x-clip-y", "line")
    helios_jobs.finish("x-clip-y", True)
    assert not (tmp_path / "missing").exists()


def test_ids_cant_leave_the_jobs_folder(jobs: Path) -> None:
    helios_jobs.start("../escape", "n", "c")
    helios_jobs.start(".hidden", "n", "c")
    assert list(jobs.iterdir()) == []


def test_prune_removes_only_old_clip_records(jobs: Path) -> None:
    for job_id in ("20260101-000000-clip-old", "20260101-000000-demo-render", "20261024-000000-clip-new"):
        helios_jobs.start(job_id, "n", "c")
    old = time.time() - 40 * 86400
    for job_id in ("20260101-000000-clip-old", "20260101-000000-demo-render"):
        os.utime(jobs / job_id / "meta.json", (old, old))
    helios_jobs.prune(30)
    assert sorted(p.name for p in jobs.iterdir()) == ["20260101-000000-demo-render", "20261024-000000-clip-new"]


# ---------- the runner keeps the queue on the page while it renders ----------

def load_runner():  # the script module: its names are checked by running it, not by pyright
    import importlib.util
    spec = importlib.util.spec_from_file_location("render_runner",
                                                  Path(__file__).resolve().parent.parent / "scripts" / "render_runner.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def submitted(render: Path, queue: Path, slug: str) -> None:
    sub = render / ".submitted" / slug
    sub.mkdir(parents=True)
    (sub / "scene.py").write_text("x = 1\n", encoding="utf-8")
    (sub / "submission.json").write_text(json.dumps({"title": f"Clip {slug}", "scene": "S", "origin": "chat"}),
                                         encoding="utf-8")
    (queue / f"{slug}.json").write_text("{}", encoding="utf-8")


def test_a_clip_sent_mid_render_shows_as_waiting_before_the_render_ends(jobs: Path, tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    runner = load_runner()
    render, queue = tmp_path / "render", tmp_path / "queue"
    render.mkdir()
    queue.mkdir()
    monkeypatch.setattr(runner, "RENDER", render)
    monkeypatch.setattr(runner, "QUEUE", queue)
    monkeypatch.setattr(runner, "WAITING_EVERY", 0.2)
    monkeypatch.setattr(runner.helios_jobs, "JOBS", jobs)

    seen: dict[str, object] = {}

    def later() -> None:                        # a chat submits while the render below is running
        time.sleep(0.3)
        submitted(render, queue, "sent-mid-render")

    import threading
    threading.Thread(target=later).start()
    # A stand-in for the container: quiet for 1.5 s (as during a long LaTeX compile), then one line.
    code, tail = runner.stream([sys.executable, "-c", "import time; time.sleep(1.5); print('done')"], "none",
                               "20261024-000000-clip-current")
    records = [m for m in (json.loads(p.read_text(encoding="utf-8")) for p in jobs.glob("*/meta.json"))]
    seen["waiting"] = [m["name"] for m in records if m["state"] == "waiting"]
    assert code == 0 and "done" in tail
    assert seen["waiting"] == ["clip: Clip sent-mid-render"]   # on the page while the render was still going


def test_note_waiting_skips_what_it_cant_describe(jobs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = load_runner()
    render, queue = tmp_path / "render", tmp_path / "queue"
    render.mkdir()
    queue.mkdir()
    monkeypatch.setattr(runner, "RENDER", render)
    monkeypatch.setattr(runner, "QUEUE", queue)
    monkeypatch.setattr(runner.helios_jobs, "JOBS", jobs)
    (queue / "no-code-yet.json").write_text("{}", encoding="utf-8")      # its submission isn't written yet
    (queue / "Bad Name.json").write_text("{}", encoding="utf-8")
    submitted(render, queue, "fine")
    runner.note_waiting()
    names = [json.loads(p.read_text(encoding="utf-8"))["name"] for p in jobs.glob("*/meta.json")]
    assert names == ["clip: Clip fine"]

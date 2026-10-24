"""Render the clips waiting in media/render-queue, each in its own throwaway sandbox container. Runs on the Obelisk
every minute from Aman's crontab, under flock so only one runs at a time:

    * * * * * flock -n /tmp/mn-render.lock python3 ~/helios/mnemosyne/scripts/render_runner.py >> ... # mnemosyne-render-runner

Two kinds of request, both named only by a slug (clips.py writes them; the scene code is never in the request):
- a scene a chat submitted (mnemosyne_clip_submit): its code waits in media/render/.submitted/<slug>/;
- a published clip whose video was cleared, rendered again from its own folder's scene.py.

This script is trusted; the scene code is not (it may come from a model in a cloud chat). So the code runs only inside
the container scripts/render.py's docker_args() builds: no network, read-only root, no capabilities, capped CPU,
memory, processes, and time, and it sees only this job's in/ and out/. Nothing it writes is published until this
script has checked it: the video must be an MP4 of sane size, the stills images, and every name plain. The
metadata shown on /watch comes from the submission, not from the container. See docs/security/clip-submit.md.

Each request also shows on Helios's Jobs & Renders page (helios-preview, :8095) through scripts/helios_jobs.py: waiting
while it's in the queue, running with the sandbox's output as it comes, then done (stills and the /watch link) or
failed (the error). The page shows the log HTML-escaped; it's the container's own output, capped in size.
"""

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helios_jobs
from render import docker_args

ROOT = Path(__file__).resolve().parent.parent
RENDER = ROOT / "media" / "render"
QUEUE = ROOT / "media" / "render-queue"
JOBS = RENDER / ".runner"
STYLE = ROOT / "scenes" / "mn_style.py"
BRAND = ROOT / "scenes" / "brand.py"       # Aman's design tokens (gitignored), when present
AMANIM = ROOT.parent / "amanim"            # ~/helios/amanim: his technique library, pulled nightly
JOB = ROOT / "services" / "manim" / "job.py"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STILL = re.compile(r"^\d{2}-[a-z0-9-]{1,40}\.(png|jpg)$")
TIMEOUT = 900            # seconds for the whole container; a chat's walkthrough can run a few minutes
MAX_VIDEO = 300 << 20    # bytes
MAX_STILLS = 80
PER_RUN = 4              # renders per run, so one minute's run doesn't hold the lock for an hour
WATCH = "http://<obelisk>:8110/watch"   # where a published clip plays (tailnet only)
WAITING_EVERY = 15       # seconds between looks at the queue during a render, for the Jobs page


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", flush=True)


def source(slug: str) -> tuple[Path, dict[str, Any]]:
    """Where the code is, and the trusted metadata: the submission's (written by the api), or the published clip's."""
    sub = RENDER / ".submitted" / slug
    if (sub / "scene.py").is_file() and (sub / "submission.json").is_file():
        return sub, json.loads((sub / "submission.json").read_text(encoding="utf-8"))
    pub = RENDER / slug
    if (pub / "scene.py").is_file() and (pub / "render.json").is_file():
        return pub, json.loads((pub / "render.json").read_text(encoding="utf-8"))
    raise LookupError("no code to render for that clip")


def checked(out: Path) -> dict[str, Any]:
    """What the container wrote, checked before anything is published. Raises on anything unexpected."""
    for p in out.rglob("*"):
        if p.is_symlink():
            raise ValueError(f"the render left a link: {p.name}")
    video = out / "clip.mp4"
    if not video.is_file() or not 1000 < video.stat().st_size <= MAX_VIDEO:
        raise ValueError("no video, or one of an impossible size")
    with video.open("rb") as f:
        if f.read(12)[4:8] != b"ftyp":
            raise ValueError("the video isn't an MP4")
    stills = sorted((out / "stills").iterdir()) if (out / "stills").is_dir() else []
    if len(stills) > MAX_STILLS or any(not STILL.match(p.name) or not p.is_file() for p in stills):
        raise ValueError("unexpected files among the stills")
    meta: dict[str, Any] = json.loads((out / "render.json").read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise TypeError("render.json is not an object")
    return meta


def publish(slug: str, src: Path, trusted: dict[str, Any], out: Path, rendered: dict[str, Any]) -> None:
    """Swap the new clip in: video, stills, and the code it came from. Facts about the render (duration, sections,
    warnings) come from the container, checked; everything /watch shows as the clip's identity comes from `trusted`."""
    sections = [{"name": str(s.get("name", ""))[:80], "start": float(s.get("start", 0)),
                 "duration": float(s.get("duration", 0)), "still": str(s.get("still", ""))}
                for s in rendered.get("sections", []) if isinstance(s, dict)
                and STILL.match(str(s.get("still", "")))][:MAX_STILLS]
    meta = {k: trusted[k] for k in ("title", "courses", "topics", "watch_for", "cards", "origin", "by", "submitted_at")
            if k in trusted}
    meta.update({"scene": trusted["scene"], "file": trusted.get("file", "scene.py"), "quality": "final",
                 "resolution": "720p30", "duration": float(rendered.get("duration", 0)), "sections": sections,
                 "layout_warnings": [str(w)[:200] for w in rendered.get("layout_warnings", [])][:40],
                 "render_seconds": float(rendered.get("render_seconds", 0)),
                 "amanim": str(rendered.get("amanim") or "")[:40] or None,
                 "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    staging = RENDER / f".{slug}.publish"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    shutil.copyfile(out / "clip.mp4", staging / "clip.mp4")
    if (out / "stills").is_dir():
        shutil.copytree(out / "stills", staging / "stills")
    else:
        (staging / "stills").mkdir()
    shutil.copyfile(src / "scene.py", staging / "scene.py")
    shutil.copyfile(STYLE, staging / "mn_style.py")
    (staging / "render.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    dest = RENDER / slug
    shutil.rmtree(dest, ignore_errors=True)
    staging.rename(dest)


def describe(slug: str, trusted: dict[str, Any]) -> tuple[str, str]:
    """The page's name and command line for a request: what it is and where it came from."""
    origin = "a chat's scene" if trusted.get("origin") == "chat" else "rendered again"
    title = str(trusted.get("title") or slug)[:120]
    return f"clip: {title}", f"mnemosyne clip {slug} · {trusted.get('scene', '?')} · {origin} · 720p30 in the sandbox"


def note_waiting() -> None:
    """Every request in the queue shows on the Jobs page as waiting. Run at the start of each pass, and every
    WAITING_EVERY seconds during a render: cron's next pass can't run while this one holds the lock, so a clip sent
    mid-render would otherwise appear only when the render ends. A record that exists already is left alone."""
    for q in sorted(QUEUE.glob("*.json"), key=lambda p: p.stat().st_mtime):
        if not SLUG.match(q.stem):
            continue
        try:
            queued = q.stat().st_mtime
            helios_jobs.waiting(helios_jobs.record_id(q.stem, queued), *describe(q.stem, source(q.stem)[1]), queued,
                                f"{WATCH}#{q.stem}")
        except (LookupError, OSError, ValueError):   # gone already, or its submission is still being written
            pass


def stream(args: list[str], name: str, job_id: str) -> tuple[int, str]:
    """Run the container, copying its output to the Jobs page's log as it comes. Returns the exit code and the
    output's tail (the error, when it fails). Past TIMEOUT the container itself is removed, which ends the run.
    Meanwhile a second thread keeps the queue's waiting records current (note_waiting): on its own clock, since the
    container can go quiet for a while (LaTeX, a long camera move) and the output loop only wakes on a line."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)
    timer = threading.Timer(TIMEOUT, lambda: subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                                                            check=False))
    timer.start()
    stop = threading.Event()

    def watch_queue() -> None:
        while not stop.wait(WAITING_EVERY):
            note_waiting()

    watcher = threading.Thread(target=watch_queue, daemon=True)
    watcher.start()
    tail: list[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            helios_jobs.log(job_id, line)
            tail = (tail + [line])[-200:]
        proc.wait()
    finally:
        stop.set()
    timed_out = not timer.is_alive()
    timer.cancel()
    if timed_out:
        raise RuntimeError(f"the render took longer than {TIMEOUT} s: make the scene shorter or simpler")
    return proc.returncode, "".join(tail)[-12000:]


def run_one(slug: str, job_id: str) -> None:
    src, trusted = source(slug)
    job = JOBS / slug
    shutil.rmtree(job, ignore_errors=True)
    (job / "in").mkdir(parents=True)
    (job / "out").mkdir()
    shutil.copyfile(src / "scene.py", job / "in" / "scene.py")
    # always the current house style, so a re-rendered clip takes on Aman's look as it evolves
    shutil.copyfile(STYLE, job / "in" / "mn_style.py")
    shutil.copyfile(JOB, job / "in" / "job.py")
    if BRAND.is_file():
        shutil.copyfile(BRAND, job / "in" / "brand.py")
    if (AMANIM / "amanim" / "__init__.py").is_file():
        shutil.copytree(AMANIM / "amanim", job / "in" / "amanim", ignore=shutil.ignore_patterns("__pycache__"))
        version = subprocess.run(["git", "-C", str(AMANIM), "log", "-1", "--format=%h %cs"], capture_output=True,
                                 text=True, check=False).stdout.strip()
        (job / "in" / "amanim_version.txt").write_text(version or "unknown", encoding="utf-8")
    name = f"mn-runner-{slug}"[:60]
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    args = docker_args(name, str(job / "in"), str(job / "out"), "scene.py", str(trusted["scene"]), "final",
                       manim_timeout=TIMEOUT - 30)
    try:
        code, tail = stream(args, name, job_id)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    if code != 0:
        raise RuntimeError(tail or f"the render failed (exit {code})")
    publish(slug, src, trusted, job / "out", checked(job / "out"))
    if src.parent.name == ".submitted":
        shutil.rmtree(src, ignore_errors=True)
    shutil.rmtree(job, ignore_errors=True)


def main() -> int:
    QUEUE.mkdir(parents=True, exist_ok=True)
    for stale in QUEUE.glob("*.working"):        # a run that died mid-render: back in line
        stale.rename(stale.with_suffix(".json"))
    queued = sorted(QUEUE.glob("*.json"), key=lambda q: q.stat().st_mtime)
    ids = {q.stem: helios_jobs.record_id(q.stem, q.stat().st_mtime) for q in queued if SLUG.match(q.stem)}
    note_waiting()                               # everything in line shows on the Jobs page, not just this run's
    for request in queued[:PER_RUN]:
        slug = request.stem
        working = request.with_suffix(".working")
        request.rename(working)
        started = time.monotonic()
        job_id = ids.get(slug, "")
        try:
            if not SLUG.match(slug):
                raise ValueError("not a clip name")
            helios_jobs.start(job_id, *describe(slug, source(slug)[1]), f"{WATCH}#{slug}")
            run_one(slug, job_id)
            working.unlink(missing_ok=True)
            took = time.monotonic() - started
            helios_jobs.log(job_id, f"published in {took:.0f} s: {WATCH}#{slug}")
            helios_jobs.finish(job_id, True, RENDER / slug / "stills")
            log(f"rendered {slug} in {took:.0f} s")
        except Exception as e:  # noqa: BLE001 -- one bad scene never stops the rest
            working.with_suffix(".failed").write_text(str(e)[-12000:] or type(e).__name__, encoding="utf-8")
            working.unlink(missing_ok=True)
            shutil.rmtree(JOBS / slug, ignore_errors=True)
            helios_jobs.log(job_id, f"failed: {str(e)[-2000:]}")
            helios_jobs.finish(job_id, False)
            log(f"failed {slug}: {str(e)[-200:]!r}")
    helios_jobs.prune()
    return 0


if __name__ == "__main__":
    sys.exit(main())

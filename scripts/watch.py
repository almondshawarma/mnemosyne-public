"""Watch a lecture video: captions and the frames that matter, cut on the Obelisk; the video itself is never kept.

    uv run scripts/watch.py https://www.youtube.com/watch?v=6-wh6yvk6uc     # fetch (once) and copy back
    uv run scripts/watch.py 6-wh6yvk6uc --frames 12:30,25:10               # the kept frames nearest those times

Run it from a Claude Code session (the `obelisk` ssh alias). The work happens in the mnemosyne-watch container
(services/watch/): it reads captions with yt-dlp, streams the video into the container's memory to pick frames, and
writes meta.json, transcript.md, frames/, and contact sheets to media/watch/<id>/ on the Obelisk. The container has
network (it has to reach YouTube) but no capabilities, a read-only root, and capped memory and time.

Copied back to .watch/<id>/ here (gitignored): meta.json, transcript.md, frames.json, and sheets/. Read the transcript,
scan the sheets, then ask for full frames only where the board matters. That's how Claude "watches": the API takes
images, not video.

Study mode, for dissecting an animation Aman admires (the loop is in the mnemosyne-clips skill):

    uv run scripts/watch.py https://youtu.be/kYB8IZa5AuE --study 1:20-2:10 [--fps 6]   # here, on this machine
    uv run scripts/watch.py <link> --study 1:20-2:10 --on obelisk                      # in the Obelisk's sandbox

A study is small (a minute of CPU), so it runs here by default: the same watch_job.py, with the packages pinned in
services/watch/Dockerfile (uv fetches them once; with no ffmpeg installed, the imageio-ffmpeg wheel's). Only the span is
fetched, into a temporary folder outside OneDrive, and deleted before anything is written. --on obelisk runs it in the
watcher's container instead and copies it back. Either way it lands in .watch/<id>/study/<start>-<end>/: study.json,
motion.png, shots-NN.jpg, sheets/strip-NN.jpg, frames/. Any link yt-dlp reads works here (YouTube, Vimeo, a direct
.mp4); the MCP tool (mnemosyne_video_study) takes YouTube only.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOST = "obelisk"
IMAGE = "mnemosyne-watch:1"
REMOTE = "helios/mnemosyne/media/watch"
ROOT = Path(__file__).resolve().parent.parent
WATCHER = ROOT / "services" / "watch"
TIMEOUT = 1800
STUDY_TIMEOUT = 900
LOCAL_FFMPEG = "imageio-ffmpeg==0.6.0"   # used only where no ffmpeg is installed
SANDBOX = ("--read-only --tmpfs /tmp:rw,size=3g --cap-drop ALL --security-opt no-new-privileges "
           "--cpus 4 --memory 4g --pids-limit 256")


def video_id(s: str) -> str:
    """The 11-character id from a YouTube link (watch, youtu.be, embed, shorts) or the id itself."""
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})", s) or re.fullmatch(r"([A-Za-z0-9_-]{11})", s)
    if not m:
        raise SystemExit(f"not a YouTube link or id: {s}")
    return m[1]


def study_source(s: str) -> tuple[str, str]:
    """What the job fetches and the folder it's kept under: a YouTube id for both; vimeo-<n> for Vimeo; link-<hash>
    for any other http(s) link."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s) or re.match(r"^https?://([a-z0-9-]+\.)?(youtube\.com|youtu\.be)/", s):
        vid = video_id(s)
        return vid, vid
    if not re.match(r"^https?://", s) and Path(s).is_file():   # a video saved here (a Reddit post, a download)
        name = re.sub(r"[^a-z0-9]+", "-", Path(s).stem.lower()).strip("-")[:40]
        return str(Path(s).resolve()), f"file-{name}"
    if not re.match(r"^https?://", s):
        raise SystemExit(f"not a link or a video file: {s}")
    m = re.search(r"vimeo\.com/(?:video/)?(\d+)", s)
    return s, f"vimeo-{m[1]}" if m else f"link-{hashlib.sha1(s.encode()).hexdigest()[:10]}"


def ssh(command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, command], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check)


def seconds(s: str) -> float:
    parts = [float(p) for p in s.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def fetch(vid: str) -> int:
    out = f"{REMOTE}/{vid}"
    if ssh(f"test -f ~/{out}/sheets.json", check=False).returncode == 0:
        print(f"already watched: {HOST}:~/{out}")
        return 0
    name = f"mn-watch-{vid.lower().replace('_', '-')}"
    keep = f"if [ -d ~/{out}/study ]; then mv ~/{out}/study ~/{out}.tmp/study; fi; "   # its studies stay
    run = (f"mkdir -p ~/{out}.tmp && chmod 777 ~/{out}.tmp && docker rm -f {name} >/dev/null 2>&1; "
           f"timeout --signal=KILL {TIMEOUT} docker run --rm --name {name} {SANDBOX} "
           f"-v $HOME/{out}.tmp:/out {IMAGE} {vid}; rc=$?; "
           f"docker rm -f {name} >/dev/null 2>&1; "
           f"if [ $rc -eq 0 ]; then {keep}rm -rf ~/{out} && mv ~/{out}.tmp ~/{out}; else rm -rf ~/{out}.tmp; fi; "
           f"exit $rc")
    print(f"watching {vid} on {HOST} (captions, then frames; a lecture takes a few minutes) ...", flush=True)
    done = ssh(run, check=False)
    print(done.stdout.strip())
    if done.returncode != 0:
        print(done.stderr.strip()[-3000:], file=sys.stderr)
        return 1
    return 0


def copy_back(vid: str) -> Path:
    local = ROOT / ".watch" / vid
    local.mkdir(parents=True, exist_ok=True)
    src = f"{REMOTE}/{vid}"
    subprocess.run(["scp", "-q", "-r", f"{HOST}:{src}/meta.json", f"{HOST}:{src}/transcript.md",
                    f"{HOST}:{src}/frames.json", f"{HOST}:{src}/sheets.json", f"{HOST}:{src}/sheets", str(local)],
                   check=True)
    return local


def frames(vid: str, local: Path, at: list[float]) -> None:
    kept = json.loads((local / "frames.json").read_text(encoding="utf-8"))
    want = sorted({min(kept, key=lambda f: abs(f["t"] - t))["file"] for t in at})
    (local / "frames").mkdir(exist_ok=True)
    subprocess.run(["scp", "-q", *[f"{HOST}:{REMOTE}/{vid}/frames/{f}" for f in want], str(local / "frames")],
                   check=True)
    for f in want:
        print(local / "frames" / f)


# ---------- study mode ----------

def pins() -> list[str]:
    """The watcher image's pinned packages (its Dockerfile's pip line), so a study here runs what the Obelisk runs,
    plus a static ffmpeg for machines without one (the image has Debian's)."""
    m = re.search(r"pip install --no-cache-dir ([^\n]+)", (WATCHER / "Dockerfile").read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("can't find the pinned packages in services/watch/Dockerfile")
    return [*m[1].split(), LOCAL_FFMPEG]


def study_here(source: str, start: float, end: float, fps: float, dest: Path, credit: list[str]) -> int:
    partial = dest.with_name(dest.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp(prefix="mn-study-"))   # the span lives here (outside OneDrive), then it's deleted
    try:
        withs = [x for p in pins() for x in ("--with", p)]
        r = subprocess.run(["uv", "run", "--no-project", "--quiet", *withs, "python", str(WATCHER / "watch_job.py"),
                            "--study", source, f"{start:g}", f"{end:g}", f"{fps:g}", "--out", str(partial),
                            "--tmp", str(tmp), *credit], check=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        return 1
    shutil.rmtree(dest, ignore_errors=True)
    partial.rename(dest)
    return 0


def study_obelisk(source: str, key: str, span: str, start: float, end: float, fps: float, dest: Path) -> int:
    out = f"{REMOTE}/{key}/study/{span}"
    if ssh(f"test -f ~/{out}/study.json", check=False).returncode != 0:
        name = f"mn-study-{hashlib.sha1(out.encode()).hexdigest()[:8]}"
        run = (f"mkdir -p ~/{out}.tmp && chmod 777 ~/{out}.tmp && docker rm -f {name} >/dev/null 2>&1; "
               f"timeout --signal=KILL {STUDY_TIMEOUT} docker run --rm --name {name} {SANDBOX} "
               f"-v $HOME/{out}.tmp:/out {IMAGE} --study '{source}' {start:g} {end:g} {fps:g}; rc=$?; "
               f"docker rm -f {name} >/dev/null 2>&1; "
               f"if [ $rc -eq 0 ]; then rm -rf ~/{out} && mv ~/{out}.tmp ~/{out}; else rm -rf ~/{out}.tmp; fi; "
               f"exit $rc")
        print(f"studying on {HOST} ...", flush=True)
        done = ssh(run, check=False)
        print(done.stdout.strip())
        if done.returncode != 0:
            print(done.stderr.strip()[-3000:], file=sys.stderr)
            return 1
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["scp", "-q", "-r", f"{HOST}:{out}", str(dest.parent)], check=True)
    return 0


def study(raw: str, span: str, fps: float, on: str, credit: dict[str, str]) -> int:
    source, key = study_source(raw)
    if "'" in source:
        raise SystemExit("a link with a quote in it isn't accepted")
    if key.startswith("file-") and on == "obelisk":
        raise SystemExit("a video file is studied here, not on the Obelisk")
    flags = [x for k, v in credit.items() if v for x in (f"--{k}", v)]
    a, _, b = span.partition("-")
    start, end = seconds(a), seconds(b)
    if not 0 <= start < end or end - start > 120:
        raise SystemExit("a study spans up to 120 seconds: --study 1:20-1:50")
    name = f"{start:g}-{end:g}"
    dest = ROOT / ".watch" / key / "study" / name
    if not (dest / "study.json").is_file():
        rc = (study_obelisk(source, key, name, start, end, fps, dest) if on == "obelisk"
              else study_here(source, start, end, fps, dest, flags))
        if rc:
            return rc
    s = json.loads((dest / "study.json").read_text(encoding="utf-8"))
    print(f"{s['attribution']['credit']}")
    print(f"  {s['summary']['shots']} shots, {s['summary']['holds']['count']} holds, {s['summary']['moves']['count']} "
          f"moves; holding {s['summary']['holds']['share_of_time']:.0%} of the time")
    for f in ("study.json", "motion.png", *s["files"]["shot_sheets"]):
        print(f"  {dest / f}")
    print(f"  {dest / 'sheets'}  ({len(s['files']['strips'])} filmstrips)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", help="YouTube link or id (study mode: any link yt-dlp reads)")
    ap.add_argument("--frames", help="comma-separated times (mm:ss or h:mm:ss): copy the nearest kept frames")
    ap.add_argument("--study", metavar="START-END", help="study one span densely, e.g. 1:20-1:50 (120 s at most)")
    ap.add_argument("--fps", type=float, default=6.0, help="study frames a second (default 6, at most 12)")
    ap.add_argument("--on", choices=["here", "obelisk"], default="here", help="where a study runs (default here)")
    for k in ("creator", "title", "link"):
        ap.add_argument(f"--{k}", default="", help=f"study: the {k} to credit (a file can't say; overrides the site)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]  -- Windows consoles
    if args.study:
        return study(args.video, args.study, args.fps, args.on,
                     {"creator": args.creator, "title": args.title, "link": args.link})
    vid = video_id(args.video)
    if fetch(vid):
        return 1
    local = copy_back(vid)
    if args.frames:
        frames(vid, local, [seconds(t) for t in args.frames.split(",")])
        return 0
    meta = json.loads((local / "meta.json").read_text(encoding="utf-8"))
    print(f"{meta['title']} ({meta['channel']}), captions: {meta['captions']}")
    print(f"  {local / 'transcript.md'}")
    for s in json.loads((local / "sheets.json").read_text(encoding="utf-8")):
        print(f"  {local / 'sheets' / s['file']}  ({len(s['frames'])} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

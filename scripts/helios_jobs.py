"""Mnemosyne's renders on Helios's Jobs & Renders page (helios-preview, :8095), in that page's own job format.

helios-preview lists /mnt/data/helios/jobs/<id>/: meta.json (name, cmd, state, started, ended, exit_code), output.log
(whose tail it shows live), and out/ (artifacts it shows as a gallery). tools/helios_job.py writes those for Helios's
own jobs; this writes them for a clip render, so a scene a chat submitted shows up there while it waits, while it
renders (the sandbox's output, line by line), and after (the stills, a /watch link, or the error).

Used by scripts/render_runner.py (chats' clips, re-renders) on the Obelisk, and by scripts/render.py (a Claude Code
session's render) over ssh, through the command line below. Standard library only: the runner runs on the host's
python3. Records are only written where the jobs folder exists; anywhere else every call does nothing.

    python3 scripts/helios_jobs.py start ID NAME CMD [LINK]
    python3 scripts/helios_jobs.py finish ID ok|failed [STILLS_DIR]
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

JOBS = Path(os.environ.get("HELIOS_JOBS_DIR", "/mnt/data/helios/jobs"))
PREFIX = "clip"            # every record here is <stamp>-clip-<slug>: prune touches nothing else
MAX_LOG = 2 << 20          # bytes: a runaway scene can't fill the disk through its log
KEEP_DAYS = 30


def stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def record_id(slug: str, when: float) -> str:
    """One record per request: named from when it was queued, so waiting and running are the same record."""
    return f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime(when))}-{PREFIX}-{slug}"[:80]


def _dir(job_id: str) -> Path | None:
    if not JOBS.is_dir() or "/" in job_id or job_id.startswith("."):
        return None
    return JOBS / job_id


def _write_meta(d: Path, meta: dict[str, object]) -> None:
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp, d / "meta.json")      # the page may read it mid-write: swap it in whole


def _meta(d: Path) -> dict[str, object]:
    try:
        return json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def waiting(job_id: str, name: str, cmd: str, queued: float, link: str | None = None) -> None:
    """A request in the queue, not started: shown as waiting, with its time in line counting from when it was sent."""
    d = _dir(job_id)
    if d is None or (d / "meta.json").is_file():
        return
    (d / "out").mkdir(parents=True, exist_ok=True)
    _write_meta(d, {"id": job_id, "name": name, "cmd": cmd, "link": link, "state": "waiting",
                    "started": stamp(queued), "ended": None, "exit_code": None})


def start(job_id: str, name: str, cmd: str, link: str | None = None) -> None:
    """The render begins: the clock restarts at now, and the log starts empty."""
    d = _dir(job_id)
    if d is None:
        return
    (d / "out").mkdir(parents=True, exist_ok=True)
    (d / "output.log").write_text("", encoding="utf-8")
    _write_meta(d, {"id": job_id, "name": name, "cmd": cmd, "link": link, "state": "running",
                    "started": stamp(time.time()), "ended": None, "exit_code": None})


def log(job_id: str, text: str) -> None:
    d = _dir(job_id)
    if d is None or not (d / "meta.json").is_file():
        return
    path = d / "output.log"
    if path.is_file() and path.stat().st_size > MAX_LOG:
        return
    with path.open("a", encoding="utf-8", errors="replace") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def finish(job_id: str, ok: bool, stills: Path | None = None) -> None:
    """Done or failed. A done render's stills (the small JPEGs) go into out/, so the page shows them."""
    d = _dir(job_id)
    if d is None or not (d / "meta.json").is_file():
        return
    if ok and stills is not None and stills.is_dir():
        for jpg in sorted(stills.glob("*.jpg")):
            shutil.copyfile(jpg, d / "out" / jpg.name)
    meta = _meta(d)
    meta.update({"state": "done" if ok else "failed", "ended": stamp(time.time()), "exit_code": 0 if ok else 1})
    _write_meta(d, meta)


def prune(days: int = KEEP_DAYS) -> None:
    """Clip records older than `days`. Only this module's own records (…-clip-…), never Helios's other jobs."""
    if not JOBS.is_dir():
        return
    cutoff = time.time() - days * 86400
    for d in JOBS.iterdir():
        if d.is_dir() and f"-{PREFIX}-" in d.name and (d / "meta.json").is_file() \
                and (d / "meta.json").stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "start":
        start(argv[1], argv[2], argv[3], argv[4] if len(argv) > 4 else None)
        return 0
    if len(argv) >= 3 and argv[0] == "finish":
        finish(argv[1], argv[2] == "ok", Path(argv[3]) if len(argv) > 3 else None)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Render a Manim scene in the sandbox on the Obelisk, and bring its stills back to look at.

    uv run scripts/render.py scenes/phys207/gauss_flux.py            # draft: 480p15, fast, for checking
    uv run scripts/render.py scenes/phys207/gauss_flux.py --final    # 720p30, published on /watch
    uv run scripts/render.py scenes/phys207/gauss_flux.py --hq       # 720p30 as a draft: what the runner would take

Run it from a Claude Code session (it uses the `obelisk` ssh alias). Scene code is model-written Python, so it runs
only inside a throwaway container: no network, read-only root, no capabilities, capped CPU, memory, processes, and
time. The scene and scenes/mn_style.py go in read-only; /out is the only thing it can write.

- Drafts land in media/render/.drafts/<slug>/ on the Obelisk, which /watch doesn't list.
- Finals land in media/render/<slug>/ (clip.mp4, stills/, render.json, scene.py), which /watch serves.
- Either way, render.json and the stills are copied to .render/<slug>/ here (gitignored), so Claude can look at each
  section's last frame before anyone else sees the clip.
- It also shows on Helios's Jobs & Renders page (:8095) while it renders, with the sandbox's output as it comes, then
  the stills (scripts/helios_jobs.py, run on the Obelisk).

Every render also gets Aman's own style layers, read-only beside the scene:
- amanim, his technique library (fields, Lorentz paths, Feynman lines, waves), from the Obelisk's ~/helios/amanim,
  which the nightly pull keeps current. Its commit lands in render.json. `--amanim PATH` sends a local working copy
  instead, for developing amanim and a scene together.
- scenes/brand.py, his design tokens (generated from helios_tokens.yaml, gitignored), when it exists: mn_style.py
  takes its palette from it.

The engine is ManimCE (mnemosyne-manim:1: the official image pinned by digest, plus matplotlib; services/manim/).
ManimGX is the candidate to replace it later (docs/video-plan.md).
"""

import argparse
import ast
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

IMAGE = "mnemosyne-manim:1"                # services/manim/Dockerfile, built on the Obelisk
HOST = "obelisk"
REMOTE = "helios/mnemosyne/media/render"   # relative to the home directory on the Obelisk
ROOT = Path(__file__).resolve().parent.parent
STYLE = ROOT / "scenes" / "mn_style.py"
BRAND = ROOT / "scenes" / "brand.py"       # Aman's design tokens; gitignored, generated from helios_tokens.yaml
AMANIM = "helios/amanim"                   # on the Obelisk, under home; the nightly pull keeps it current
JOB = ROOT / "services" / "manim" / "job.py"
RECORD = "python3 ~/helios/mnemosyne/scripts/helios_jobs.py"   # the Jobs page's records, on the Obelisk
HELIOS_JOBS = "/mnt/data/helios/jobs"
TIMEOUT = 300                              # seconds for a draft; a scene that needs longer is too long for a clip
TIMEOUT_720 = 900                          # at 720p30, the same cap scripts/render_runner.py gives a chat's clip


def scene_class(path: Path) -> str:
    """The scene's class: CLIP['scene'] if set, else the first class whose base name ends in 'Scene'."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CLIP" for t in node.targets):
            clip = ast.literal_eval(node.value)
            if isinstance(clip, dict) and clip.get("scene"):
                return str(clip["scene"])
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(isinstance(b, ast.Name) and re.search(r"Scene\w*$", b.id)
                                                  for b in node.bases):
            return node.name
    raise SystemExit(f"no Scene class in {path}")


def ssh(command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, command], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check)


def docker_args(name: str, in_dir: str, out_dir: str, scene: str, cls: str, quality: str,
                manim_timeout: int = TIMEOUT - 20) -> list[str]:
    """The sandbox: one throwaway container per render, seeing only its own job's in/ (read-only) and out/. Shared
    with scripts/render_runner.py, which runs chats' scenes the same way."""
    return ["docker", "run", "--rm", "--name", name,
            "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=1g",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "1000:1000", "--cpus", "4", "--memory", "2g", "--pids-limit", "256",
            "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", f"MN_TIMEOUT={manim_timeout}",
            "-v", f"{in_dir}:/in:ro", "-v", f"{out_dir}:/out",
            "--entrypoint", "python", IMAGE, "/in/job.py", f"/in/{scene}", cls, quality]


def sandbox(job: str, scene: str, cls: str, quality: str) -> str:
    """The docker command, as one shell line for the Obelisk. The name lets a timeout kill the container itself:
    killing the docker client alone would leave it running."""
    name = f"mn-render-{Path(scene).stem.replace('_', '-')}"
    limit = TIMEOUT_720 if quality == "final" else TIMEOUT
    run = docker_args(name, f"$HOME/{job}/in", f"$HOME/{job}/out", scene, cls, quality, manim_timeout=limit - 30)
    line = " ".join(a if a.startswith("$HOME") else shlex.quote(a) for a in run)
    return (f"docker rm -f {name} >/dev/null 2>&1; timeout --signal=KILL {limit} {line}; rc=$?; "
            f"docker rm -f {name} >/dev/null 2>&1; exit $rc")


def send_amanim(job: str, local: Path | None) -> None:
    """Put amanim beside the scene, with the commit it came from (render.json records it)."""
    if local is None:
        ssh(f"cp -r ~/{AMANIM}/amanim ~/{job}/in/ && "
            f"git -C ~/{AMANIM} log -1 --format='%h %cs' > ~/{job}/in/amanim_version.txt")
        return
    package = local.resolve() / "amanim"
    if not (package / "__init__.py").is_file():
        raise SystemExit(f"no amanim package in {local}")
    described = subprocess.run(["git", "-C", str(local), "describe", "--always", "--dirty"], capture_output=True,
                               text=True, check=False).stdout.strip() or "unknown"
    subprocess.run(["scp", "-q", "-r", str(package), f"{HOST}:{job}/in/"], check=True)
    ssh(f"echo {shlex.quote('local ' + described)} > ~/{job}/in/amanim_version.txt && "
        f"rm -rf ~/{job}/in/amanim/__pycache__")


def helios_record(slug: str, cls: str, kind: str) -> str:
    """Start this render's record on the Jobs & Renders page; returns its id. A missing page never stops a render."""
    record = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + f"-clip-{slug}"
    link = f"http://<obelisk>:8110/watch#{slug}" if kind == "published" else ""
    args = [record, f"clip: {slug} ({kind}, from Claude Code)", f"scripts/render.py {slug} · {cls} · {kind}", link]
    ssh(f"{RECORD} start " + " ".join(shlex.quote(a) for a in args if a), check=False)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene", type=Path)
    ap.add_argument("--final", action="store_true", help="720p30, published on /watch")
    ap.add_argument("--hq", action="store_true",
                    help="720p30 like --final, but kept as a draft: to time a clip against the runner's cap")
    ap.add_argument("--amanim", type=Path, help="a local amanim checkout to use instead of the Obelisk's")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]  -- Windows consoles
    scene: Path = args.scene.resolve()
    cls = scene_class(scene)
    slug = scene.stem.replace("_", "-")
    quality = "final" if args.final or args.hq else "draft"
    job = f"{REMOTE}/.jobs/{slug}"
    dest = f"{REMOTE}/{slug}" if args.final else f"{REMOTE}/.drafts/{slug}"

    ssh(f"rm -rf ~/{job} && mkdir -p ~/{job}/in ~/{job}/out && chmod 777 ~/{job}/out")
    subprocess.run(["scp", "-q", str(scene), str(STYLE), str(JOB), *([str(BRAND)] if BRAND.exists() else []),
                    f"{HOST}:{job}/in/"], check=True)
    send_amanim(job, args.amanim)
    kind = "published" if args.final else "720p draft" if args.hq else "draft"
    record = helios_record(slug, cls, kind)
    print(f"rendering {cls} ({quality}) on {HOST} ...", flush=True)
    # The sandbox's output goes to the Jobs page's log as it comes (tee), and comes back here too.
    log = f"{HELIOS_JOBS}/{record}/output.log"
    done = ssh(f"set -o pipefail; L={log}; [ -f $L ] || L=/dev/null; ( {sandbox(job, scene.name, cls, quality)} ) "
               f"2>&1 | tee -a $L", check=False)
    if done.returncode != 0:
        print(done.stdout.strip()[-30000:], file=sys.stderr)
        print(f"render failed (exit {done.returncode}{', timed out' if done.returncode == 137 else ''})",
              file=sys.stderr)
        ssh(f"{RECORD} finish {record} failed", check=False)
        return 1
    print(done.stdout.strip().splitlines()[-1])    # job.py's summary; the full log is on the Jobs page
    ssh(f"cp ~/{job}/in/{shlex.quote(scene.name)} ~/{job}/out/scene.py && cp ~/{job}/in/mn_style.py ~/{job}/out/ && "
        f"rm -rf ~/{dest} && mkdir -p $(dirname ~/{dest}) && mv ~/{job}/out ~/{dest} && rm -rf ~/{job}")
    ssh(f"{RECORD} finish {record} ok ~/{dest}/stills", check=False)

    local = ROOT / ".render" / slug
    local.mkdir(parents=True, exist_ok=True)
    for old in local.glob("stills/*.png"):
        old.unlink()
    subprocess.run(["scp", "-q", "-r", f"{HOST}:{dest}/stills", f"{HOST}:{dest}/render.json", str(local)],
                   check=True)
    meta = json.loads((local / "render.json").read_text(encoding="utf-8"))
    print(f"{meta['duration']} s, rendered in {meta['render_seconds']} s -> {HOST}:~/{dest}")
    for w in meta["layout_warnings"]:
        print(f"  {w}")
    for s in meta["sections"]:
        print(f"  {s['start']:6.1f}s  {s['name']:<24} {local / 'stills' / s['still']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

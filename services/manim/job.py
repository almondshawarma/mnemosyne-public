"""One render, inside a throwaway sandbox container on the Obelisk.

    python /in/job.py SCENE_FILE SCENE_CLASS draft|final|publish

`publish` is a 1080p60 export with title and end cards, for posting elsewhere (scripts/render.py --publish); it
never lands on /watch.

Started by scripts/render.py (a session's render) or scripts/render_runner.py (clips a chat submitted, and clips
rendered again after their video was cleared). Each job gets its own container: no network, a read-only root, no
capabilities, capped CPU, memory, processes, and time. /in (the scene, mn_style.py, this file) is read-only, and /out
is the only place it writes besides /tmp. Scene code may be written by a model in a cloud chat, so nothing here is
trusted to the host: the runner checks what lands in /out before it publishes anything
(docs/security/clip-submit.md).

It renders the scene with sections, then writes to /out:
- clip.mp4: the whole scene;
- stills/NN-<section>.png and .jpg: the last frame of each section. The PNG is what Claude reads in a session; the
  smaller JPEG goes back to a chat through mnemosyne_clip_status;
- render.json: the scene's CLIP metadata, the sections with their start times, any MN-LAYOUT and MN-MOTION warnings,
  and the sound cues and tone tracks (MN-CUE / MN-TONE lines) that render.py --publish turns into a marker EDL and WAVs.

A CLIP block with "look": "ethereal" (or "soft") puts the clip and its stills through amanim.post afterwards: bloom,
chromatic aberration, vignette, grain. It costs about 0.1 s a frame at 720p, inside the same container time limit.

Standard library plus what the manimcommunity/manim image already has (PyAV, Pillow); nothing is installed.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

QUALITY = {"draft": ("-ql", "480p15"), "final": ("-qm", "720p30"),
           "publish": ("-qh", "1080p60")}   # an export to post elsewhere, with title and end cards (MN_CARDS)
OUT = Path("/out")
MEDIA = Path("/tmp/media")
TIMEOUT = int(os.environ.get("MN_TIMEOUT", "280"))   # seconds for manim itself; the container's own limit is longer
PREVIEW = 960                                        # long edge of the JPEG stills


def clip_meta(scene: Path) -> dict[str, Any]:
    """The scene file's module-level CLIP = {...} literal (title, courses, topics, watch_for, cards)."""
    for node in ast.parse(scene.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CLIP" for t in node.targets):
            try:
                value: dict[str, Any] = ast.literal_eval(node.value)
            except ValueError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def the_look(name: str) -> Any:
    """amanim.post's look by name ("ethereal", "soft"), from the amanim beside the scene; None for no look. The name
    comes from the scene's CLIP literal, so only known names count."""
    if name not in ("ethereal", "soft"):
        return None
    from amanim import post  # /in is on the path: render.py copies amanim beside the scene

    return post.LOOKS[name]


def last_frame(video: Path, png: Path, look: Any = None) -> None:
    import av  # in the image: manim renders through PyAV

    frame = None
    with av.open(str(video)) as container:
        for frame in container.decode(video=0):
            pass
    if frame is not None:
        img = frame.to_image()
        if look is not None:
            from amanim import post
            from PIL import Image

            img = Image.fromarray(post.finish(frame.to_ndarray(format="rgb24"), look))
        img.save(png)
        img.thumbnail((PREVIEW, PREVIEW))
        img.convert("RGB").save(png.with_suffix(".jpg"), quality=85)


MAX_CUES, MAX_TRACKS, MAX_SAMPLES = 500, 8, 40000   # a 10-minute export at 60 fps is 36000 samples a track


def _num(v: Any, lo: float, hi: float) -> float | None:
    """A finite number within [lo, hi], else None: these lines come from scene code, which may be a chat's."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
        return None
    return float(v)


def sound(log: str) -> dict[str, Any]:
    """The scene's sound cues and tone tracks (mn_style's MN-CUE, MN-TONE-TRACK and MN-TONE lines), validated and
    capped: cues are {t, kind, label, strength}, and each tone is {base_hz, samples: [[t, ratio or None, amp]]}.
    Only numbers and short strings survive; render.py --publish writes them out as a marker EDL and WAV stems, and
    the runner never publishes them."""
    cues: list[dict[str, Any]] = []
    tones: dict[str, dict[str, Any]] = {}
    for line in log.splitlines():
        head, _, body = line.partition(" ")
        if head not in ("MN-CUE", "MN-TONE-TRACK", "MN-TONE"):
            continue
        try:
            v = json.loads(body)
        except ValueError:
            continue
        if head == "MN-CUE" and isinstance(v, dict) and len(cues) < MAX_CUES:
            t, strength = _num(v.get("t"), 0, 36000), _num(v.get("strength", 1.0), 0, 100)
            if t is not None and strength is not None:
                cues.append({"t": t, "kind": str(v.get("kind", "hit"))[:20], "label": str(v.get("label", ""))[:160],
                             "strength": strength})
        elif head == "MN-TONE-TRACK" and isinstance(v, dict) and len(tones) < MAX_TRACKS:
            base = _num(v.get("base_hz"), 20, 20000)
            name = str(v.get("name", ""))[:40]
            voice = str(v.get("voice", "tone"))
            if base is not None and re.fullmatch(r"[A-Za-z0-9_-]+", name) and voice in ("tone", "buzz"):
                tones.setdefault(name, {"base_hz": base, "voice": voice, "samples": []})
        elif head == "MN-TONE" and isinstance(v, list) and len(v) == 4 and str(v[0]) in tones:
            samples = tones[str(v[0])]["samples"]
            t, amp = _num(v[1], 0, 36000), _num(v[3], 0, 1)
            ratio = None if v[2] is None else _num(v[2], 1e-3, 1e3)
            if t is not None and amp is not None and (ratio is not None or v[2] is None) and \
                    len(samples) < MAX_SAMPLES:
                samples.append([t, ratio, amp])
    return {"cues": sorted(cues, key=lambda c: c["t"]), "tones": tones}


def main(scene_file: str, cls: str, quality: str) -> int:
    scene = Path(scene_file)
    flag, folder = QUALITY[quality]
    started = time.monotonic()
    # Manim's output is passed through line by line (no progress bars), so whoever runs this container can show the
    # render as it goes: the runner writes it to the Helios Jobs & Renders page. It's also kept for MN-LAYOUT lines.
    # An export gets mn_style's title and end cards; /watch's clips don't (the page already says what they are).
    env = {**os.environ, "MN_CARDS": "1"} if quality == "publish" else None
    proc = subprocess.Popen([sys.executable, "-m", "manim", "render", flag, "--save_sections", "--disable_caching",
                             "--progress_bar", "none", "--media_dir", str(MEDIA), str(scene), cls],
                            cwd="/tmp", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env=env)
    timer = threading.Timer(TIMEOUT, proc.kill)
    timer.start()
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    proc.wait()
    timed_out = not timer.is_alive()
    timer.cancel()
    if timed_out:
        print(f"the render took longer than {TIMEOUT} s: make the scene shorter or simpler", flush=True)
        return 1
    log = "".join(lines)
    if proc.returncode != 0:
        print(f"manim failed (exit {proc.returncode}); its output is above", flush=True)
        return 1
    warnings = sorted(set(re.findall(r"MN-(?:LAYOUT|MOTION) .*", log)))   # layout and frozen motion: both fixable
    videos = MEDIA / "videos" / scene.stem / folder
    look_name = str(clip_meta(scene).get("look") or "")
    look = the_look(look_name)
    if look is not None:
        from amanim import post

        print(f"applying the {look_name} look (amanim.post: bloom, aberration, vignette, grain) ...", flush=True)
        post.finish_video(videos / f"{cls}.mp4", OUT / "clip.mp4", look)
    else:
        shutil.copyfile(videos / f"{cls}.mp4", OUT / "clip.mp4")
    (OUT / "stills").mkdir(exist_ok=True)
    index = videos / "sections" / f"{cls}.json"
    sections: list[dict[str, Any]] = []
    at = 0.0
    for i, s in enumerate(json.loads(index.read_text(encoding="utf-8")) if index.exists() else []):
        duration = float(s.get("duration") or 0)
        if duration <= 0:
            continue
        still = f"{i:02d}-{re.sub(r'[^a-z0-9]+', '-', str(s['name']).lower()).strip('-')[:40] or 'section'}.png"
        last_frame(videos / "sections" / s["video"], OUT / "stills" / still, look)
        sections.append({"name": str(s["name"])[:80], "start": round(at, 2), "duration": round(duration, 2),
                         "still": still})
        at += duration
    version = scene.parent / "amanim_version.txt"
    meta = {**clip_meta(scene), "scene": cls, "file": scene.name, "quality": quality, "resolution": folder,
            "amanim": version.read_text(encoding="utf-8").strip() if version.is_file() else None,
            "duration": round(at, 2), "sections": sections, "layout_warnings": warnings, **sound(log),
            "look": look_name if look is not None else None,
            "render_seconds": round(time.monotonic() - started, 1),
            "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (OUT / "render.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("scene", "duration", "render_seconds", "layout_warnings")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))

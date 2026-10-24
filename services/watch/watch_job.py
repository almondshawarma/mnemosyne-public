"""Watch one video, inside the container scripts/watch.py starts on the Obelisk: keep what a reader needs, not the video.

    python watch_job.py VIDEO_ID     # one video into /out (scripts/watch.py, from a session)
    python watch_job.py --serve      # the watcher service: requests from /queue (videos.py) into /watch/<id>
    python watch_job.py --study SOURCE START END [FPS] [--out DIR --tmp DIR]   # study mode: one span, densely

Writes (to /out, or /watch/<id> in the service; the Obelisk's media/watch/<id>/):
- meta.json: title, channel, duration, license, chapters, link;
- transcript.json: the caption segments (human captions first, YouTube's automatic ones if there are none);
- transcript.md: the same as timestamped paragraphs, for reading;
- frames/f-<seconds>.jpg and frames.json: the moments worth seeing, each with the words spoken around it;
- sheets/sheet-NN.jpg and sheets.json: contact sheets, a labeled grid of frames per image, so Claude can scan a whole
  lecture in a few images and then open full frames only where the board matters.

The video itself goes to /tmp (memory, in this container) and is gone when the container exits. Aman's call
(2026-10-13): videos are understood, never saved.

Picking frames: sample once a second; from every WINDOW seconds keep the frame that shows the most detail (chalk,
symbols) while moving the least. Near-duplicates are dropped by a difference hash.

Study mode is the opposite, for animations Aman wants to learn from: one span (10-90 s), densely. ffmpeg fetches only
the span (yt-dlp --download-sections; the whole video only if that fails) into TMP, and it's deleted before anything
is written. Into /out (scripts/watch.py moves it to media/watch/<id>/study/<start>-<end>/):
- frames/s-<seconds>.jpg and frames.json: FPS frames a second (default 6, at most 12);
- sheets/strip-NN.jpg: filmstrips, 6 frames a row, each with its time and a bar for the motion into it;
- motion.csv and motion.png: the motion energy at the video's own rate (up to 30 a second), holds shaded, cuts
  marked, caption starts ticked: that's where timing, easing, and sync live;
- shots-NN.jpg: each shot's key frame with its length, palette, and composition;
- study.json: the shots (palette, tone, composition, what's said), holds and moves (easing), the span's transcript,
  and the attribution to cite. The measurements are study.py's.
Any link yt-dlp reads works from a session (YouTube, Vimeo, a direct .mp4); the service takes YouTube ids only.
"""

import argparse
import itertools
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import av
import numpy as np
import study
from PIL import Image, ImageDraw, ImageFont

OUT = Path("/out")
TMP = Path("/tmp")
WATCHED = Path("/watch")   # the service writes each video here
QUEUE = Path("/queue")     # and takes requests from here: <id>.json, then <id>.working, then done or <id>.failed
POLL = 10                  # seconds between looks at an empty queue
STEP = 1.0          # seconds between samples
WINDOW = 30.0       # one frame per this many seconds: the clearest view of what's on the board or screen
SAME = 6            # difference-hash bits: closer than this to a kept frame is the same frame
PARAGRAPH = 45.0    # seconds of speech per transcript paragraph
GRID = (3, 4)       # contact sheet columns, rows
CELL_H = 234        # contact sheet cell height; its width follows the video (4:3 lectures, 16:9 animations)
VIDEO_FORMAT = "bv*[height<=720][vcodec^=avc1]/bv*[height<=720]/b[height<=720]"   # the picture only: no sound used
# a study's: the short side at most 720 whichever way up (Reels are 720 x 1280), else whatever there is
STUDY_FORMAT = ("bv*[height<=1280][width<=1280][vcodec^=avc1]/bv*[height<=1280][width<=1280]/"
                "b[height<=1280][width<=1280]/bv*/b")
STUDY_FPS, STUDY_MAX_FPS = 6.0, 12.0   # frames a second kept in study mode
CURVE_FPS = 30.0    # the motion curve's rate at most (the video's own rate, if lower)
STUDY_MAX = 120.0   # seconds a study may span
THUMB_W = 320       # width the motion curve compares frames at (thin lines must still register)
COLOR_W = 192       # width palettes and composition are measured at
STRIP = (6, 5)      # filmstrip columns (a second a row at 6 fps), rows
STRIP_H = 162       # filmstrip cell height
SHOT_GRID = (3, 3)  # shot sheet columns, rows
SHOT_H = 216        # shot sheet cell height
INK, AMBER, RED, MUTED, PANEL = "#0f141b", "#e2a743", "#e05d5d", "#7f8b99", "#1c2632"   # the sheets' colors


def ffmpeg() -> list[str]:
    """The ffmpeg yt-dlp cuts sections with: the system's (the image's, from Debian), else the one the imageio-ffmpeg
    wheel ships (scripts/watch.py adds it for local runs)."""
    if shutil.which("ffmpeg"):
        return []
    try:
        import imageio_ffmpeg
    except ImportError:
        return []
    return ["--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe()]


def ytdlp(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress", *ffmpeg(), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def youtube(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def meta(vid: str, url: str = "") -> dict[str, Any]:
    r = ytdlp("-J", "--skip-download", url or youtube(vid))
    if r.returncode != 0:
        raise SystemExit(f"yt-dlp couldn't read the video (it may need updating: YouTube changes often):\n"
                         f"{r.stderr[-1500:]}")
    d = json.loads(r.stdout)
    return {"id": vid or d.get("id"), "url": d.get("webpage_url") or url or youtube(vid), "title": d.get("title"),
            "channel": d.get("channel") or d.get("uploader"), "duration": d.get("duration"),
            "license": d.get("license") or _license(d.get("description") or ""),
            "chapters": [{"start": c["start_time"], "title": c["title"]} for c in d.get("chapters") or []],
            "description": (d.get("description") or "")[:1500],
            "human_captions": sorted(k for k in d.get("subtitles") or {} if k.startswith("en")),
            "auto_captions": "en" in (d.get("automatic_captions") or {})}


def _license(description: str) -> str | None:
    m = re.search(r"License:\s*([^\n]+)", description)
    return m[1].strip() if m else None


# ---------- captions ----------

def stamp(s: str) -> float:
    h, m, rest = ([0, *s.split(":")] if s.count(":") == 1 else s.split(":"))
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parse_vtt(text: str) -> list[dict[str, Any]]:
    """Caption cues as {start, end, text}. Automatic captions repeat each line as the next scrolls in; a cue whose
    text the previous one already ended with is dropped, and a cue that starts with the previous one keeps only
    what's new."""
    cues: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = block.strip().split("\n")
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        a, b = (x.strip().split(" ")[0] for x in lines[timing].split("-->"))
        words = re.sub(r"<[^>]+>", "", " ".join(lines[timing + 1:]))
        words = re.sub(r"\s+", " ", words.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")).strip()
        if not words:
            continue
        if cues:
            prev = cues[-1]["text"]
            if prev.endswith(words):
                continue
            if words.startswith(prev):
                words = words[len(prev):].strip()
                if not words:
                    continue
        cues.append({"start": round(stamp(a), 2), "end": round(stamp(b), 2), "text": words})
    return cues


def captions(info: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    vid = info["id"]
    if info["human_captions"]:
        lang, kind = info["human_captions"][0], ["--write-subs"]
    elif info["auto_captions"]:
        lang, kind = "en", ["--write-auto-subs"]
    else:
        return [], "none"
    r = ytdlp("--skip-download", *kind, "--sub-langs", lang, "--sub-format", "vtt", "-o", str(TMP / "cap"),
              info.get("url") or youtube(vid))
    files = sorted(TMP.glob("cap*.vtt"))
    if r.returncode != 0 or not files:
        print(f"captions failed: {r.stderr[-800:]}", flush=True)
        return [], "failed"
    return parse_vtt(files[0].read_text(encoding="utf-8")), "human" if kind == ["--write-subs"] else "automatic"


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def paragraphs(cues: list[dict[str, Any]], chapters: list[dict[str, Any]]) -> str:
    out: list[str] = []
    marks = sorted(chapters, key=lambda c: c["start"])
    para: list[str] = []
    start = 0.0
    for c in cues:
        while marks and c["start"] >= marks[0]["start"]:
            if para:
                out.append(f"[{mmss(start)}] {' '.join(para)}")
                para = []
            out.append(f"\n## {mmss(marks[0]['start'])} {marks[0]['title']}\n")
            marks.pop(0)
        if not para:
            start = c["start"]
        para.append(c["text"])
        if c["end"] - start >= PARAGRAPH:
            out.append(f"[{mmss(start)}] {' '.join(para)}")
            para = []
    if para:
        out.append(f"[{mmss(start)}] {' '.join(para)}")
    return "\n\n".join(out) + "\n"


def spoken(cues: list[dict[str, Any]], t: float, before: float = 8.0, after: float = 4.0) -> str:
    return " ".join(c["text"] for c in cues if c["end"] >= t - before and c["start"] <= t + after)


# ---------- frames ----------

def download(vid: str) -> Path:
    r = ytdlp("-f", VIDEO_FORMAT, "-o", str(TMP / "video.%(ext)s"), youtube(vid))
    files = [p for p in TMP.glob("video.*") if not p.name.endswith(".part")]
    if r.returncode != 0 or not files:
        raise SystemExit(f"yt-dlp couldn't fetch the video:\n{r.stderr[-1500:]}")
    return files[0]


def dhash(gray: np.ndarray) -> int:
    small = np.asarray(Image.fromarray(gray).resize((9, 8), Image.Resampling.BILINEAR), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def sample(video: Path) -> tuple[list[float], list[np.ndarray], list[float], list[Path]]:
    """One frame a second: its time, a 64x36 grayscale thumbnail for comparing, how much detail it shows (writing on
    a board, an equation on screen), and the full frame as a JPEG."""
    times: list[float] = []
    thumbs: list[np.ndarray] = []
    detail: list[float] = []
    jpegs: list[Path] = []
    (TMP / "s").mkdir(exist_ok=True)
    nxt = 0.0
    with av.open(str(video)) as box:
        stream = box.streams.video[0]
        stream.thread_type = "AUTO"
        for frame in box.decode(stream):
            if frame.time is None or frame.time < nxt:
                continue
            img = frame.to_image()
            path = TMP / "s" / f"{len(times):05d}.jpg"
            img.save(path, quality=88)
            gray = img.convert("L")
            times.append(float(frame.time))
            thumbs.append(np.asarray(gray.resize((64, 36), Image.Resampling.BILINEAR), dtype=np.float32))
            mid = np.asarray(gray.resize((320, 180), Image.Resampling.BILINEAR), dtype=np.float32)
            detail.append(float(np.abs(np.diff(mid, axis=0)).mean() + np.abs(np.diff(mid, axis=1)).mean()))
            jpegs.append(path)
            nxt = frame.time + STEP
    return times, thumbs, detail, jpegs


def z(xs: list[float]) -> list[float]:
    m, sd = float(np.mean(xs)), float(np.std(xs)) or 1.0
    return [(x - m) / sd for x in xs]


def pick(times: list[float], thumbs: list[np.ndarray], detail: list[float]) -> list[int]:
    """One frame per WINDOW seconds: the one showing the most (fine detail: chalk, symbols) while moving the least.
    Lecture cameras pan and zoom constantly, so cuts don't mark the boards; detail does. Then near-duplicates of the
    last few kept frames are dropped."""
    n = len(times)
    if n == 0:
        return []
    diff = [0.0] + [float(np.abs(thumbs[i] - thumbs[i - 1]).mean()) for i in range(1, n)]
    motion = [diff[i] + (diff[i + 1] if i + 1 < n else diff[i]) for i in range(n)]   # into and out of i
    score = [d - m for d, m in zip(z(detail), z(motion), strict=True)]
    chosen: list[int] = []
    w = 0
    while w < n:
        end = w
        while end < n and times[end] - times[w] < WINDOW:
            end += 1
        chosen.append(max(range(w, end), key=lambda i: (score[i], i)))
        w = end
    kept: list[int] = []
    hashes: list[int] = []
    for i in chosen:
        h = dhash(thumbs[i].astype(np.uint8))
        if all((h ^ k).bit_count() >= SAME for k in hashes[-4:]):
            kept.append(i)
            hashes.append(h)
    return kept


def sheets(frames: list[dict[str, Any]], out_dir: Path) -> list[dict[str, Any]]:
    cols, rows = GRID
    per = cols * rows
    font = ImageFont.load_default(size=24)
    out: list[dict[str, Any]] = []
    (out_dir / "sheets").mkdir(exist_ok=True)
    for k in range(0, len(frames), per):
        group = frames[k:k + per]
        with Image.open(out_dir / "frames" / group[0]["file"]) as first:
            cell = (round(CELL_H * first.width / first.height), CELL_H)
        sheet = Image.new("RGB", (cols * cell[0] + (cols + 1) * 6, rows * cell[1] + (rows + 1) * 6), "#0f141b")
        draw = ImageDraw.Draw(sheet)
        for j, f in enumerate(group):
            x, y = 6 + (j % cols) * (cell[0] + 6), 6 + (j // cols) * (cell[1] + 6)
            img = Image.open(out_dir / "frames" / f["file"]).convert("RGB")
            img.thumbnail(cell)
            sheet.paste(img, (x, y))
            label = mmss(f["t"])
            box = draw.textbbox((x + 6, y + 4), label, font=font)
            draw.rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2), fill="#0f141b")
            draw.text((x + 6, y + 4), label, fill="#e2a743", font=font)
        name = f"sheet-{k // per + 1:02d}.jpg"
        sheet.save(out_dir / "sheets" / name, quality=85)
        out.append({"file": name, "from": group[0]["t"], "to": group[-1]["t"], "frames": [f["file"] for f in group]})
    return out


def main(vid: str, out: Path = OUT) -> int:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        raise SystemExit("not a YouTube video id")
    info = meta(vid)
    cues, source = captions(info)
    info["captions"] = source
    (out / "meta.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    (out / "transcript.json").write_text(json.dumps(cues), encoding="utf-8")
    head = (f"# {info['title']}\n\n{info['channel']} · {mmss(info['duration'] or 0)} · {info['license'] or 'license unknown'}"
            f" · captions: {source}\n{info['url']}\n")
    (out / "transcript.md").write_text(head + "\n" + paragraphs(cues, info["chapters"]), encoding="utf-8")
    print(f"captions: {source}, {len(cues)} cues", flush=True)

    video = download(vid)
    times, thumbs, detail, jpegs = sample(video)
    video.unlink()                              # gone before anything is written: frames only from here on
    (out / "frames").mkdir(exist_ok=True)
    frames: list[dict[str, Any]] = []
    for i in pick(times, thumbs, detail):
        name = f"f-{int(times[i]):05d}.jpg"
        shutil.copyfile(jpegs[i], out / "frames" / name)   # /tmp and /out are different filesystems
        frames.append({"t": round(times[i], 1), "at": mmss(times[i]), "file": name, "spoken": spoken(cues, times[i])})
    (out / "frames.json").write_text(json.dumps(frames, indent=1), encoding="utf-8")
    groups = sheets(frames, out)
    (out / "sheets.json").write_text(json.dumps(groups, indent=1), encoding="utf-8")
    print(json.dumps({"title": info["title"], "duration": info["duration"], "captions": source, "cues": len(cues),
                      "samples": len(times), "frames": len(frames), "sheets": len(groups)}), flush=True)
    return 0


# ---------- study mode: one span of an animation, densely ----------

def secs(s: str) -> float:
    """'1:20', '1:20.5', '0:01:20', or '80' as seconds."""
    return sum(float(p) * 60 ** i for i, p in enumerate(reversed(s.strip().split(":"))))


def hms(t: float) -> str:
    """A time to the hundredth, for study frames: 1:20.17."""
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m)}:{s:05.2f}"


def span_name(start: float, end: float) -> str:
    return f"{start:g}-{end:g}"


def fetch_span(url: str, start: float, end: float) -> tuple[Path, float]:
    """The span's picture into TMP, and the time in the video its own time 0 is. ffmpeg cuts at the keyframe before
    the section's start (the copy's timestamps still count from the asked-for start), so the section begins a margin
    early and frames outside the span are skipped. If cutting fails, the whole video (offset 0)."""
    lo = max(0.0, start - 2.0)
    r = ytdlp("-f", STUDY_FORMAT, "--download-sections", f"*{lo:.2f}-{end + 1.0:.2f}", "-o",
              str(TMP / "span.%(ext)s"), url)
    files = [p for p in TMP.glob("span.*") if not p.name.endswith(".part")]
    if r.returncode == 0 and files:
        return files[0], lo
    print(f"cutting the span failed, fetching the whole video instead: {r.stderr[-400:]}", flush=True)
    for p in TMP.glob("span.*"):
        p.unlink()
    r = ytdlp("-f", STUDY_FORMAT, "-o", str(TMP / "video.%(ext)s"), url)
    files = [p for p in TMP.glob("video.*") if not p.name.endswith(".part")]
    if r.returncode != 0 or not files:
        raise SystemExit(f"yt-dlp couldn't fetch the video:\n{r.stderr[-1500:]}")
    return files[0], 0.0


def sample_span(video: Path, offset: float, start: float, end: float, fps: float
                ) -> tuple[float, dict[str, list[float]], list[dict[str, Any]]]:
    """Decode the span once. The motion curve at the video's own rate (at most CURVE_FPS): each frame's time, its
    energy, and its cut score (study.pair, against the frame before). And `fps` frames a second kept: each saved to
    TMP/s as a JPEG, with a small RGB copy for palettes and composition. Returns the curve's rate too."""
    curve: dict[str, list[float]] = {"t": [], "energy": [], "moved": [], "score": []}
    kept: list[dict[str, Any]] = []
    (TMP / "s").mkdir(exist_ok=True)
    prev: Any = None
    with av.open(str(video)) as box:
        stream = box.streams.video[0]
        stream.thread_type = "AUTO"
        rate = float(stream.average_rate or 30)
        cfps = min(rate, CURVE_FPS)
        w, h = stream.codec_context.width, stream.codec_context.height
        thumb, color = (THUMB_W, max(2, round(THUMB_W * h / w))), (COLOR_W, max(2, round(COLOR_W * h / w)))
        slack = 0.25 / rate
        next_c = next_s = start
        for frame in box.decode(stream):
            if frame.time is None:
                continue
            t = offset + float(frame.time)
            if t < start - slack:
                continue
            if t >= end:
                break
            if t >= next_c - slack:
                g = study.gray(frame.reformat(width=thumb[0], height=thumb[1], format="rgb24").to_ndarray())
                e, mv, sc = study.pair(prev, g) if prev is not None else (0.0, 0.0, 0.0)
                curve["t"].append(round(t, 4))
                curve["energy"].append(e)
                curve["moved"].append(mv)
                curve["score"].append(sc)
                prev = g
                while next_c <= t + slack:
                    next_c += 1 / cfps
            if t >= next_s - slack and next_s < end - slack:   # a frame grid of exactly (end - start) * fps
                path = TMP / "s" / f"s-{t:07.2f}.jpg"
                frame.to_image().save(path, quality=88)
                kept.append({"t": round(t, 3), "path": path, "ci": len(curve["t"]) - 1,   # its place on the curve
                             "rgb": frame.reformat(width=color[0], height=color[1], format="rgb24").to_ndarray()})
                while next_s <= t + slack:
                    next_s += 1 / fps
    return cfps, curve, kept


def youtu_be(info: dict[str, Any], t: float) -> str:
    vid = info.get("id") or ""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid) and "youtube" in (info.get("url") or ""):
        return f"https://youtu.be/{vid}?t={int(t)}"
    return info.get("url") or ""


def attribution(info: dict[str, Any], start: float, end: float) -> dict[str, Any]:
    """What to cite when a study's findings or frames are used: creator, title, the moment, the license."""
    link = youtu_be(info, start)
    who, title = info.get("channel") or "unknown creator", info.get("title") or "untitled"
    return {"creator": who, "title": title, "span": f"{mmss(start)}-{mmss(end)}", "link": link,
            "license": info.get("license") or "not stated: assume all rights reserved",
            "credit": f"{who}, “{title}”, {mmss(start)}–{mmss(end)}, {link}"}


def shots_of(curve: dict[str, list[float]], cuts: list[int], segs: list[dict[str, Any]], kept: list[dict[str, Any]],
             cues: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    """The span split at its cuts. Each shot: its times, holds and moves, palette and tone over up to 8 of its kept
    frames, composition against its background (its dominant color), the frame that best shows it (the end of its
    longest hold: the composed picture), and what's said during it."""
    edges = [start, *[curve["t"][c] for c in cuts], end]
    out: list[dict[str, Any]] = []
    for n, (a, b) in enumerate(itertools.pairwise(edges), 1):
        inside = [f for f in kept if a - 1e-3 <= f["t"] < b] or [min(kept, key=lambda f: abs(f["t"] - (a + b) / 2))]
        some = inside[::max(1, len(inside) // 8)][:8]
        pal = study.palette(np.concatenate([f["rgb"].reshape(-1, 3) for f in some]))
        bg = tuple(pal[0]["rgb"])
        mine = [g for g in segs if a - 1e-3 <= g["start"] < b]
        holds = [g for g in mine if g["kind"] == "hold"]
        moves = [g for g in mine if g["kind"] == "move"]
        key = inside[len(inside) // 2]
        if holds:
            longest = max(holds, key=lambda g: g["duration"])
            key = ([f for f in inside if longest["start"] - 1e-3 <= f["t"] < longest["end"]] or [key])[-1]
        # the boldest ink: the palette color (1% of the frame or more) with the most contrast against the background
        ink = max((c for c in pal[1:] if c["share"] >= 0.01), default=None,
                  key=lambda c: study.contrast_ratio(bg, tuple(c["rgb"])))
        out.append({
            "n": n, "start": round(a, 3), "end": round(b, 3), "duration": round(b - a, 3), "at": hms(a),
            "open": [n == 1, n == len(edges) - 1],   # the span cuts into it at its start / end
            "key_frame": key["path"].name, "holds": len(holds), "moves": len(moves),
            "longest_hold": max((g["duration"] for g in holds), default=0.0),
            "palette": pal, "tone": study.tone([f["rgb"] for f in some]),
            "ink_contrast": round(study.contrast_ratio(bg, tuple(ink["rgb"])), 1) if ink else None,
            "composition": study.composition([f["rgb"] for f in some], bg),
            "spoken": " ".join(c["text"] for c in cues if c["end"] > a and c["start"] < b),
        })
    return out


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def motion_plot(curve: dict[str, list[float]], cuts: list[int], segs: list[dict[str, Any]],
                shots: list[dict[str, Any]], cues: list[dict[str, Any]], start: float, end: float, title: str,
                path: Path) -> float:
    """The motion curve as a picture: energy over time, holds shaded, cuts in red, shot numbers along the top, and a
    tick under the axis where each caption line starts (for sync). Spikes above the scale are clipped with a red
    mark. Returns the top of the scale, which the filmstrips' bars share."""
    wd, ht, left, right, top, bottom = 1400, 360, 64, 24, 74, 50
    img = Image.new("RGB", (wd, ht), INK)
    d = ImageDraw.Draw(img)
    small, big = font(13), font(16)
    e = curve["energy"]
    scale = max(float(np.percentile(e, 99)) * 1.15 if e else 0.0, 0.01)

    def x(t: float) -> float:
        return left + (t - start) / (end - start) * (wd - left - right)

    def y(v: float) -> float:
        return ht - bottom - min(v, scale) / scale * (ht - bottom - top)

    for g in segs:
        if g["kind"] == "hold":
            d.rectangle((x(g["start"]), top, x(g["end"]), ht - bottom), fill=PANEL)
    for c in cuts:
        d.line((x(curve["t"][c]), top, x(curve["t"][c]), ht - bottom), fill=RED, width=2)
    pts = [(x(t), y(v)) for t, v in zip(curve["t"], e, strict=True)]
    if len(pts) > 1:
        d.line(pts, fill=AMBER, width=2)
    for t, v in zip(curve["t"], e, strict=True):
        if v > scale:
            d.polygon([(x(t) - 4, top - 2), (x(t) + 4, top - 2), (x(t), top + 5)], fill=RED)
    for s in shots:
        d.text((x(s["start"]) + 4, top - 17), f"#{s['n']}", fill="#c9d1db", font=small)
    for c in cues:
        if start <= c["start"] < end:
            d.line((x(c["start"]), ht - bottom + 3, x(c["start"]), ht - bottom + 11), fill=MUTED, width=1)
    d.line((left, ht - bottom, wd - right, ht - bottom), fill=MUTED)
    step = next(s for s in (1, 2, 5, 10, 15, 30, 60) if (end - start) / s <= 14)
    t = np.ceil(start / step) * step
    while t <= end + 1e-6:
        d.line((x(t), ht - bottom, x(t), ht - bottom + 16), fill="#c9d1db")
        d.text((x(t) - 12, ht - bottom + 20), mmss(t), fill="#c9d1db", font=small)
        t += step
    d.text((left, 10), title, fill="#e6e9ee", font=big)
    d.text((left, 30), "motion energy (mean change of the frame); shaded: holds; red: cuts; ticks under the axis: "
                       "caption lines start", fill=MUTED, font=small)
    img.save(path)
    return scale


def cell(height: int, w: int, h: int, portrait_width: int) -> tuple[int, int]:
    """A sheet cell for a w x h video: `height` tall for landscape, `portrait_width` wide for portrait (phone video,
    Reels), so a vertical frame isn't shrunk to a sliver."""
    if w >= h:
        return round(height * w / h), height
    return portrait_width, round(portrait_width * h / w)


def strips(kept: list[dict[str, Any]], out_dir: Path) -> list[dict[str, Any]]:
    """Filmstrips: STRIP columns by rows of the kept frames, each labeled with its time, a red frame and the shot's
    number where a shot starts, and a bar under it for the most motion since the frame before (amber: moving, gray:
    holding, red: a cut)."""
    cols, rows = STRIP
    per = cols * rows
    with Image.open(out_dir / "frames" / kept[0]["file"]) as first:
        cw, ch = cell(STRIP_H, first.width, first.height, STRIP_H)
    pad, bar = 5, 7
    label = font(15)
    out: list[dict[str, Any]] = []
    for k in range(0, len(kept), per):
        group = kept[k:k + per]
        sheet = Image.new("RGB", (cols * (cw + pad) + pad, rows * (ch + bar + 3 + pad) + pad), INK)
        d = ImageDraw.Draw(sheet)
        for j, f in enumerate(group):
            x0, y0 = pad + (j % cols) * (cw + pad), pad + (j // cols) * (ch + bar + 3 + pad)
            with Image.open(out_dir / "frames" / f["file"]) as im:
                pic = im.convert("RGB")
            pic.thumbnail((cw, ch))
            sheet.paste(pic, (x0, y0))
            if f["shot_start"]:
                d.rectangle((x0, y0, x0 + cw - 1, y0 + ch - 1), outline=RED, width=3)
            text = hms(f["t"]) + (f"  #{f['shot']}" if f["shot_start"] else "")
            box = d.textbbox((x0 + 5, y0 + 3), text, font=label)
            d.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill=INK)
            d.text((x0 + 5, y0 + 3), text, fill=RED if f["shot_start"] else AMBER, font=label)
            colour = {"move": AMBER, "hold": "#5a6675", "cut": RED}[f["state"]]
            d.rectangle((x0, y0 + ch + 3, x0 + max(2, round(cw * f["bar"])), y0 + ch + 3 + bar),
                        fill=colour)
        name = f"strip-{k // per + 1:02d}.jpg"
        sheet.save(out_dir / "sheets" / name, quality=86)
        out.append({"file": name, "from": group[0]["t"], "to": group[-1]["t"], "frames": len(group)})
    return out


def shot_sheets(shots: list[dict[str, Any]], out_dir: Path) -> list[str]:
    """One cell per shot: its key frame, its times and length, holds and moves, tone and where its ink sits, and its
    palette as a bar of swatches as wide as their shares."""
    cols, rows = SHOT_GRID
    per = cols * rows
    with Image.open(out_dir / "frames" / shots[0]["key_frame"]) as first:
        cw, ch = cell(SHOT_H, first.width, first.height, round(SHOT_H * 1.5))
    pad, text_h, swatch = 8, 46, 16
    small = font(14)
    names: list[str] = []
    for k in range(0, len(shots), per):
        group = shots[k:k + per]
        n_rows = (len(group) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (cw + pad) + pad, n_rows * (ch + text_h + swatch + 2 * pad) + pad), INK)
        d = ImageDraw.Draw(sheet)
        for j, s in enumerate(group):
            x0, y0 = pad + (j % cols) * (cw + pad), pad + (j // cols) * (ch + text_h + swatch + 2 * pad)
            with Image.open(out_dir / "frames" / s["key_frame"]) as im:
                pic = im.convert("RGB")
            pic.thumbnail((cw, ch))
            sheet.paste(pic, (x0, y0))
            c = s["composition"]
            where = (f"ink {c['coverage']:.0%} at ({c['centroid'][0]:.2f}, {c['centroid'][1]:.2f}), {c['nearest']}"
                     if c.get("centroid") else "no ink")
            line1 = (f"#{s['n']}  {hms(s['start'])}-{hms(s['end'])}  {s['duration']:.1f} s  "
                     f"{s['holds']} holds, {s['moves']} moves")
            line2 = f"L* {s['tone']['lightness']:.0f} ±{s['tone']['contrast']:.0f}  {where}"
            d.text((x0, y0 + ch + 4), line1, fill="#e6e9ee", font=small)
            d.text((x0, y0 + ch + 24), line2, fill=MUTED, font=small)
            sx = x0
            for p in s["palette"]:
                wpx = round(cw * p["share"])
                d.rectangle((sx, y0 + ch + text_h, sx + max(1, wpx) - 1, y0 + ch + text_h + swatch),
                            fill=tuple(p["rgb"]))
                sx += wpx
            d.rectangle((x0, y0 + ch + text_h, x0 + cw - 1, y0 + ch + text_h + swatch), outline=MUTED)
        name = f"shots-{k // per + 1:02d}.jpg"
        sheet.save(out_dir / name, quality=88)
        names.append(name)
    return names


def study_main(source: str, start: float, end: float, fps: float = STUDY_FPS, out: Path = OUT,
               credit: dict[str, str] | None = None) -> int:
    """Study one span of a video into `out` (see the module docstring). `source` is a YouTube id, an http(s) link, or
    (from a session only) a local video file, which is read in place and left alone. `credit` (creator, title, link)
    fills in what a local file can't say about itself."""
    if not 0 <= start < end or end - start > STUDY_MAX:
        raise SystemExit(f"a study spans 0 < end - start <= {STUDY_MAX:g} seconds")
    fps = min(max(fps, 1.0), STUDY_MAX_FPS)
    local = Path(source) if not re.match(r"^https?://", source) and Path(source).is_file() else None
    vid = source if re.fullmatch(r"[A-Za-z0-9_-]{11}", source) else ""
    url = youtube(vid) if vid else source
    if local:
        info: dict[str, Any] = {"id": None, "url": "", "title": local.name, "channel": None, "duration": None,
                                "license": None, "human_captions": [], "auto_captions": False}
    elif not vid and not re.match(r"^https?://", url):
        raise SystemExit("give a YouTube id, an http(s) link, or a video file")
    else:
        info = meta(vid, url)
    if credit:
        info |= {"channel": credit.get("creator") or info.get("channel"), "title": credit.get("title") or info["title"],
                 "url": credit.get("link") or info["url"]}
    if info.get("duration"):
        end = min(end, float(info["duration"]))
    cues, source_kind = captions(info) if not local else ([], "none")
    span_cues = [c for c in cues if c["end"] > start and c["start"] < end]

    video, offset = (local, 0.0) if local else fetch_span(url, start, end)
    cfps, curve, kept = sample_span(video, offset, start, end, fps)
    if not local:
        video.unlink()                          # gone before anything is written: frames only from here on
    if len(curve["t"]) < 3 or not kept:
        raise SystemExit(f"no frames decoded between {mmss(start)} and {mmss(end)}")
    end = min(end, curve["t"][-1] + 1 / cfps)   # a video shorter than the span asked for (Reels give no length)
    (out / "frames").mkdir(exist_ok=True)
    (out / "sheets").mkdir(exist_ok=True)
    for f in kept:
        shutil.copyfile(f["path"], out / "frames" / f["path"].name)
        f["file"] = f["path"].name

    cuts = study.find_cuts(curve["energy"], curve["score"], cfps)
    curve["raw"] = curve["energy"]
    curve["energy"] = study.speed(curve["raw"], cuts, cfps)
    curve["moved"] = study.speed(curve["moved"], cuts, cfps)
    segs = study.segments(curve["energy"], curve["moved"], curve["t"], cuts)
    shots = shots_of(curve, cuts, segs, kept, span_cues, start, end)
    state = ["hold"] * len(curve["t"])
    for g in segs:
        for i, t in enumerate(curve["t"]):
            if g["start"] - 1e-3 <= t < g["end"] - 1e-3:
                state[i] = g["kind"]
    for c in cuts:
        state[c] = "cut"
    with (out / "motion.csv").open("w", encoding="utf-8") as fh:
        # energy and moved are speeds (repeats spread, a 1/6 s mean); raw is the energy as measured
        fh.write("t,energy,moved,raw,cut_score,state\n")
        for t, e, m, r, sc, st in zip(curve["t"], curve["energy"], curve["moved"], curve["raw"], curve["score"],
                                      state, strict=True):
            fh.write(f"{t:.4f},{e:.5f},{m:.5f},{r:.5f},{sc:.3f},{st}\n")
    title = f"{info.get('channel')}: {info.get('title')}  {hms(start)}-{hms(end)}"
    scale = motion_plot(curve, cuts, segs, shots, span_cues, start, end, title, out / "motion.png")

    frames: list[dict[str, Any]] = []
    for k, f in enumerate(kept):
        idx = list(range(kept[k - 1]["ci"] + 1 if k else 0, f["ci"] + 1))   # the curve since the frame before
        shot = next(s for s in reversed(shots) if s["start"] - 1e-3 <= f["t"]) if shots else {"n": 1}
        prev_shot = frames[-1]["shot"] if frames else 0
        frames.append({"t": f["t"], "at": hms(f["t"]), "file": f["file"], "shot": shot["n"],
                       "shot_start": shot["n"] != prev_shot and k > 0,
                       "state": "cut" if any(state[i] == "cut" for i in idx) else (state[idx[-1]] if idx else "hold"),
                       "motion": round(max((curve["energy"][i] for i in idx), default=0.0), 5)})
    for f in frames:
        f["bar"] = min(1.0, f["motion"] / scale)
    groups = strips(frames, out)
    shot_files = shot_sheets(shots, out)
    for f in frames:
        del f["bar"]
    (out / "frames.json").write_text(json.dumps(frames, indent=1), encoding="utf-8")

    holds = [g for g in segs if g["kind"] == "hold"]
    moves = [g for g in segs if g["kind"] == "move"]
    span = end - start
    result: dict[str, Any] = {
        "video": {k: info.get(k) for k in ("id", "url", "title", "channel", "duration", "license")},
        "attribution": attribution(info, start, end),
        "span": {"start": start, "end": end, "at": f"{hms(start)}-{hms(end)}", "seconds": round(span, 3)},
        "rates": {"frames": fps, "curve": cfps},
        "summary": {
            "shots": len(shots), "cuts_per_minute": round(len(cuts) / span * 60, 1),
            "shot_seconds": {"median": round(float(np.median([s["duration"] for s in shots])), 2),
                             "shortest": min(s["duration"] for s in shots),
                             "longest": max(s["duration"] for s in shots)},
            "holds": {"count": len(holds), "share_of_time": round(sum(g["duration"] for g in holds) / span, 2),
                      "median_seconds": round(float(np.median([g["duration"] for g in holds])), 2) if holds else 0,
                      "longest_seconds": max((g["duration"] for g in holds), default=0.0)},
            "moves": {"count": len(moves),
                      "median_seconds": round(float(np.median([g["duration"] for g in moves])), 2) if moves else 0,
                      "eases": {w: sum(g["ease"] == w for g in moves)
                                for w in ("ease in-out", "fast start", "fast finish", "steady", "snap")}},
        },
        "shots": shots,
        "segments": segs,
        "captions": source_kind,
        "transcript": span_cues,
        "files": {"motion_plot": "motion.png", "motion_csv": "motion.csv", "shot_sheets": shot_files,
                  "strips": groups, "frames": "frames.json"},
        "limits": ("Measured from frames, not seen as video: speed is inferred from how much of the picture changes "
                   "between frames (a big shape moving slowly can read like a small one moving fast), cuts are "
                   "isolated spikes that replace the picture (a whole-screen snap counts as one; a fade never does), "
                   "and there is no sound: music, sound design, and the voice's tone are missing."),
    }
    (out / "study.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"title": info.get("title"), "span": result["span"]["at"], "frames": len(frames),
                      "curve_points": len(curve["t"]), "shots": len(shots), "holds": len(holds),
                      "moves": len(moves), "strips": len(groups), "cues": len(span_cues)}), flush=True)
    return 0


def study_cli(argv: list[str]) -> int:
    global TMP
    ap = argparse.ArgumentParser(prog="watch_job.py --study")
    ap.add_argument("source", help="a YouTube id, any http(s) link yt-dlp reads, or a video file")
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("fps", nargs="?", type=float, default=STUDY_FPS)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--tmp", type=Path, default=TMP, help="where the span lives while it's decoded")
    for k in ("creator", "title", "link"):
        ap.add_argument(f"--{k}", default="", help=f"the {k} to credit (for a file, which can't say)")
    a = ap.parse_args(argv)
    TMP = a.tmp
    credit = {k: getattr(a, k) for k in ("creator", "title", "link") if getattr(a, k)}
    return study_main(a.source, secs(a.start), secs(a.end), a.fps, a.out, credit or None)


def clear_tmp() -> None:
    for p in TMP.iterdir():
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)


def serve() -> int:
    """The watcher service: the oldest request in QUEUE, watched into WATCHED/<id>, one at a time, forever. A request
    left 'working' by a restart goes back in line. A failure leaves <id>.failed with the error, which a new request
    for the same video clears. A study request is <id>@<start>-<end>.json, studied into WATCHED/<id>/study/<start>-<end>;
    re-watching a video keeps its studies."""
    for stale in QUEUE.glob("*.working"):
        stale.rename(stale.with_suffix(".json"))
    print("watcher: waiting for requests", flush=True)
    while True:
        waiting = sorted(QUEUE.glob("*.json"), key=lambda q: q.stat().st_mtime)
        if not waiting:
            time.sleep(POLL)
            continue
        name = waiting[0].stem
        vid = name.split("@")[0]
        ask = json.loads(waiting[0].read_text(encoding="utf-8")) if "@" in name else {}
        working = waiting[0].with_suffix(".working")
        waiting[0].rename(working)
        partial = WATCHED / f".{name}.partial"   # not a video id, so the API never reads it half-written
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        clear_tmp()
        print(f"watcher: {name}", flush=True)
        try:
            if ask:
                study_main(vid, float(ask["start"]), float(ask["end"]), float(ask.get("fps") or STUDY_FPS), partial)
                final = WATCHED / vid / "study" / name.split("@")[1]
                final.parent.mkdir(parents=True, exist_ok=True)
            else:
                main(vid, partial)
                final = WATCHED / vid
                if (final / "study").is_dir():
                    (final / "study").rename(partial / "study")
            shutil.rmtree(final, ignore_errors=True)
            partial.rename(final)
            working.unlink(missing_ok=True)
        except (Exception, SystemExit) as e:  # noqa: BLE001 -- one bad video doesn't stop the service
            shutil.rmtree(partial, ignore_errors=True)
            working.with_suffix(".failed").write_text(str(e)[-2000:] or type(e).__name__, encoding="utf-8")
            working.unlink(missing_ok=True)
            print(f"watcher: {vid} failed: {str(e)[-300:]}", flush=True)
        finally:
            clear_tmp()


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        sys.exit(serve())
    sys.exit(study_cli(sys.argv[2:]) if sys.argv[1] == "--study" else main(sys.argv[1]))

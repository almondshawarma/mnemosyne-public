"""Lecture videos the tutor has watched: transcripts, key frames, and contact sheets, never the videos themselves.

The watcher (services/watch/, the `watcher` compose service, or scripts/watch.py from a session) writes one folder
per YouTube video under media/watch/<id>/: meta.json, transcript.json, frames.json + frames/, sheets.json + sheets/.
This module reads those folders for the API and the MCP tools, and queues new videos for the watcher.

Watching costs no LLM tokens on the server. The understanding happens in Aman's own Claude session, which reads:
- `search`: where in his watched videos something is said, with a timestamped link;
- `span`: what's said between two times, plus the contact sheets that cover it (each one is 12 frames in one image);
- `frames`: a few full frames at chosen times, for a board worth reading closely.
Spans keep the cost down: five minutes of a lecture is a couple of thousand tokens; the whole of one is about 20k.

Study mode is for animations Aman wants to learn craft from: `study` queues one span (up to 120 s) for the watcher, which
writes media/watch/<id>/study/<start>-<end>/ (dense frames, filmstrips, a motion curve with holds and cuts, shots with
palettes and composition, study.json with the attribution to cite), and reads it back once it's done.
"""

import base64
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FOLDER = "watch"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
LINK = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/|live/)([A-Za-z0-9_-]{11})")
PARAGRAPH = 45.0       # seconds of speech per transcript paragraph
SHEETS = 3             # contact sheets a span returns at most
AUTO_SHEETS = 15 * 60  # images="auto" sends sheets only for spans this short or shorter
FRAMES = 4             # full frames per call
QUEUE_MAX = 5          # videos (and studies) waiting at once
REQUEST = re.compile(r"^([A-Za-z0-9_-]{11})(?:@(\d+(?:\.\d+)?-\d+(?:\.\d+)?))?$")   # <id> or <id>@<start>-<end>
STUDY_MAX = 120.0      # seconds a study may span
STUDY_FPS = (1.0, 12.0)
STUDY_SHEETS = 2       # shot sheets a study call returns at most
LOOK = 4               # filmstrips or full frames a `look` returns at most


class VideoError(ValueError):
    pass


def video_id(s: str) -> str:
    """The id from a YouTube link (watch, youtu.be, embed, shorts, live) or the bare id. Only YouTube: the watcher
    must not be pointed at arbitrary URLs."""
    s = s.strip()
    if VIDEO_ID.match(s):
        return s
    m = LINK.search(s)
    if m and re.match(r"^https?://([a-z0-9-]+\.)?(youtube\.com|youtu\.be)/", s):
        return m[1]
    raise VideoError("give a YouTube link or an 11-character video id")


def seconds(s: str | float | None) -> float | None:
    """'12:30', '1:02:03', '750', or a number, as seconds; empty means not given."""
    if s is None or s == "":
        return None
    if isinstance(s, int | float):
        return float(s)
    try:
        parts = [float(p) for p in str(s).strip().split(":")]
    except ValueError:
        raise VideoError(f"can't read the time {s!r}: use mm:ss, h:mm:ss, or seconds") from None
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def folder(roots: list[Path], vid: str) -> Path | None:
    if not VIDEO_ID.match(vid):
        return None
    for root in roots:
        d = root / FOLDER / vid
        if (d / "meta.json").is_file() and (d / "sheets.json").is_file():
            return d
    return None


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def link(vid: str, t: float = 0) -> str:
    return f"https://youtu.be/{vid}?t={int(t)}" if t else f"https://youtu.be/{vid}"


def paragraphs(cues: list[dict[str, Any]], lo: float = 0, hi: float = float("inf")) -> list[dict[str, Any]]:
    """Caption cues in [lo, hi) as paragraphs of about PARAGRAPH seconds: {at, t, text}."""
    out: list[dict[str, Any]] = []
    para: list[str] = []
    start = 0.0
    for c in cues:
        if c["end"] < lo or c["start"] >= hi:
            continue
        if not para:
            start = c["start"]
        para.append(c["text"])
        if c["end"] - start >= PARAGRAPH:
            out.append({"at": mmss(start), "t": round(start, 1), "text": " ".join(para)})
            para = []
    if para:
        out.append({"at": mmss(start), "t": round(start, 1), "text": " ".join(para)})
    return out


# ---------- reading ----------

def library(roots: list[Path], queue: Path | None = None) -> dict[str, Any]:
    """Every watched video (title, channel, length, license, chapters, how many sheets), and what's waiting."""
    seen: dict[str, dict[str, Any]] = {}
    for root in roots:
        base = root / FOLDER
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.name in seen or folder([root], d.name) is None:
                continue
            m = _json(d / "meta.json")
            seen[d.name] = {"id": d.name, "title": m.get("title"), "channel": m.get("channel"),
                            "duration": mmss(m.get("duration") or 0), "license": m.get("license"),
                            "captions": m.get("captions"), "link": link(d.name),
                            "chapters": [{"at": mmss(c["start"]), "title": c["title"]} for c in m.get("chapters", [])],
                            "sheets": len(_json(d / "sheets.json"))}
    return {"videos": list(seen.values()), "studies": studies(roots), "waiting": status_all(queue) if queue else []}


def search(roots: list[Path], query: str, vid: str = "", limit: int = 10) -> dict[str, Any]:
    """Paragraphs (about 45 s each) that contain every word of the query, across the watched videos or one of them.
    A word matches as a prefix ("eigen" finds "eigenvalue")."""
    words = [w for w in re.findall(r"[a-z0-9']+", query.lower()) if len(w) > 1]
    if not words:
        raise VideoError("search for at least one word")
    vids = [vid] if vid else [v["id"] for v in library(roots)["videos"]]
    hits: list[dict[str, Any]] = []
    for v in vids:
        d = folder(roots, v)
        if d is None:
            raise VideoError(f"{v} hasn't been watched yet")
        title = _json(d / "meta.json").get("title")
        for p in paragraphs(_json(d / "transcript.json")):
            tokens = re.findall(r"[a-z0-9']+", p["text"].lower())
            if all(any(t.startswith(w) for t in tokens) for w in words):
                score = sum(sum(t.startswith(w) for t in tokens) for w in words)
                hits.append({"video": v, "title": title, "at": p["at"], "link": link(v, p["t"]), "text": p["text"],
                             "_score": score})
    hits.sort(key=lambda h: -h["_score"])
    return {"query": query, "total": len(hits), "hits": [{k: v for k, v in h.items() if k != "_score"}
                                                         for h in hits[:max(1, min(limit, 30))]]}


def span(roots: list[Path], vid: str, start: str | float | None = None, end: str | float | None = None,
         images: str = "auto") -> dict[str, Any]:
    """What's said from `start` to `end` (default: all of it), and the contact sheets that cover that stretch (at
    most SHEETS). images: "auto" sends sheets only for spans of AUTO_SHEETS or less, "yes", or "no"."""
    d = folder(roots, vid)
    if d is None:
        raise VideoError(f"{vid} hasn't been watched yet: ask for it with the watch request first")
    m = _json(d / "meta.json")
    length = float(m.get("duration") or 0)
    lo, hi = seconds(start) or 0.0, seconds(end) or length or float("inf")
    if hi <= lo:
        raise VideoError("end must come after start")
    sheets = [s for s in _json(d / "sheets.json") if s["to"] >= lo and s["from"] <= hi]
    send = images == "yes" or (images == "auto" and hi - lo <= AUTO_SHEETS)
    out: dict[str, Any] = {
        "video": vid, "title": m.get("title"), "channel": m.get("channel"), "license": m.get("license"),
        "captions": m.get("captions"), "span": f"{mmss(lo)}-{mmss(min(hi, length) if length else hi)}",
        "link": link(vid, lo),
        "chapters": [{"at": mmss(c["start"]), "title": c["title"]} for c in m.get("chapters", [])
                     if lo <= c["start"] < hi],
        "transcript": paragraphs(_json(d / "transcript.json"), lo, hi),
        "sheets": [{"file": s["file"], "covers": f"{mmss(s['from'])}-{mmss(s['to'])}"} for s in sheets],
        "images": [],
    }
    if send:
        for s in sheets[:SHEETS]:
            out["images"].append({"label": f"contact sheet {mmss(s['from'])}-{mmss(s['to'])} (each frame is labeled "
                                           "with its time)",
                                  "jpeg_base64": base64.b64encode((d / "sheets" / s["file"]).read_bytes()).decode()})
        if len(sheets) > SHEETS:
            out["note"] = (f"{len(sheets)} sheets cover this span; sent the first {SHEETS}. Narrow start/end for the "
                           "rest.")
    elif sheets:
        out["note"] = "Sheets not sent for a span this long; narrow start/end (15 minutes or less) or pass images='yes'."
    return out


def frames(roots: list[Path], vid: str, at: list[str]) -> dict[str, Any]:
    """The kept frames nearest the given times (at most FRAMES), full size, each with what was said around it."""
    d = folder(roots, vid)
    if d is None:
        raise VideoError(f"{vid} hasn't been watched yet")
    kept: list[dict[str, Any]] = _json(d / "frames.json")
    if not kept or not at:
        raise VideoError("no frames to pick from" if not kept else "give at least one time")
    picked: list[dict[str, Any]] = []
    for t in at[:FRAMES]:
        want = seconds(t)
        f = min(kept, key=lambda k: abs(k["t"] - (want or 0)))
        if f not in picked:
            picked.append(f)
    return {"video": vid, "frames": [{"at": f["at"], "link": link(vid, f["t"]), "spoken": f.get("spoken", "")}
                                     for f in picked],
            "images": [{"label": f"frame at {f['at']}",
                        "jpeg_base64": base64.b64encode((d / "frames" / f["file"]).read_bytes()).decode()}
                       for f in picked]}


# ---------- the watcher's queue: one small JSON file per request ----------

def status(roots: list[Path], queue: Path | None, vid: str) -> str:
    if folder(roots, vid) is not None:
        return "watched"
    if queue is None:
        return "unknown"
    for state in ("working", "json", "failed"):
        if (queue / f"{vid}.{state}").exists():
            return {"json": "waiting"}.get(state, state)
    return "not watched"


def status_all(queue: Path) -> list[dict[str, Any]]:
    if not queue.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(queue.iterdir()):
        m = REQUEST.match(p.stem)
        if p.suffix in (".json", ".working", ".failed") and m:
            item: dict[str, Any] = {"id": m[1], "state": {".json": "waiting"}.get(p.suffix, p.suffix[1:])}
            if m[2]:
                item["study"] = m[2]
            if p.suffix == ".failed":
                item["error"] = p.read_text(encoding="utf-8", errors="replace")[-600:]
            out.append(item)
    return out


def request(roots: list[Path], queue: Path | None, raw: str, by: str = "mcp") -> dict[str, Any]:
    """Queue a YouTube video for the watcher. Watched or already waiting: nothing to do. A failed one is retried."""
    vid = video_id(raw)
    if queue is None:
        raise VideoError("the watcher isn't set up on this server")
    state = status(roots, queue, vid)
    if state in ("watched", "waiting", "working"):
        return {"id": vid, "state": state, "link": link(vid)}
    if sum(1 for i in status_all(queue) if i["state"] in ("waiting", "working")) >= QUEUE_MAX:
        raise VideoError(f"{QUEUE_MAX} videos are already waiting; try again once they're done")
    (queue / f"{vid}.failed").unlink(missing_ok=True)
    (queue / f"{vid}.json").write_text(json.dumps({"id": vid, "by": by,
                                                   "requested_at": datetime.now(UTC).isoformat(timespec="seconds")}),
                                       encoding="utf-8")
    return {"id": vid, "state": "waiting", "link": link(vid),
            "next": "A lecture takes about two minutes. Then mnemosyne_video_watch reads it."}


# ---------- study mode: one span of an animation, densely (watch_job.py --study, study.py) ----------

def study_span(start: str | float | None, end: str | float | None) -> tuple[float, float, str]:
    """A study's span in seconds and its folder name (seconds, as watch_job.py names it)."""
    lo, hi = seconds(start), seconds(end)
    if lo is None or hi is None:
        raise VideoError("a study needs start and end (mm:ss)")
    if not 0 <= lo < hi or hi - lo > STUDY_MAX:
        raise VideoError(f"a study spans up to {STUDY_MAX:g} seconds, end after start")
    return lo, hi, f"{lo:g}-{hi:g}"


def study_folder(roots: list[Path], vid: str, name: str) -> Path | None:
    if not VIDEO_ID.match(vid):
        return None
    for root in roots:
        d = root / FOLDER / vid / "study" / name
        if (d / "study.json").is_file():
            return d
    return None


def studies(roots: list[Path]) -> list[dict[str, Any]]:
    """Every finished study: video, span, title."""
    out: list[dict[str, Any]] = []
    for root in roots:
        for f in sorted((root / FOLDER).glob("*/study/*/study.json")):
            s = _json(f)
            out.append({"video": f.parents[2].name, "span": s["span"]["at"], "title": s["video"].get("title"),
                        "credit": s["attribution"]["credit"]})
    return out


def _image(path: Path, label: str) -> dict[str, Any]:
    im: dict[str, Any] = {"label": label, "jpeg_base64": base64.b64encode(path.read_bytes()).decode()}
    if path.suffix == ".png":
        im["format"] = "png"
    return im


def study_read(d: Path, look: str = "") -> dict[str, Any]:
    """A finished study for a chat: the summary, every shot (palette, tone, composition, what's said), the holds and
    moves, the transcript, and images: the motion plot and the shot sheets. `look` adds detail: a stretch ("1:25-1:30")
    sends the filmstrips covering it, times ("1:27.5,1:41") send the nearest full frames (at most LOOK)."""
    s = _json(d / "study.json")
    out: dict[str, Any] = {k: s[k] for k in ("attribution", "span", "rates", "summary", "limits")}
    out["shots"] = [{**{k: sh[k] for k in ("n", "at", "duration", "holds", "moves", "longest_hold", "tone",
                                           "ink_contrast", "spoken")},
                     "palette": [f"{p['hex']} {p['share']:.0%}" for p in sh["palette"]],
                     "composition": sh["composition"]} for sh in s["shots"]]
    out["segments"] = s["segments"]
    out["transcript"] = [{"at": mmss(c["start"]), "t": c["start"], "text": c["text"]} for c in s["transcript"]]
    out["filmstrips"] = [{"covers": f"{mmss(g['from'])}-{mmss(g['to'])}", "frames": g["frames"]}
                         for g in s["files"]["strips"]]
    images = [_image(d / "motion.png", "motion energy over the span: holds shaded, cuts red, shots numbered, caption "
                                       "starts ticked under the axis")]
    for name in s["files"]["shot_sheets"][:STUDY_SHEETS]:
        images.append(_image(d / name, "shots: each one's key frame, length, tone, ink, and palette"))
    look = look.strip()
    if look and "-" in look:
        a, _, b = look.partition("-")
        lo, hi = seconds(a) or 0.0, seconds(b) or 0.0
        strips = [g for g in s["files"]["strips"] if g["to"] >= lo and g["from"] <= hi][:LOOK]
        images += [_image(d / "sheets" / g["file"], f"filmstrip {mmss(g['from'])}-{mmss(g['to'])} (each frame "
                                                     "labeled with its time; the bar under it is the motion into it)")
                   for g in strips]
    elif look:
        kept: list[dict[str, Any]] = _json(d / "frames.json")
        picked: list[dict[str, Any]] = []
        for t in look.split(",")[:LOOK]:
            want = seconds(t) or 0.0
            f = min(kept, key=lambda k: abs(k["t"] - want))
            if f not in picked:
                picked.append(f)
        images += [_image(d / "frames" / f["file"], f"frame at {f['at']} (shot {f['shot']})") for f in picked]
    out["images"] = images
    return out


def study(roots: list[Path], queue: Path | None, raw: str, start: str | float | None, end: str | float | None,
          fps: float = 6.0, look: str = "", by: str = "mcp") -> dict[str, Any]:
    """Read a study if it's done; otherwise queue it for the watcher (YouTube only, like `request`). One call does
    both, so a chat asks again with the same arguments once it's ready. A failed study is retried, with the error."""
    vid = video_id(raw)
    lo, hi, name = study_span(start, end)
    d = study_folder(roots, vid, name)
    if d is not None:
        return {"id": vid, "state": "studied", **study_read(d, look)}
    if queue is None:
        raise VideoError("the watcher isn't set up on this server")
    key = f"{vid}@{name}"
    for state in ("json", "working"):
        if (queue / f"{key}.{state}").exists():
            return {"id": vid, "span": name, "state": "waiting" if state == "json" else "working"}
    failed = queue / f"{key}.failed"
    error = failed.read_text(encoding="utf-8", errors="replace")[-600:] if failed.exists() else None
    if sum(1 for i in status_all(queue) if i["state"] in ("waiting", "working")) >= QUEUE_MAX:
        raise VideoError(f"{QUEUE_MAX} requests are already waiting; try again once they're done")
    failed.unlink(missing_ok=True)
    (queue / f"{key}.json").write_text(json.dumps({
        "id": vid, "start": lo, "end": hi, "fps": min(max(float(fps), STUDY_FPS[0]), STUDY_FPS[1]), "by": by,
        "requested_at": datetime.now(UTC).isoformat(timespec="seconds")}), encoding="utf-8")
    out: dict[str, Any] = {"id": vid, "span": name, "state": "waiting", "link": link(vid, lo),
                           "next": "A study takes a minute or two. Call again with the same video, start, and end."}
    if error:
        out["last_try_failed"] = error
    return out

"""Animated clips for /watch: Manim scenes rendered on the Obelisk (scripts/render.py), one folder each.

A clip's folder is media/render/<slug>/ under the media root: clip.mp4, stills/, render.json (its metadata), and
scene.py, the source, which the page shows beside the clip because Aman learns Manim from it too. Drafts live in
.drafts/ and are never listed. Opening a clip is counted (clip_view), so the weekly audit can see whether clips get
watched, and from where: a card's link or the page itself.

The video is the only part that's ever cleared (Aman, 2026-10-13): it goes `keep_days` after the clip was last opened
(or rendered, if never opened), 14 by default (config/clips.yaml), or whatever he set for that clip on the page,
or never. The code, stills, and metadata stay, so the page can have the render runner (scripts/render_runner.py)
render it again from the clip's own scene.py.

A chat can also submit a scene as code (`submit`, the mnemosyne_clip_submit tool): the runner renders it in its own
throwaway sandbox and publishes it here, and `report` hands the chat its stills to check (docs/security/clip-submit.md).
"""

import ast
import base64
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

from mnemosyne.ledger.db import now

FOLDER = "render"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
GALLERY = "gallery"          # scenes/gallery/: one short demo per technique, with its stills committed beside it
SCENE_BASE = re.compile(r"Scene\w*$")   # Scene, MnScene, ThreeDScene, MnScene3D, MnZoomScene, ...
VIA = ("page", "card", "session")
DEFAULT_KEEP = 14
CHOICES = (7, 14, 30, 90)


def folder(roots: list[Path], slug: str) -> Path | None:
    """A published clip's folder (its metadata, and its video or its code), or None. The slug is checked first, so
    it can't climb out of the media root."""
    if not SLUG.match(slug):
        return None
    for root in roots:
        d = root / FOLDER / slug
        if (d / "render.json").is_file() and ((d / "clip.mp4").is_file() or (d / "scene.py").is_file()):
            return d
    return None


def video(roots: list[Path], slug: str) -> Path | None:
    d = folder(roots, slug)
    return d / "clip.mp4" if d is not None and (d / "clip.mp4").is_file() else None


def default_keep(config_dir: Path) -> int:
    try:
        data: dict[str, Any] = yaml.safe_load((config_dir / "clips.yaml").read_text(encoding="utf-8")) or {}
        days = int(data["keep_days"])
    except (OSError, KeyError, TypeError, ValueError):
        return DEFAULT_KEEP
    return days if days > 0 else DEFAULT_KEEP


def _when(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _keep(con: sqlite3.Connection | None, slug: str) -> dict[str, Any]:
    row = con.execute("SELECT days, forever, cleared_at FROM clip_keep WHERE slug = ?", (slug,)).fetchone() if con \
        else None
    return {"days": row["days"], "forever": bool(row["forever"]), "cleared_at": row["cleared_at"]} if row \
        else {"days": None, "forever": False, "cleared_at": None}


def _last_opened(con: sqlite3.Connection | None, slug: str) -> str | None:
    if con is None:
        return None
    row = con.execute("SELECT MAX(viewed_at) AS at FROM clip_view WHERE slug = ?", (slug,)).fetchone()
    return row["at"] if row else None


def expiry(rendered_at: str | None, last_opened: str | None, days: int, forever: bool) -> datetime | None:
    """When the video goes: `days` after the later of rendering and the last open. None: kept for good."""
    if forever:
        return None
    start = max((t for t in (_when(rendered_at), _when(last_opened)) if t), default=None)
    return start + timedelta(days=days) if start else None


def library(roots: list[Path], con: sqlite3.Connection | None = None, queue: Path | None = None,
            default_days: int = DEFAULT_KEEP) -> list[dict[str, Any]]:
    """Every published clip, newest first: its metadata, whether its video is there, when the video would be
    cleared, and whether it's being rendered again. A folder without a readable render.json is skipped."""
    seen: dict[str, dict[str, Any]] = {}
    for root in roots:
        base = root / FOLDER
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.name in seen or not SLUG.match(d.name) or folder([root], d.name) is None:
                continue
            try:
                meta: dict[str, Any] = json.loads((d / "render.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            keep = _keep(con, d.name)
            last = _last_opened(con, d.name)
            days = keep["days"] or default_days
            has_video = (d / "clip.mp4").is_file()
            goes = expiry(meta.get("rendered_at"), last, days, keep["forever"]) if has_video else None
            seen[d.name] = {"slug": d.name, "title": meta.get("title") or d.name, "courses": meta.get("courses", []),
                            "topics": meta.get("topics", []), "watch_for": meta.get("watch_for", ""),
                            "duration": meta.get("duration"), "sections": meta.get("sections", []),
                            "rendered_at": meta.get("rendered_at", ""), "has_scene": (d / "scene.py").is_file(),
                            "has_video": has_video, "last_opened": last,
                            "keep": {"days": days, "default": keep["days"] is None, "forever": keep["forever"],
                                     "choices": list(CHOICES), "default_days": default_days},
                            "expires_at": goes.isoformat(timespec="minutes") if goes else None,
                            "cleared_at": None if has_video else keep["cleared_at"],
                            "render": render_state(queue, d.name)}
    return sorted(seen.values(), key=lambda c: str(c["rendered_at"]), reverse=True)


def record_view(con: sqlite3.Connection, slug: str, via: str) -> None:
    with con:
        con.execute("INSERT INTO clip_view (slug, via, viewed_at) VALUES (?, ?, ?)",
                    (slug, via if via in VIA else "page", now()))


def views(con: sqlite3.Connection, lo: str, hi: str) -> dict[str, Any]:
    """Opens in [lo, hi), per clip and by where they came from: the evidence for whether clips belong on cards."""
    rows = con.execute("SELECT slug, via FROM clip_view WHERE viewed_at >= ? AND viewed_at < ?", (lo, hi)).fetchall()
    by_clip: dict[str, Counter[str]] = {}
    for r in rows:
        by_clip.setdefault(str(r["slug"]), Counter())[str(r["via"])] += 1
    return {"opens": len(rows),
            "clips": [{"slug": s, "opens": sum(c.values()), **dict(c)} for s, c in
                      sorted(by_clip.items(), key=lambda kv: -sum(kv[1].values()))]}


# ---------- keeping and clearing the video ----------

def set_keep(con: sqlite3.Connection, slug: str, days: int | None, forever: bool) -> None:
    """Aman's choice for one clip: `days` (None = the default) or forever."""
    if days is not None and not 1 <= days <= 3650:
        raise ValueError("days must be between 1 and 3650")
    with con:
        con.execute("INSERT INTO clip_keep (slug, days, forever, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(slug) DO UPDATE SET days = excluded.days, forever = excluded.forever, "
                    "updated_at = excluded.updated_at", (slug, days, int(forever), now()))


def clear(con: sqlite3.Connection, roots: list[Path], slug: str) -> bool:
    """Delete the clip's video, keeping its code, stills, and metadata. False if there was no video."""
    path = video(roots, slug)
    if path is None:
        return False
    path.unlink()
    with con:
        con.execute("INSERT INTO clip_keep (slug, forever, cleared_at, updated_at) VALUES (?, 0, ?, ?) "
                    "ON CONFLICT(slug) DO UPDATE SET cleared_at = excluded.cleared_at, updated_at = excluded.updated_at",
                    (slug, now(), now()))
    return True


def expire(con: sqlite3.Connection, roots: list[Path], default_days: int,
           at: datetime | None = None) -> list[str]:
    """Clear every video whose time is up. Part of the nightly run; it touches media only, never the collection."""
    at = at or datetime.now(UTC)
    gone: list[str] = []
    for c in library(roots, con, None, default_days):
        t = _when(c["expires_at"])
        if c["has_video"] and t is not None and t <= at and clear(con, roots, c["slug"]):
            gone.append(c["slug"])
    return gone


# ---------- rendering again: the render runner's queue (scripts/render_runner.py) ----------

def render_state(queue: Path | None, slug: str) -> str | None:
    if queue is None:
        return None
    for suffix, state in ((".working", "rendering"), (".json", "waiting"), (".failed", "failed")):
        if (queue / f"{slug}{suffix}").exists():
            return state
    return None


def request_render(roots: list[Path], queue: Path | None, slug: str) -> str:
    """Queue the clip to be rendered again from its own scene.py. Only a slug goes in the request, never code."""
    d = folder(roots, slug)
    if d is None or not (d / "scene.py").is_file():
        raise LookupError("no code to render that clip from")
    if queue is None:
        raise RuntimeError("the renderer isn't set up on this server")
    state = render_state(queue, slug)
    if state in ("waiting", "rendering"):
        return state
    (queue / f"{slug}.failed").unlink(missing_ok=True)
    (queue / f"{slug}.json").write_text(json.dumps({"slug": slug, "requested_at": now()}), encoding="utf-8")
    return "waiting"


# ---------- clips a chat submits as code (mnemosyne_clip_submit, Aman 2026-10-13) ----------

MAX_CODE = 200_000         # bytes of scene code
PER_DAY = 20               # submissions in any 24 hours
QUEUE_MAX = 5              # renders waiting at once
COURSE = re.compile(r"^[a-z]{2,6}\d{3}$")


class SubmitError(ValueError):
    pass


def scene_class(code: str) -> str:
    """The scene to render: CLIP['scene'] if the code names one, else its first class based on a *Scene."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SubmitError(f"the code doesn't parse: line {e.lineno}: {e.msg}") from None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CLIP" for t in node.targets):
            try:
                clip: Any = ast.literal_eval(node.value)
            except ValueError:
                break
            fields = cast(dict[str, Any], clip) if isinstance(clip, dict) else {}
            named = fields.get("scene")
            if isinstance(named, str) and named.isidentifier():
                return named
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
                (isinstance(b, ast.Name) and SCENE_BASE.search(b.id))
                or (isinstance(b, ast.Attribute) and SCENE_BASE.search(b.attr)) for b in node.bases):
            return node.name
    raise SubmitError("no Scene class in the code (a class based on Scene, MnScene, ThreeDScene, ...)")


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-")
    return s or "clip"


def submit(con: sqlite3.Connection, media: Path, queue: Path | None, code: str, title: str, courses: list[str],
           topics: list[str], watch_for: str, replace: str = "", by: str = "mcp") -> dict[str, Any]:
    """A chat's scene, queued for the render runner (scripts/render_runner.py), which renders it in its own sandbox
    and publishes it to /watch. `replace` re-submits a clip a chat made before (to fix what its stills showed)."""
    if queue is None:
        raise SubmitError("the render runner isn't set up on this server")
    if len(code.encode("utf-8")) > MAX_CODE:
        raise SubmitError(f"the code is over {MAX_CODE // 1000} KB")
    cls = scene_class(code)
    title = " ".join(title.split())
    if not 3 <= len(title) <= 120:
        raise SubmitError("give a title of 3 to 120 characters")
    courses = [c.lower() for c in courses]
    if len(courses) > 4 or any(not COURSE.match(c) for c in courses):
        raise SubmitError("courses are codes like phys207 or math309 (at most 4)")
    topics = [t for t in (str(t).strip()[:80] for t in topics) if t][:6]
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    recent = int(con.execute("SELECT COUNT(*) FROM clip_submission WHERE submitted_at >= ?", (since,)).fetchone()[0])
    if recent >= PER_DAY:
        raise SubmitError(f"{PER_DAY} clips were submitted in the last 24 hours; that's the limit")
    if len(list(queue.glob("*.json"))) + len(list(queue.glob("*.working"))) >= QUEUE_MAX:
        raise SubmitError(f"{QUEUE_MAX} renders are already waiting; check back in a few minutes")
    if replace:
        slug = replace
        old = media / FOLDER / slug / "render.json"
        pending = media / FOLDER / ".submitted" / slug / "submission.json"
        made_by_chat = SLUG.match(slug) is not None and (
            pending.is_file() or (old.is_file() and json.loads(old.read_text(encoding="utf-8")).get("origin") == "chat"))
        if not made_by_chat:
            raise SubmitError("replace only names a clip a chat submitted (its slug from mnemosyne_clip_submit)")
        if render_state(queue, slug) in ("waiting", "rendering"):
            raise SubmitError("that clip is already rendering; check its status first")
    else:
        base = slugify(title)
        slug, n = base, 2
        while (media / FOLDER / slug).exists() or (media / FOLDER / ".submitted" / slug).exists() \
                or (queue / f"{slug}.json").exists():
            slug, n = f"{base}-{n}", n + 1
    sub = media / FOLDER / ".submitted" / slug
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "scene.py").write_text(code, encoding="utf-8")
    (sub / "submission.json").write_text(json.dumps({
        "title": title, "courses": courses, "topics": topics, "watch_for": watch_for.strip()[:600], "scene": cls,
        "file": "scene.py", "origin": "chat", "by": by, "submitted_at": now()}, indent=2), encoding="utf-8")
    (queue / f"{slug}.failed").unlink(missing_ok=True)
    (queue / f"{slug}.json").write_text(json.dumps({"slug": slug, "requested_at": now()}), encoding="utf-8")
    raw = code.encode("utf-8")
    with con:
        con.execute("INSERT INTO clip_submission (slug, by, code_sha, bytes, submitted_at) VALUES (?, ?, ?, ?, ?)",
                    (slug, by, hashlib.sha256(raw).hexdigest(), len(raw), now()))
    return {"slug": slug, "scene": cls, "state": "waiting",
            "next": "Rendering takes about as long as the clip runs. Check with mnemosyne_clip_status."}


def report(roots: list[Path], queue: Path | None, slug: str, page: str | None = None,
           stills: int = 8, scenes: Path | None = None) -> dict[str, Any]:
    """Where a clip stands, for the chat that made it: waiting, rendering, failed (with the error, to fix the code),
    or published (with the /watch link, layout warnings, and up to `stills` section stills as images to check).
    A `gallery-<name>` slug is a gallery entry instead: its code and stills (`gallery_entry`)."""
    if not SLUG.match(slug):
        raise LookupError("no such clip")
    if slug.startswith(GALLERY + "-") and scenes is not None:
        return gallery_entry(scenes, slug, stills)
    state = render_state(queue, slug)
    out: dict[str, Any] = {"slug": slug, "state": state or "unknown"}
    if state == "failed" and queue is not None:
        out["error"] = (queue / f"{slug}.failed").read_text(encoding="utf-8", errors="replace")[-6000:]
        out["next"] = "Fix the code from the error and submit it again with replace=<slug>."
        return out
    if state in ("waiting", "rendering"):
        out["next"] = "Check again in a minute: a clip renders in about as long as it runs."
        return out
    d = folder(roots, slug)
    if d is None:
        raise LookupError("no such clip")
    meta: dict[str, Any] = json.loads((d / "render.json").read_text(encoding="utf-8"))
    sections: list[dict[str, Any]] = meta.get("sections", [])
    out.update({"state": "published", "title": meta.get("title"), "duration": meta.get("duration"),
                "has_video": (d / "clip.mp4").is_file(), "sections": [s.get("name") for s in sections],
                "layout_warnings": meta.get("layout_warnings", []), "made_by_chat": meta.get("origin") == "chat",
                "watch": f"{page.rstrip('/')}/watch#{slug}" if page else f"/watch#{slug}"})
    images: list[dict[str, str]] = []
    for s in sections[:stills]:
        jpg = d / "stills" / Path(str(s.get("still", ""))).with_suffix(".jpg").name
        if jpg.is_file():
            images.append({"label": f"still: end of section '{s.get('name')}' ({float(s.get('start', 0)):.0f} s)",
                           "jpeg_base64": base64.b64encode(jpg.read_bytes()).decode()})
    out["images"] = images
    if len(sections) > stills:
        out["note"] = f"{len(sections)} sections; sent the first {stills} stills"
    return out


# ---------- the toolkit a chat writes scenes with: read live, so it follows Aman's style as it evolves ----------

def _api(path: Path, module: str) -> list[dict[str, str]]:
    """Public functions and classes in one source file: name, signature, the docstring's first paragraph."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[dict[str, str]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef) and not node.name.startswith("_"):
            if isinstance(node, ast.FunctionDef):
                sig = f"{node.name}({ast.unparse(node.args)})"
            else:
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                sig = f"class {node.name}({bases})"
            doc = (ast.get_docstring(node) or "").split("\n\n")[0].replace("\n", " ").strip()
            out.append({"module": module, "signature": sig, "doc": doc[:400]})
    return out


def _git_head(repo: Path) -> str | None:
    """The checked-out commit, read from .git without running git (the api container has none)."""
    try:
        head = (repo / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head[:10]
        ref = head[5:]
        loose = repo / ".git" / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()[:10]
        for line in (repo / ".git" / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split()[0][:10]
    except OSError:
        return None
    return None


def harvest(roots: list[Path], scenes: Path | None) -> list[dict[str, str]]:
    """Helpers in scenes marked `# harvest: <what>`: candidates to move into amanim, so the next scene borrows
    them instead of rewriting them."""
    files = [d / "scene.py" for root in roots if (root / FOLDER).is_dir()
             for d in sorted((root / FOLDER).iterdir()) if SLUG.match(d.name) and (d / "scene.py").is_file()]
    if scenes is not None and scenes.is_dir():
        files += sorted(p for p in scenes.rglob("*.py") if p.name not in ("mn_style.py", "brand.py"))
    found: list[dict[str, str]] = []
    for f in files:
        lines = f.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = re.search(r"#\s*harvest:\s*(.+)$", line)
            if m:
                nxt = next((ln.strip() for ln in lines[i + 1:i + 4] if ln.strip().startswith(("def ", "class "))), "")
                where = f.parent.name if f.name == "scene.py" else f.relative_to(scenes).as_posix() if scenes else f.name
                found.append({"in": where, "what": m[1].strip(), "code": nxt[:160]})
    return found


def _clip_block(path: Path) -> dict[str, Any]:
    """A scene file's module-level CLIP = {...} literal, or {} (as job.py reads it)."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CLIP" for t in node.targets):
            try:
                value: Any = ast.literal_eval(node.value)
            except ValueError:
                return {}
            return cast(dict[str, Any], value) if isinstance(value, dict) else {}
    return {}


def gallery(scenes: Path | None) -> list[dict[str, Any]]:
    """scenes/gallery/: one short demo scene per technique, the reference a new scene pattern-matches against. Each
    entry names its technique; `mnemosyne_clip_status("gallery-<name>")` returns its code and stills."""
    folder = scenes / GALLERY if scenes is not None else None
    if folder is None or not folder.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(folder.glob("*.py")):
        meta = _clip_block(f)
        stills = sorted((folder / "stills" / f.stem).glob("*.jpg"))
        out.append({"name": f"{GALLERY}-{f.stem.replace('_', '-')}", "title": meta.get("title", f.stem),
                    "technique": meta.get("technique", ""), "stills": len(stills)})
    return out


def gallery_entry(scenes: Path, slug: str, stills: int = 8) -> dict[str, Any]:
    """One gallery entry for a chat: its code (to borrow from) and its stills as images (to see the result)."""
    stem = slug.removeprefix(GALLERY + "-").replace("-", "_")
    f = scenes / GALLERY / f"{stem}.py"
    if not f.is_file():
        raise LookupError("no such gallery entry")
    meta = _clip_block(f)
    jpgs = sorted((scenes / GALLERY / "stills" / stem).glob("*.jpg"))
    return {"slug": slug, "state": "gallery", "title": meta.get("title", stem), "technique": meta.get("technique"),
            "watch_for": meta.get("watch_for"), "code": f.read_text(encoding="utf-8"),
            "next": "Borrow the pattern, not the scene: compute your own numbers and views from your own inputs.",
            "images": [{"label": f"still: end of section '{j.stem[3:]}'", "jpeg_base64": base64.b64encode(
                j.read_bytes()).decode()} for j in jpgs[:stills]]}


def toolkit(scenes: Path | None, amanim: Path | None, roots: list[Path]) -> dict[str, Any]:
    """Everything a chat needs to write a scene in Aman's style, read from the live copies on the Obelisk: amanim's
    API (and its commit), mn_style's helpers and palette roles, and the harvest candidates."""
    out: dict[str, Any] = {"render": "ManimCE 0.21.0, Python 3.14, numpy, scipy, matplotlib; no network; 720p30",
                           "imports": ["from mn_style import *   # all of manim, plus the house style",
                                       "from amanim import ...    # Aman's technique library (below)"]}
    if amanim is not None and (amanim / "amanim" / "__init__.py").is_file():
        pkg = amanim / "amanim"
        version = re.search(r'__version__\s*=\s*"([^"]+)"', (pkg / "__init__.py").read_text(encoding="utf-8"))
        out["amanim"] = {"version": version[1] if version else None, "commit": _git_head(amanim),
                         "api": [a for f in sorted(pkg.glob("*.py")) if f.name != "__init__.py"
                                 for a in _api(f, f"amanim.{f.stem}")]}
    else:
        out["amanim"] = None
    if scenes is not None and (scenes / "mn_style.py").is_file():
        out["mn_style"] = _api(scenes / "mn_style.py", "mn_style")
        roles = ["GROUND", "INK", "MUTED", "ACCENT", "FLOW", "WARM", "STRUCT", "GRID", "WARN", "GOOD", "BAD"]
        out["palette"] = {"roles": roles, "branded": (scenes / "brand.py").is_file(),
                          "note": "Use the role names, never hex values: the colors are Aman's design tokens. "
                                  "See them first: mnemosyne_clip_status('gallery-palette') shows each role as it "
                                  "renders, and which ones look alike."}
    out["gallery"] = gallery(scenes)
    if out["gallery"]:
        out["gallery_note"] = ("One short demo per technique. Before writing a scene like one of these, read it with "
                               "mnemosyne_clip_status('<name>'): its code and stills.")
    out["harvest"] = harvest(roots, scenes)
    return out

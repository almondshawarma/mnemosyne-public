"""Mnemosyne (Anki cards) domain for the Helios MCP layer. Pure stdlib; talks to the mnemosyne API.

The cloud-facing `mcp` service gets only the propose token, so it can add drafts but never approve
them (mnemosyne invariant 7). `mcp-local` also gets the approve token.
"""
import json, os, urllib.error, urllib.parse, urllib.request

URL = os.environ.get("MNEMOSYNE_URL", "http://mnemosyne:8110").rstrip("/")
PROPOSE = os.environ.get("MNEMOSYNE_PROPOSE_TOKEN", "").strip()
APPROVE = os.environ.get("MNEMOSYNE_APPROVE_TOKEN", "").strip()


def _call(method, path, token, body=None):
    if not token:
        return {"error": "mnemosyne is not configured on this MCP instance"}
    req = urllib.request.Request(URL + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace")[:2000]}
    except OSError as e:
        return {"error": f"mnemosyne unreachable: {e}"}


def can_approve():
    return bool(APPROVE)


def propose(payload):
    """payload = the mnemosyne-cards JSON ({"source": {...}, "cards": [...]}) from the skill."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return {"error": "payload must be the mnemosyne-cards JSON object"}
    return _call("POST", "/inbox", PROPOSE or APPROVE, {"payload": payload, "origin": "mcp"})


def pending():
    return _call("GET", "/batches?status=pending", APPROVE)


def approve(batch_id, approve=True):
    return _call("POST", f"/batches/{batch_id}/decision", APPROVE,
                 {"action": "approve" if approve else "reject", "by": "mcp-local"})


# ---- the learner model (P4): read-only, so every instance may use it with the propose token ----

def _read(path):
    return _call("GET", path, PROPOSE or APPROVE)


def learner_profile(fresh=False):
    return _read("/learner/profile" + ("?fresh=true" if fresh else ""))


def search_cards(query, limit=20):
    return _read("/learner/search?" + urllib.parse.urlencode({"q": query, "limit": int(limit)}))


def exam_status(days=60):
    return {"exams": _read(f"/learner/exams?days={int(days)}")}


def card_stats(days=30):
    return _read(f"/learner/stats?days={int(days)}")


def log_question(asked, course=None, lecture=None, kind="clarify", about=None, answer=None, sharper=None,
                 beyond_course=False):
    """Keep a question Aman asked in a session. Like proposing a card, it only adds (the propose token is enough)."""
    body = {"asked": asked, "course": course, "lecture": lecture, "kind": kind, "about": about, "answer": answer,
            "sharper": sharper, "beyond_course": bool(beyond_course)}
    return _call("POST", "/questions", PROPOSE or APPROVE, {k: v for k, v in body.items() if v is not None})


# ---- Aman's books: read-only (quote, cite, practice problems), so every instance may use them ----

def books(book=""):
    return _read("/books" + ("?" + urllib.parse.urlencode({"book": book}) if book else ""))


def book_search(query, book="", limit=10):
    params = {"q": query, "limit": int(limit)}
    if book:
        params["book"] = book
    return _read("/books/search?" + urllib.parse.urlencode(params))


def book_read(book, where, images="auto"):
    return _read(f"/books/{urllib.parse.quote(book)}/read?" + urllib.parse.urlencode({"where": where, "images": images}))


def book_problems(book, where, difficulty=0, limit=8, offset=0, images="auto"):
    q = {"where": where, "difficulty": int(difficulty), "limit": int(limit), "offset": int(offset), "images": images}
    return _read(f"/books/{urllib.parse.quote(book)}/problems?" + urllib.parse.urlencode(q))


def with_images(result):
    """A read or problems result as MCP content: the JSON, then each page image as an image (not base64 text)."""
    import base64
    from mcp.server.fastmcp import Image

    images = result.pop("images", []) if isinstance(result, dict) else []
    out = [result]
    for im in images:
        out.append(im.get("label") or f"page image: PDF page {im['pdf_page']}")
        out.append(Image(data=base64.b64decode(im["jpeg_base64"]), format=im.get("format") or "jpeg"))
    return out


def log_attempt(mistake_id, right, hints=False, problem=None, note=None):
    """A fresh problem Aman tried on one of his graded mistakes. Only adds (the propose token is enough); marking the
    mistake fixed stays his tick in the exam's stub."""
    body = {"right": bool(right), "hints": bool(hints), "problem": problem, "note": note}
    return _call("POST", f"/mistakes/{int(mistake_id)}/attempts", PROPOSE or APPROVE,
                 {k: v for k, v in body.items() if v is not None})


# ---- exam prep (the mnemosyne-exam skill): the pack and documents read, probes only add ----

def exam_pack(course, exam=""):
    return _read("/exams/pack?" + urllib.parse.urlencode({"course": course, "exam": exam}))


def doc_read(doc_id, pages="", images=True):
    q = urllib.parse.urlencode({"pages": pages, "images": "true" if images else "false"})
    return _read(f"/documents/{int(doc_id)}/read?{q}")


def log_probe(course, hook, question, right, hints=False, note_id=None, lecture=None, topic=None, exam=None,
              note=None):
    body = {"course": course, "hook": hook, "question": question, "right": bool(right), "hints": bool(hints),
            "note_id": note_id, "lecture": lecture, "topic": topic, "exam": exam, "note": note}
    return _call("POST", "/probes", PROPOSE or APPROVE, {k: v for k, v in body.items() if v is not None})


# ---- stuck documents (2026-10-07): when the TAMU allowance runs out, a session cards a lecture instead ----

def stuck_documents():
    return _read("/pipeline/stuck")


def settle_document(doc_id):
    return _call("POST", f"/pipeline/document/{int(doc_id)}/settle", PROPOSE or APPROVE, {})


# ---- the weekly audit (2026-10-07): the mnemosyne-weekly skill's evidence ----

def weekly_audit(last="", days=7):
    q = urllib.parse.urlencode({"last": last, "days": int(days)})
    return _read(f"/weekly?{q}")


# ---- lecture videos (2026-10-13): what the watcher kept (captions, key frames, contact sheets), never the video ----

def videos():
    return _read("/videos")


def video_search(query, video="", limit=10):
    return _read("/videos/search?" + urllib.parse.urlencode({"q": query, "video": video, "limit": int(limit)}))


def video_watch(video, start="", end="", images="auto"):
    q = urllib.parse.urlencode({"start": start, "end": end, "images": images})
    return _read(f"/videos/{urllib.parse.quote(video)}/watch?{q}")


def video_frames(video, at):
    return _read(f"/videos/{urllib.parse.quote(video)}/frames?" + urllib.parse.urlencode({"at": at}))


def video_request(video):
    """Queue a YouTube video for the watcher. Only adds a request, so the propose token is enough."""
    return _call("POST", "/videos/watch", PROPOSE or APPROVE, {"video": video})


def video_study(video, start, end, fps=6, look=""):
    """A study of one span (2026-10-24): read it if it's done, else queue it. Only adds a request, like video_request."""
    return _call("POST", "/videos/study", PROPOSE or APPROVE,
                 {"video": video, "start": str(start), "end": str(end), "fps": float(fps), "look": look})


# ---- animated clips (2026-10-13): a chat's Manim scene, rendered in a sandbox on the Obelisk and shown on /watch ----

def clips():
    return _read("/clips")


def clip_submit(code, title, courses="", topics="", watch_for="", replace=""):
    """Only the scene's text goes up; the Obelisk renders it in a throwaway sandbox. Adds a render, so the propose
    token is enough (docs/security/clip-submit.md in mnemosyne)."""
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()] if isinstance(s, str) else list(s or [])  # noqa: E731
    body = {"code": code, "title": title, "courses": split(courses), "topics": split(topics), "watch_for": watch_for,
            "replace": replace}
    return _call("POST", "/clips", PROPOSE or APPROVE, body)


def clip_status(clip):
    return _read(f"/clips/{urllib.parse.quote(clip)}")


def clip_toolkit():
    return _read("/clips/toolkit")


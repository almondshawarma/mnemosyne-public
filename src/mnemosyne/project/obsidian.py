"""Aman's questions from a lecture, and the debrief of a graded exam or homework, kept in that document's Paperless
stub in the vault (Sources/Paperless/).

The homelab's stub ingester rewrites each stub's top every morning and keeps everything from its
`<!-- HELIOS:below-is-yours ... -->` marker on verbatim. mnemosyne writes only one block there, between its own
markers, just under that line: the lecture's questions as tasks Aman can tick, or a graded document's debrief, its misses
as tasks he ticks once he has them (graded.py). So there's one note per lecture, not a
second "· questions" file. A stub that doesn't exist yet (the ingester runs at 05:15 UTC) gets the block on the next
poll after it appears. Until then the questions are on the review page.

Daily Notes are private to AI, so nothing writes there. Stubs carry `day: "[[Sep 28, 2026]]"`, which puts them in
that daily note's backlinks. For a list of every open question, one Dataview query does it
(docs/obsidian-questions.md). A question ticked in the stub is read back as answered before the block is rewritten.
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

from mnemosyne import graded, papers
from mnemosyne.questions import KIND_LABEL, for_doc, set_status
from mnemosyne.questions import move_doc as move_doc_questions

START, END = "<!-- mnemosyne:start -->", "<!-- mnemosyne:end -->"
HELIOS = "<!-- HELIOS:below-is-yours"          # the stub ingester's marker: everything from here on is kept
TICK = re.compile(r"^\s*- \[[xX]\] .*\^q(\d+)\s*$", re.MULTILINE)
PAPERLESS_ID = re.compile(r"^paperless_id:\s*(\d+)", re.MULTILINE)
BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END) + r"\n?", re.DOTALL)


def ticked(text: str) -> set[int]:
    return {int(m) for m in TICK.findall(text)}


def one_line(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def block(qs: list[dict[str, Any]], debrief: list[str] | None = None, what: str = "lecture") -> str:
    lines = [START, *(debrief or [])]
    if qs:
        lines += ([""] if debrief else []) + [f"## Questions from this {what}", ""]
    for q in qs:
        done = q["status"] in ("answered", "carded", "dropped")
        page = f" (p. {q['page']})" if q.get("page") else ""
        lines.append(f"- [{'x' if done else ' '}] ❓ **{one_line(q['asked'])}**{page} ^q{q['id']}")
        if q.get("answer"):
            lines.append(f"    - Answer: {one_line(q['answer'])}")
        if q.get("sharper"):
            lines.append(f"    - Asked sharper: *{one_line(q['sharper'])}*")
        tail = [KIND_LABEL.get(str(q["kind"]), str(q["kind"]))]
        if q["status"] in ("carded", "dropped"):
            tail.append(str(q["status"]))
        if q.get("hyperion_id"):
            tail.append(f"Hyperion: {q['hyperion_id']}")
        lines.append(f"    - _{' · '.join(tail)}_")
    lines.append(END)
    return "\n".join(lines) + "\n"


def place(text: str, new: str) -> str:
    """The stub with mnemosyne's block put in (or replaced, or taken out when `new` is empty): just under the
    ingester's marker line, so it survives the next regeneration. Nothing else in the stub changes."""
    if START in text and END in text:
        return BLOCK.sub(lambda _: new, text, count=1)
    if not new:
        return text
    i = text.find(HELIOS)
    if i == -1:
        return text.rstrip("\n") + "\n\n" + new
    eol = text.find("\n", i)
    if eol == -1:
        return text + "\n\n" + new
    return text[:eol + 1] + "\n" + new + text[eol + 1:]


def stubs(root: Path) -> dict[int, Path]:
    """paperless_id → stub, read from each stub's frontmatter (filenames follow titles, ids don't change)."""
    out: dict[int, Path] = {}
    for path in root.glob("*.md"):
        if ".sync-conflict-" in path.name:
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                head = f.read(800)
        except OSError:
            continue
        if m := PAPERLESS_ID.search(head):
            out[int(m[1])] = path
    return out


def sync_doc(con: sqlite3.Connection, root: Path, doc_id: int, index: dict[int, Path] | None = None) -> str | None:
    """Read back ticks, then rewrite the document's block in its stub. Returns the stub's name, or None when the
    document has no questions or debrief, or no stub yet."""
    qs = for_doc(con, doc_id)
    path = (index if index is not None else stubs(root)).get(doc_id)
    if path is None or not (qs or graded.work_for(con, doc_id) or papers.get(con, doc_id)):
        return None
    existing = path.read_text(encoding="utf-8")
    done = ticked(existing)
    for q in qs:
        if q["id"] in done and q["status"] == "open":
            set_status(con, int(q["id"]), "answered", "ticked in Obsidian")
    graded.read_ticks(con, existing)
    paper = papers.get(con, doc_id) is not None
    text = place(existing, block(for_doc(con, doc_id), graded.stub_lines(con, doc_id) + papers.stub_lines(con, doc_id),
                                 "paper" if paper else "lecture"))
    if text != existing:
        path.write_text(text, encoding="utf-8")
    return path.name


def move_doc(con: sqlite3.Connection, root: Path, old: int, new: int, title: str) -> str | None:
    """A lecture's new copy replaced the old one: read back the old stub's ticks, take the block out of it, and move
    the questions. The new copy's stub gets the block once the ingester has made it."""
    index = stubs(root)
    sync_doc(con, root, old, index)
    if (path := index.get(old)) is not None:
        text = path.read_text(encoding="utf-8")
        if (cleaned := place(text, "")) != text:
            path.write_text(cleaned, encoding="utf-8")
    move_doc_questions(con, old, new, title)
    return sync_doc(con, root, new, index)


def retire_question_notes(con: sqlite3.Connection, old_dir: Path, root: Path) -> list[str]:
    """One-time move (2026-10-05) from the separate '… · questions' notes in Sources/Lectures/ into the stubs. Ticks
    are read back first, and anything Aman wrote outside the old note's block goes into the stub too. An old note is
    removed only once its lecture's stub holds the questions."""
    index = stubs(root)
    moved: list[str] = []
    for old in sorted(old_dir.glob("* · questions.md")):
        text = old.read_text(encoding="utf-8")
        ids = [int(i) for i in re.findall(r"\^q(\d+)", text)]
        row = con.execute(f"SELECT DISTINCT doc_id FROM question WHERE id IN ({','.join('?' * len(ids))})",
                          ids).fetchone() if ids else None
        if row is None or (stub := index.get(int(row[0]))) is None:
            continue
        for qid in ticked(text):
            con.execute("UPDATE question SET status = 'answered', note = 'ticked in Obsidian' WHERE id = ? AND status = 'open'",
                        (qid,))
        mine = text.split(END, 1)[1] if END in text else ""
        mine = re.sub(r"^\s*## My notes\s*", "", mine).strip()
        if mine and mine != "-":
            body = stub.read_text(encoding="utf-8").rstrip("\n")
            stub.write_text(f"{body}\n\n## From the old questions note\n{mine}\n", encoding="utf-8")
        if sync_doc(con, root, int(row[0]), index) is not None:
            old.unlink()
            moved.append(old.name)
    return moved


def sync_all(con: sqlite3.Connection, root: Path) -> int:
    """Every lecture with questions and every graded document (a handful a week): cheap enough for each poll."""
    docs = [int(r[0]) for r in con.execute("SELECT DISTINCT doc_id FROM question WHERE doc_id IS NOT NULL"
                                           " UNION SELECT doc_id FROM graded_work")]
    index = stubs(root)
    return sum(1 for d in docs if sync_doc(con, root, d, index))

"""Adds the exam prep tools to every Helios MCP instance (the mnemosyne-exam skill, 2026-10-07): the exam pack and
course documents read; probes only add. The propose token is enough for all three.
Additive and idempotent like the other patches: backs up to *.bak-mnemosyne-exam, refuses to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    @mcp.tool()
    def mnemosyne_exam_pack(course: str, exam: str = "") -> dict:
        """Where exam prep starts (the mnemosyne-exam skill). `course` like "PHYS 207"; `exam` like "Exam II", or
        empty for the course's next exam. Returns the exam and its lectures, the course's exam rules (own formula
        sheet or provided), each lecture's Paperless documents and card counts (strong = hook candidates, weak),
        the prep material filed for it (review sheets, practice exams, keys) with each problem's lectures, topics,
        what Aman did on it, and its card coverage, plus his open mistakes and recent probes."""
        return mnemo.exam_pack(course, exam)

    @mcp.tool()
    def mnemosyne_doc_read(doc_id: int, pages: str = "", images: bool = True):
        """Read a course document from Paperless (a lecture deck, review sheet, practice exam, key, or graded work)
        by its id from mnemosyne_exam_pack: up to 12 pages a call ("1-12", then what `more` says), as text plus page
        images, since his handwriting and the figures are on the images. Only course documents."""
        return mnemo.with_images(mnemo.doc_read(doc_id, pages, images))

    @mcp.tool()
    def mnemosyne_log_probe(course: str, hook: str, question: str, right: bool, hints: bool = False,
                            note_id: int = 0, lecture: int = 0, topic: str = "", exam: str = "", note: str = "") -> dict:
        """Keep one probe: a step up from a card Aman holds (`hook`: its prompt, or the idea; `note_id` when it's
        one card), the `question` that asked for the step, and whether he took it (`right`, `hints`). Log the misses
        too: a miss is a card he can recall but not use. Only adds."""
        return mnemo.log_probe(course, hook, question, right, hints, note_id or None, lecture or None, topic or None,
                               exam or None, note or None)

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_exam_pack" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-exam"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-exam")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+3 mnemosyne exam tools) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

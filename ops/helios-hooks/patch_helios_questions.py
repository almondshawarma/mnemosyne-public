"""Adds mnemosyne_log_question to every Helios MCP instance: a tutoring session keeps the questions Aman asks.
Additive and idempotent like the other patches: backs up to *.bak-mnemosyne-questions, refuses to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOL = '''    @mcp.tool()
    def mnemosyne_log_question(asked: str, course: str = "", lecture: int = 0, kind: str = "clarify",
                               about: str = "", answer: str = "", sharper: str = "",
                               beyond_course: bool = False) -> dict:
        """Keep a question Aman asked, so it isn't lost: it joins his question list (review page, the lecture's
        Obsidian note, the Sunday report). kind = clarify|mechanism|predict|connect|apply|verify. Give `answer`
        (short) and `sharper` (the same curiosity asked more precisely) when you have them. Only adds; writes
        nothing to Anki."""
        return mnemo.log_question(asked, course or None, lecture or None, kind, about or None, answer or None,
                                  sharper or None, beyond_course)

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_log_question" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOL + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-questions"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-questions")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+mnemosyne_log_question) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

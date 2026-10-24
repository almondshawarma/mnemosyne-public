"""Adds mnemosyne_log_attempt to every Helios MCP instance: each fresh problem the tutor sets on a graded mistake is
kept, right or wrong. Additive and idempotent like the other patches: backs up to *.bak-mnemosyne-attempts, refuses
to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOL = '''    @mcp.tool()
    def mnemosyne_log_attempt(mistake_id: int, right: bool, hints: bool = False, problem: str = "",
                              note: str = "") -> dict:
        """Keep a fresh problem Aman tried on one of his graded mistakes (the profile's graded.open_mistakes, by id):
        right = he reached the right answer, hints = he needed one. `problem` in a sentence, `note` what went wrong
        or what he did differently. Log misses too: they're how the profile tells "never tried" from "still
        falling for it". Only adds; marking the mistake fixed stays his tick in the exam's stub."""
        return mnemo.log_attempt(mistake_id, right, hints, problem or None, note or None)

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_log_attempt" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOL + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-attempts"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-attempts")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+mnemosyne_log_attempt) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

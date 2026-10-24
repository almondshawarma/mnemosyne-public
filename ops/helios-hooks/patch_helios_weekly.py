"""Adds mnemosyne_weekly_audit to every Helios MCP instance (2026-10-07): the evidence for the weekly audit (the
mnemosyne-weekly skill), where a Claude session on Aman's own account reads the week's cards, his decisions, how new
cards hold, and the gaps. Read-only, so the propose token is enough.
Additive and idempotent like the other patches: backs up to *.bak-mnemosyne-weekly, refuses to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    @mcp.tool()
    def mnemosyne_weekly_audit(last: str = "", days: int = 7) -> dict:
        """One week of mnemosyne for the weekly audit: cards made per source and what Aman did with them (his edits
        and rejections in his words), how cards first studied this week are holding (by course and note type), leech
        diagnoses, what generation skipped, his questions, mistakes, probes, exams with uncarded lectures, jobs not
        done, and load against the budget. `last` = the window's last day, YYYY-MM-DD (default yesterday)."""
        return mnemo.weekly_audit(last, days)

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_weekly_audit" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-weekly"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-weekly")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+mnemosyne_weekly_audit) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

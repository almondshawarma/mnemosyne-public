"""P4 addition to the Helios MCP server: the read-only learner tools the mnemosyne-tutor skill uses.

Additive, like patch_helios.py: backs up server.py to server.py.bak-mnemosyne-p4 first, aborts unless the anchor is
found exactly once, and refuses to run twice. Copies the updated mnemo.py next to it. Rebuild mcp and mcp-local after.
"""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"

ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    # ---- mnemosyne learner model (P4): read-only, on every instance ----
    @mcp.tool()
    def mnemosyne_learner_profile(fresh: bool = False) -> dict:
        """What Aman knows and where he struggles, from his Anki collection: per-course memory (cards seen, strong,
        weak), cards he keeps failing, cards he missed this week, upcoming exams with readiness and their weakest
        cards, study load against his budget, and his own recent reasons for rejecting cards. Call it once at the
        start of a study or tutoring conversation (the mnemosyne-tutor skill). Rebuilt daily at 04:15; fresh=true
        rebuilds now (~20 s)."""
        return mnemo.learner_profile(fresh)

    @mcp.tool()
    def mnemosyne_search_cards(query: str, limit: int = 20) -> dict:
        """Aman's Anki cards on a topic, each with how well he holds it: retrievability (FSRS chance he'd recall it
        now), stability, lapses, last review. `query` is plain words ("debye length") or Anki search syntax
        ("tag:course::nuen201::l07*", "prop:r<0.8"). Check this before explaining a topic or writing cards."""
        return mnemo.search_cards(query, limit)

    @mcp.tool()
    def mnemosyne_exam_status(days: int = 60) -> dict:
        """Aman's exams in the next `days`: mapped lectures, lectures without cards, readiness (share of the exam's
        cards held at R >= 0.9), unseen cards, and the cards he's most likely to miss."""
        return mnemo.exam_status(days)

    @mcp.tool()
    def mnemosyne_card_stats(days: int = 30) -> dict:
        """Study load against the budget, daily metrics (retention, fluency index, backlog), card flow through
        mnemosyne, and the leech doctor's cases."""
        return mnemo.card_stats(days)

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_learner_profile" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-p4"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-p4")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+4 read-only tools) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

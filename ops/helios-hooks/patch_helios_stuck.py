"""Adds mnemosyne_stuck_documents and mnemosyne_settle_document to every Helios MCP instance (2026-10-07): when the
TAMU gateway's daily allowance runs out, a Claude session on Aman's own account cards a waiting lecture instead.
Settling only finishes a lecture the session proposed cards for, so the propose token is enough.
Additive and idempotent like the other patches: backs up to *.bak-mnemosyne-stuck, refuses to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    @mcp.tool()
    def mnemosyne_stuck_documents() -> dict:
        """Documents the mnemosyne pipeline hasn't finished: each with its Paperless id, title, job (paperless =
        a lecture), tries, and waiting_on_gateway (the TAMU daily allowance ran out; it retries by itself after
        midnight UTC). A lecture can be carded in this session instead: see the mnemosyne-cards skill, stuck.md."""
        return mnemo.stuck_documents()

    @mcp.tool()
    def mnemosyne_settle_document(doc_id: int) -> dict:
        """After proposing a stuck lecture's cards in this session (source.link = its Paperless link): records its
        slides so a re-send cards only what's new, and marks it done so the pipeline doesn't card it again.
        Refused unless cards for it were proposed."""
        return mnemo.settle_document(doc_id)

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_stuck_documents" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-stuck"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-stuck")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+2 stuck-document tools) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

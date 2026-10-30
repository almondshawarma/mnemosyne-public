"""Adds mnemosyne_neighbors to every Helios MCP instance (2026-10-29, docs/embeddings.md): cards that mean what a
phrase says (or what one card asks), with Aman's memory of each, where mnemosyne_search_cards only matches words.
Read-only, so the propose token is enough. Run it once `embeddings.neighbors` is on in config/llm.yaml; until then the
endpoint answers 404.
Additive and idempotent like the other patches: backs up to *.bak-mnemosyne-neighbors, refuses to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    @mcp.tool()
    def mnemosyne_neighbors(query: str = "", card_id: int = 0, k: int = 10) -> dict:
        """Cards that MEAN the same as `query` (a concept, a problem statement, a formula), or as card `card_id`,
        each with Aman's memory of it (retrievability, stability, lapses). Semantic, unlike mnemosyne_search_cards,
        which matches words: use this to find what he already holds near an idea, and that tool for exact terms."""
        return mnemo.neighbors(query, card_id, k)

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_neighbors" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-neighbors"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-neighbors")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+mnemosyne_neighbors) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

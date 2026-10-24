"""Adds mnemosyne_clip_toolkit to every Helios MCP instance: amanim's API, the house style, and harvest candidates,
read live, so a chat writes scenes in Aman's current style. Read-only. Backs up to *.bak-mnemosyne-toolkit and refuses
to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    @mcp.tool()
    def mnemosyne_clip_toolkit() -> dict:
        """Before writing a clip's scene: Aman's own Manim style, read live. amanim (his technique library: computed
        fields, Lorentz paths, Feynman lines, waves) with every function's signature and doc and its commit; the
        mn_style house helpers and palette roles (use role names, never hex: the colors are his design tokens); and
        harvest candidates, helpers marked `# harvest:` in published scenes that may belong in amanim. Reuse before
        rewriting."""
        return mnemo.clip_toolkit()

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_clip_toolkit" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-toolkit"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-toolkit")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+mnemosyne_clip_toolkit) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

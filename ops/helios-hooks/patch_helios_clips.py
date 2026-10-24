"""Adds the mnemosyne_clip* tools to every Helios MCP instance: a chat writes a Manim scene, the Obelisk renders it in
a throwaway sandbox (scripts/render_runner.py) and publishes it on /watch, and the chat checks its stills. Submitting
adds a render and never touches Anki, so the propose token is enough. Reviewed in docs/security/clip-submit.md.
Additive and idempotent like the other patches: backs up to *.bak-mnemosyne-clips, refuses to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    @mcp.tool()
    def mnemosyne_clips() -> dict:
        """Animated clips already on Aman's /watch page (title, courses, length, whether the video is there now).
        Check before making a new one; a clip whose video was cleared can be rendered again from the page."""
        return mnemo.clips()

    @mcp.tool()
    def mnemosyne_clip_submit(code: str, title: str, courses: str = "", topics: str = "", watch_for: str = "",
                              replace: str = "") -> dict:
        """Publish an animation to Aman's /watch page: send the Manim scene's Python and the Obelisk renders it
        (ManimCE 0.21.0, 720p30, Python 3.14 with numpy and scipy, no network, no files but mn_style.py). Follow the
        mnemosyne-tutor skill's clips.md for the house style (`from mn_style import *`, MnScene, sections). courses and
        topics are comma-separated ("phys207, math309"). Returns the clip's slug: check it with
        mnemosyne_clip_status, and to fix what its stills show, submit again with replace=<slug>. At most 20 a day."""
        return mnemo.clip_submit(code, title, courses, topics, watch_for, replace)

    @mcp.tool()
    def mnemosyne_clip_status(clip: str):
        """Where a submitted clip stands: waiting, rendering, failed (with the error to fix), or published, with
        its /watch link, any layout warnings, and the last frame of each section as images. Look at every still
        before telling Aman it's ready: overlapping labels, text off the edge, a wrong sign or number."""
        return mnemo.with_images(mnemo.clip_status(clip))

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_clip_submit" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-clips"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-clips")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+3 mnemosyne_clip* tools) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

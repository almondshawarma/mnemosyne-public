"""Adds mnemosyne_video_study to every Helios MCP instance: study mode, one span of an animation Aman wants to learn
craft from, measured densely (motion and holds, cuts, palette, composition) with the attribution to cite. It reads a
finished study, or only adds a request to the watcher's queue (YouTube links only), so the propose token is enough.
Also updates mnemo.py (with_images takes PNG too). Additive and idempotent like the other patches: backs up to
*.bak-mnemosyne-videostudy and refuses to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    @mcp.tool()
    def mnemosyne_video_study(video: str, start: str, end: str, fps: float = 6, look: str = ""):
        """Study one span (up to 120 s) of an animation Aman admires (3Blue1Brown, motion design), to dissect its
        craft with him (the "Dissecting a video" loop in the mnemosyne-clips skill: he says what he thinks works
        first). The first call queues it for the watcher on the Obelisk (YouTube links only; a minute or two); call
        again with the same video, start, and end to read it: the motion curve as an image (holds shaded, cuts red,
        caption starts ticked), each shot's key frame with its palette, tone, and where its ink sits, the holds and
        moves with their easing, the transcript, and the attribution to cite whenever a finding or frame is used.
        `look` looks closer: a stretch ("1:25-1:30") sends its filmstrips (6 frames a second, timed), times
        ("1:27.5,1:41") the nearest full frames. Measured from frames: no sound, and speed is inferred from how much
        of the picture changes."""
        return mnemo.with_images(mnemo.video_study(video, start, end, fps, look))

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_video_study" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-videostudy"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-videostudy")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+mnemosyne_video_study) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

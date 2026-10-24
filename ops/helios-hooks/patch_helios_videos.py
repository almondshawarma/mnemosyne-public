"""Adds the mnemosyne_video* tools to every Helios MCP instance: lecture videos the tutor has watched (captions, key
frames, contact sheets; never the video), and a request to watch a new one. Reading needs only the propose token, and
the request only adds to the watcher's queue (YouTube links only), so the propose token is enough for it too.
Additive and idempotent like the other patches: backs up to *.bak-mnemosyne-videos, refuses to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    @mcp.tool()
    def mnemosyne_videos() -> dict:
        """Lecture videos already watched for Aman (3Blue1Brown, Strang's 18.06, ...): title, length, license,
        chapters, and how many contact sheets each has; plus any still waiting to be watched, or failed."""
        return mnemo.videos()

    @mcp.tool()
    def mnemosyne_video_search(query: str, video: str = "", limit: int = 10) -> dict:
        """Where something is said in the watched videos: ~45-second transcript passages containing every word
        (prefix match), each with the video, its time, and a timestamped YouTube link. `video` limits it to one id.
        Search first, then read just that stretch with mnemosyne_video_watch: it costs far fewer tokens."""
        return mnemo.video_search(query, video, limit)

    @mcp.tool()
    def mnemosyne_video_watch(video: str, start: str = "", end: str = "", images: str = "auto"):
        """Watch a stretch of a video: the transcript from `start` to `end` (mm:ss; default the whole video), its
        chapters, and the contact sheets covering it (each image is a grid of 12 frames labeled with their times, so
        the boards and equations can be read). images="auto" sends sheets only for spans of 15 minutes or less (at
        most 3 sheets); "yes" or "no" to force. Captions can be wrong, even human ones: trust the board. Five minutes
        is a couple of thousand tokens; a whole lecture is about 20k."""
        return mnemo.with_images(mnemo.video_watch(video, start, end, images))

    @mcp.tool()
    def mnemosyne_video_frames(video: str, at: str):
        """Full-size frames nearest the given times (comma-separated mm:ss, up to 4), each with what was said around
        it: for reading one board or equation closely after spotting it on a contact sheet."""
        return mnemo.with_images(mnemo.video_frames(video, at))

    @mcp.tool()
    def mnemosyne_video_request(video: str) -> dict:
        """Ask the watcher on the Obelisk to watch a YouTube video (a link or the 11-character id): it keeps the
        captions and the frames that matter, never the video itself, and costs no AI tokens. A lecture takes about two
        minutes; check with mnemosyne_videos, then read it with mnemosyne_video_watch. Already watched: says so."""
        return mnemo.video_request(video)

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_video_watch" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-videos"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-videos")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+5 mnemosyne_video* tools) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

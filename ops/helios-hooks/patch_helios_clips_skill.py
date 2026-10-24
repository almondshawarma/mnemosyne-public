"""Points the clip tools at the mnemosyne-clips skill, and says in their descriptions what they're for, so a chat that
is asked for "a video" or "an animation" finds mnemosyne_clip_submit instead of rendering one in its own sandbox
(2026-10-23: a claude.ai chat did exactly that). Only docstrings change. Backs up to *.bak-mnemosyne-clips-skill and
refuses to run twice."""

import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
OLD_SUBMIT = '''        """Publish an animation to Aman's /watch page: send the Manim scene's Python and the Obelisk renders it
        (ManimCE 0.21.0, 720p30, Python 3.14 with numpy and scipy, no network, no files but mn_style.py). Follow the
        mnemosyne-tutor skill's clips.md for the house style (`from mn_style import *`, MnScene, sections). courses and
        topics are comma-separated ("phys207, math309"). Returns the clip's slug: check it with
        mnemosyne_clip_status, and to fix what its stills show, submit again with replace=<slug>. At most 20 a day."""
'''
NEW_SUBMIT = '''        """Publish an animation, video, or visualization to Aman's /watch page. Use this for any request for a video or
        an animation of his course material: send the Manim scene's Python and the Obelisk renders it (ManimCE
        0.21.0, 720p30, Python 3.14 with numpy and scipy, no network, no files but mn_style.py and amanim). A video
        rendered in your own sandbox never reaches him. Follow the mnemosyne-clips skill (clips.md; 3d.md for 3D) for
        the house style (`from mn_style import *`, MnScene or MnScene3D, sections). courses and topics are
        comma-separated ("phys207, math309"). Returns the clip's slug: check it with mnemosyne_clip_status, and to fix
        what its stills show, submit again with replace=<slug>. At most 20 a day."""
'''
OLD_CLIPS = '''        """Animated clips already on Aman's /watch page (title, courses, length, whether the video is there now).
        Check before making a new one; a clip whose video was cleared can be rendered again from the page."""
'''
NEW_CLIPS = '''        """Animated clips (Manim videos) already on Aman's /watch page (title, courses, length, whether the video is
        there now). Check before making a new animation; a clip whose video was cleared can be rendered again from the
        page. Making one is the mnemosyne-clips skill."""
'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne-clips skill" in text:
        sys.exit("already patched")
    if OLD_SUBMIT not in text or OLD_CLIPS not in text:
        sys.exit("server.py doesn't have the docstrings this patch expects; patch by hand")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-clips-skill"))
    srv.write_text(text.replace(OLD_SUBMIT, NEW_SUBMIT).replace(OLD_CLIPS, NEW_CLIPS))
    print("patched mcp/server.py (clip tool descriptions); now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()

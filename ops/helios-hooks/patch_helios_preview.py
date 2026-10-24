"""Helios's Jobs & Renders page (helios-preview, :8095) learns about mnemosyne's clip renders (2026-10-24).

scripts/helios_jobs.py already writes each clip render into the page's own job format, so the page lists them as it
lists any job. This patch adds what those records need: a "waiting" badge (a clip in the render queue), a link to the
clip on /watch, auto-refresh while anything is waiting, and a "How to send a render" panel at the top: open when
there are no jobs, folded otherwise. Backs up to preview_server.py.bak-mnemosyne-preview and refuses to run twice.
Then: docker compose up -d --build preview (in ~/helios/stack).
"""

import shutil
import sys
from pathlib import Path

SRV = Path.home() / "helios" / "stack" / "preview" / "preview_server.py"
MARK = "mnemosyne-preview"

HELP = '''HELP = """<details class="help"{open}><summary>How to send a render here</summary>
<h3>An animated clip for /watch (mnemosyne)</h3><ul>
<li><b>From any Claude chat:</b> ask for an animation or video of course material (&ldquo;make a video showing the four
subspaces&rdquo;). The <i>mnemosyne-clips</i> skill writes the scene and sends it with <code>mnemosyne_clip_submit</code>.
It shows up here within a minute as <i>clip: &lt;title&gt;</i>, waiting and then running with its log, and lands on
<a href="http://<obelisk>:8110/watch">/watch</a> when it's done.</li>
<li><b>From Claude Code</b> in the mnemosyne repo: <code>uv run scripts/render.py scenes/&lt;course&gt;/&lt;name&gt;.py</code>
for a draft, <code>--hq</code> to time it at 720p, <code>--final</code> to publish it.</li>
<li><b>A clip whose video was cleared:</b> &ldquo;Render it again&rdquo; on /watch.</li>
<li>Limits: 870 s of rendering at 720p30 and 20 submissions a day. A clip waits while another renders.</li></ul>
<h3>Any other job (Blender, a Manim script, a long command)</h3><ul>
<li><b>From a Helios chat:</b> <code>submit_job</code>, kind <code>demo</code>, <code>blender</code>, or <code>manim</code>.</li>
<li><b>On the Obelisk:</b> <code>~/helios/stack/tools/helios_job.py &lt;name&gt; "&lt;command&gt;"</code>.</li></ul>
</details>"""  # mnemosyne-preview


def link(m):
    u = str(m.get("link") or "")
    return f'<a href="{html.escape(u)}">open &#8599;</a>' if u.startswith("http") else ""


'''

EDITS = [
    # the waiting badge and the help panel's look
    ('.b-running{background:var(--amber)}.b-done{background:var(--green)}.b-failed{background:var(--red)}',
     '.b-running{background:var(--amber)}.b-done{background:var(--green)}.b-failed{background:var(--red)}'
     '.b-waiting{background:var(--blue)}\n'
     'details.help{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:10px 14px;'
     'margin:10px 0}\ndetails.help summary{cursor:pointer;font-weight:600}details.help h3{font-size:.92rem;'
     'margin:12px 0 4px}\ndetails.help ul{margin:0;padding-left:18px;color:var(--mut);font-size:.88rem}'
     'details.help li{margin:4px 0}details.help code{font-size:.8rem;color:var(--fg)}'),
    ('def badge(state):', HELP + 'def badge(state):'),
    # the panel on the index: open when empty, folded above the list otherwise
    ("""        return page('<div class="empty">No jobs yet. Launch one with '
                    '<code>helios_job.py &lt;name&gt; &lt;command&gt;</code>.</div>')
    running = any(j.get("state") == "running" for j in jobs)""",
     """        return page(HELP.format(open=" open") + '<div class="empty">No jobs yet.</div>')
    running = any(j.get("state") in ("running", "waiting") for j in jobs)"""),
    ("""    return page("".join(cards), refresh=8 if running else None)""",
     """    return page(HELP.format(open="") + "".join(cards), refresh=8 if running else None)"""),
    # the /watch link on each card and on a job's page
    ("""            f'<span>{html.escape((m.get("started") or "")[:19])}</span>'
            + (f'<span>exit {m.get("exit_code")}</span>' if m.get("exit_code") not in (None,0) else "")""",
     """            f'<span>{html.escape((m.get("started") or "")[:19])}</span>'
            + (f'<span>{link(m)}</span>' if link(m) else "")
            + (f'<span>exit {m.get("exit_code")}</span>' if m.get("exit_code") not in (None,0) else "")"""),
    ("""            + (f'<span>exit {m.get("exit_code")}</span>' if m.get("exit_code") is not None else "")""",
     """            + (f'<span>{link(m)}</span>' if link(m) else "")
            + (f'<span>exit {m.get("exit_code")}</span>' if m.get("exit_code") is not None else "")"""),
    ("""    return page(body, refresh=6 if m.get("state") == "running" else None)""",
     """    return page(body, refresh=6 if m.get("state") in ("running", "waiting") else None)"""),
]


def main() -> None:
    text = SRV.read_text(encoding="utf-8")
    if MARK in text:
        sys.exit("already patched")
    for old, _ in EDITS:
        if text.count(old) != 1:
            sys.exit(f"preview_server.py doesn't match this patch near: {old[:60]!r}; patch by hand")
    shutil.copy2(SRV, SRV.with_name("preview_server.py.bak-mnemosyne-preview"))
    for old, new in EDITS:
        text = text.replace(old, new)
    SRV.write_text(text, encoding="utf-8")
    print("patched preview/preview_server.py; now: cd ~/helios/stack && docker compose up -d --build preview")


if __name__ == "__main__":
    main()

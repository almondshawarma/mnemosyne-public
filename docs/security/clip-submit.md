# Security review: chats submit scene code to be rendered (2026-10-13)

## What changed

CLAUDE.md said no MCP tool may run code until this review was done (`docs/video-plan.md`, V3). Aman asked that any
Claude chat be able to put a clip on `/watch`. He chose to render right away, with the sandbox as the gate, instead of
waiting for his approval on each one.

**What it adds:**
- `mnemosyne_clip_submit`, on every MCP instance, including the cloud-facing one;
- `POST /clips`, which takes the propose token.

**How a submission travels:**
1. The api stores the code as text in `media/render/.submitted/<slug>/`, and queues the slug.
2. `scripts/render_runner.py` runs on the host every minute from Aman's crontab. It renders each queued scene in a
   fresh container.
3. If the output checks out, it publishes the clip.

## Threats and what stops them

| Threat | Control |
|---|---|
| A cloud chat (or someone who gets the propose token) runs code on the Obelisk | The code runs only inside a throwaway container: `--network none`, `--read-only` root, `--cap-drop ALL`, `no-new-privileges`, user 1000, 2 GB of memory, 4 CPUs, 256 processes, a 1 GB tmpfs, and a 15-minute kill. No secrets, no Docker socket, no Helios network. |
| The code reaches other clips, the Ledger, or the repo | The container mounts only this job's `in/` (read-only: the scene, `mn_style.py`, and `job.py`) and `out/`. Each job has its own container, so nothing it starts outlives it, and no job can touch another's files. |
| A crafted output escapes when published | The runner, which is trusted code, checks `out/` first. It refuses symlinks, a video that isn't an MP4 (`ftyp`) or is over 300 MB, files among the stills that aren't `NN-name.png/jpg`, and more than 80 stills. It copies only those files. |
| Its metadata injects into `/watch` | Title, courses, topics, and "watch for" come from the submission, which the api validated, never from the container. From the container, only numbers and section names (capped at 80 characters) are kept, and the page HTML-escapes everything. |
| Command injection in the runner | The queue holds only slugs, matched against `^[a-z0-9][a-z0-9-]{0,63}$`. The docker command is an argument list, never a shell string. The class name comes from the api's own parse and must be an identifier. |
| A chat reads repo files through the gallery (2026-10-23) | `mnemosyne_clip_status("gallery-<name>")` reads only `scenes/gallery/<name>.py` and the JPEG stills under `scenes/gallery/stills/<name>/`. The name comes from a slug already matched against `^[a-z0-9][a-z0-9-]{0,63}$`, so it can't leave that folder. It's read-only, and the gallery is demo code committed to the repo: nothing secret. |
| A scene's `"look"` (2026-10-24) runs more code | The post pass (bloom and the rest) is `amanim.post`, from the read-only copy of amanim in `in/`. It runs inside the same container, after Manim, on the container's own output. Only the names `ethereal` and `soft` do anything. The runner checks `out/` exactly as before, so nothing new reaches the host. It costs about 0.1 s a frame, inside the same 15-minute kill. |
| A scene's sound cues and tones (2026-10-25) flood or inject | They're `MN-CUE` / `MN-TONE` lines on the scene's stdout. job.py keeps only numbers in range and short strings: 500 cues, 8 tracks with names matching `[A-Za-z0-9_-]+` and a voice of `tone` or `buzz`, 40,000 samples a track, labels capped at 160 characters, and non-finite values refused. The runner's whitelist never copies them to `/watch`. Only `render.py --publish` or `--kit` reads them, from a session, writing an ASCII EDL (with `\|` stripped from names) and WAVs synthesized locally from the numbers. |
| A chat overwrites Aman's hand-made clips | `replace` only takes a clip a chat submitted (`origin: chat`). New submissions get new slugs. |
| Abuse: CPU burn, disk fill | 20 submissions a day (Ledger `clip_submission`), 5 renders waiting at once, 4 renders per runner pass, 200 KB of code, and the container caps above. Videos expire after `keep_days` (`config/clips.yaml`). |
| A container escape through a kernel or runtime bug | Not stopped by these controls; it's the remaining risk. No capabilities, no new privileges, and no network shrink it. Keep Docker and the kernel patched (unattended upgrades are on). |

## What it doesn't touch

- Anki: no collection access, so invariants 1–5 and 9 don't apply.
- The TAMU gateway: nothing is spent.
- Invariant 7 ("cloud-facing tools can only propose"): it holds for the collection. This tool adds a rendered clip,
  which Aman can delete on the page.

## Record

Every submission is in Ledger `clip_submission`: slug, who sent it, the code's sha256, size, and time. The code
itself stays with the clip (`scene.py`). The runner logs to `media/render-queue/runner.log`.

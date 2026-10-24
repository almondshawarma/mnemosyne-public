# Video for the tutor — plan (proposal, 2026-10-13)

Two abilities for `mnemosyne-tutor`:
1. **Watch:** read a video (a 3Blue1Brown episode, a Strang 18.06 lecture) closely enough to quote it, point at a
   moment, and teach from it.
2. **Make:** write and render short Manim animations for the ideas where motion *is* the content.

Phase: the watch tools are tutor tools, which PLAN §10 puts in **P4**. Video on cards is media, which is **P5**. Nothing
here writes to the collection until step V4, and V4 needs a desktop session.

Status (2026-10-13):
- **V2 is built.** It uses ManimCE v0.21.0, pinned by digest, which Aman left to Claude's choice.
- **Three scenes are published on `/watch`:** `math309/linear_map`, `phys207/gauss_flux`, and
  `phys207/mesh_matrix`.
- **V1 works from Claude Code.** `scripts/watch.py` fetches captions and frames on the Obelisk and builds contact
  sheets; the video is never kept.
  - It was tested on Strang 18.06 L12, *Graphs, Networks, Incidence Matrices*: 762 human caption cues, then 86 frames
    on 8 sheets. Every board was legible.
  - Frame picking changed after the first try. OCW's camera pans constantly, so cuts don't mark the boards. Now each
    30 s window keeps the frame with the most detail and the least motion.
  - **The claude.ai tutor can watch too (2026-10-13).** It has five MCP tools: search, a watch span, frames, the
    library, and a request.
    - Requests go through a queue folder to the `watcher` compose service. It is isolated: its own network, a
      read-only root, no capabilities, and YouTube links only.
    - None of it uses the TAMU gateway; the reading happens in Aman's own Claude conversation.
  - **Clips clear their own videos (2026-10-13).** A clip's video goes 14 days after its last open
    (`config/clips.yaml`), or whatever Aman sets for that clip on the page, or never. Its code stays, and the
    `renderer` service renders it again on request.
  - **V3 is done: any chat can publish a clip (2026-10-13).** `mnemosyne_clip_submit` sends the scene's code, and
    `scripts/render_runner.py` renders each one in its own throwaway container and checks the output.
    - The security review is `docs/security/clip-submit.md`.
    - The long-running `renderer` service from earlier the same day was retired: its shared mounts weren't
      safe for untrusted code.
  - **Study mode (2026-10-24)** is for dissecting an animation's craft: one span, densely. The loop is in the
    `mnemosyne-clips` skill.
    - **The watcher's controls are unchanged:** the same container flags, its own network, YouTube-only requests
      from the API, and the video is deleted before anything is written. The API also caps a span at 120 s and the
      frame rate at 12.
    - **What changed:**
      - The image now has Debian's ffmpeg. yt-dlp uses it to fetch only the span; that's a new network client in the
        same sandbox.
      - The queue takes `<id>@<start>-<end>.json` requests. Their numbers are validated by the API and clamped again
        by the job.
    - **From a session,** `scripts/watch.py --study` runs on the ThinkPad by default (Aman's own links, any site
      yt-dlp reads). There it's outside any sandbox: the same pinned packages, the span in a temp folder outside
      OneDrive, deleted afterwards.
    - **Attribution:** every study records its creator, title, link, and license. Dense frames stay in gitignored
      folders.
  - **Still open:** a curated library (`config/videos.yaml`) to watch ahead of time, course by course.

Aman's answers (2026-10-13):
- Start with **MATH 309 and PHYS 207**.
- The **tailnet `/watch` page** is enough for now, so V4 waits.
- Generated scenes **double as material to learn from**, through `mnemosyne-unpack`.
- **Videos are never saved.** Only transcripts and the frames that matter are kept, which is option (a) below. The goal
  is for the tutor to understand a video, not to archive it.
- The engine is **ManimCE for now.**

> **Later: try ManimGX** (note for a future session). Once it has been out a few months (past v0.1, with outside users
> and a settled API), run the bake-off below on the same two scenes, `linear_map` and `gauss_flux`. Check four things:
> the PHYS 207 notation through its LaTeX→Typst conversion, `manimgx inspect` against `MnScene`'s layout check, render
> time, and the number of fix rounds. If it wins, the switch is: a new image in `scripts/render.py`, its render
> command in `services/manim/job.py`, and ports of `mn_style.py` and the scenes. The APIs mostly match.

---

## What exists already

- **Claude can't take video directly.** The API and claude.ai accept images, not video files. So "watching" means:
  the transcript with timestamps, plus frames picked from the right moments, sent as images. The book tools already do
  this for page images (`with_images` in `ops/helios-hooks/mnemo.py`), so the MCP plumbing works.
- **Video already plays on AnkiMobile.** The 48 M-Sign cards carry Signbank videos through `audio.py`'s `ATTACH` and
  `store_once`.
- **`SourceLink` is on every note type.** A card made from a video can link `https://youtu.be/<id>?t=460`, and
  tapping it on the iPhone opens YouTube at that second. No schema change is needed.
- **`computing::manim`** is already in `config/topics.yaml`.

## Make It Stick caution

Watching a good explanation feels like learning, and that feeling is mostly fluency. A video is **another angle** for a
weak card (tutor §2), and retrieval comes right after it. The tutor never says "go watch this" and stops there.

---

## Part 1 — Watch

### Sources: `config/videos.yaml`, like `books.yaml`

A curated list. Each entry gets: key, title, by, URL (playlist or video), license, courses, and topics. 3b1b entries
also get the matching folder in `3b1b/videos` (`source_code`).

| Course | First entries |
|---|---|
| MATH 309 (Leon, Haberman) | 3b1b *Essence of linear algebra*; Strang 18.06 (MIT OCW, CC BY-NC-SA); 3b1b *Differential equations* (heat equation, Fourier series) |
| PHYS 207 (Young & Freedman ch 21–32) | to choose together, by chapter. There's less 3b1b here, so lectures matter more. |
| PHYS 221, MEEN 221, NUEN 201 | later, and only where a card keeps failing |

Ad-hoc links ("watch this with me") work too. They're limited to YouTube, MIT OCW, and archive.org hosts, so a cloud
caller can't make the Obelisk fetch arbitrary URLs.

### Fetch and index: `src/mnemosyne/videos.py` + `scripts/video_fetch.py`

Runs on the Obelisk, on demand. Nothing is scheduled.

1. **Metadata and chapters** come from `yt-dlp`, pinned and updated deliberately. YouTube breaks it every few months
   (PO tokens, JS-runtime changes), so when a fetch fails, the tool says "update yt-dlp" instead of failing silently.
2. **Transcript.** Use human captions first, then auto-captions. OCW's own transcript is best for 18.06. When there are
   no captions, fall back to `faster-whisper`. It runs on CPU, or on the 2080 only when Ollama is idle, so dedupe never
   loses its GPU.
3. **Frames that matter.** Fixed-interval sampling wastes images on 3b1b mid-animation and on Strang walking.
   - Decode at 2 fps and keep **settled** frames: the picture has stopped changing for at least 1 s. That's the end of a
     3b1b animation, or a finished board.
   - For chalkboard lectures, also keep the **most-ink frame before each erase**.
   - Drop near-duplicates by perceptual hash.
   - Each kept frame is stored with the caption line at its timestamp.
4. **Storage.**
   - Keep the frames, about 30 MB per video, in `media/video/watch/<id>/` (gitignored, like ASL).
   - Delete the video file once frames are cut. A request for an arbitrary moment re-fetches only that second
     (`ffmpeg -ss`).
   - The search index (SQLite FTS + Ollama embeddings) is a cache, as `library.py`'s is: delete it and it rebuilds.

### MCP tools: read-only, every instance, propose token (like the book tools)

| Tool | Returns |
|---|---|
| `mnemosyne_videos(course?)` | The library: what's indexed, with chapters |
| `mnemosyne_video_search(query, course?)` | Transcript segments that match, with timestamps and a `?t=` link |
| `mnemosyne_video_watch(video, from?, to?)` | The span's transcript with timestamps, plus a **contact sheet**: one image, a labeled grid of its settled frames |
| `mnemosyne_video_frames(video, at=[…])` | Up to 6 full frames (1280 px) at chosen times |
| `mnemosyne_video_source(video)` | 3b1b only: the scene code behind the episode, so the tutor knows what the animation computes |

The loop: read the transcript, look at the contact sheet, and pull full frames only where the board or equation matters.

Rough cost (image tokens ≈ w×h/750):
- **A 20-minute 3b1b episode:** about 4k tokens of transcript plus about 6 contact sheets at about 1.8k each, so
  roughly 15k in total.
- **A 50-minute Strang lecture:** about 10k of transcript plus boards.

All of the watching happens in Aman's own Claude session. The server only cuts frames, so there's no gateway allowance
and no spend.

### Tutor skill: a new §2 bullet and a short §1 entry

- **Another angle for a weak card.** Search the library for the idea, give the timestamped link and a sentence on what to
  watch for, then ask the retrieval question afterwards.
- **Watching together.** When Aman asks "what did Strang mean at 31:40?", pull those frames and that transcript span
  and work it with him. Log the question with `mnemosyne_log_question` as usual.
- **Cards from a video** go through `mnemosyne-cards` with `Source: "3b1b EoLA ch. 6, 7:40"` and a timestamped
  `SourceLink`. Canon 9 (source-faithful, in the source's notation) applies as it does to a lecture.
- The `mnemosyne-books` skill pattern carries over: cite the moment the way it cites a page.

### V1 is done when

- In a session on a weak MATH 309 card, the tutor finds the matching *Essence of linear algebra* segment, shows its
  frames, and quizzes afterwards.
- A card proposed from it has a `SourceLink` that opens YouTube at the right second on the iPhone.

---

## Part 2 — Make

### Engine: ManimGX if it passes a bake-off, else ManimCE

**ManimGX** ([PyPI](https://pypi.org/project/manimgx/), [repo](https://github.com/academa-labs/manimgx)) is Academa's
engine, first released 2026-10-07. It has a Rust/wgpu core and Manim CE's API. On paper it fits this job better than
CE:

- **`manimgx inspect`** writes a storyboard PNG and checks 2D layout without rendering, and exits 1 on problems. That's
  the feedback loop below, already built.
- **No TeX Live.** `MathTex` is converted to Typst. On Linux with no GPU it draws on the CPU (Mesa lavapipe) with
  nothing more to install, so the sandbox image is small.
- **Statically typed.** A type checker catches a wrong keyword before a render. At runtime, most mobjects silently
  ignore keywords they don't accept.
- **Fast**, by the vendor's own benchmark (11.5× ManimGL, 90× CE).

The risks:
- **It's v0.1.1 and six days old,** with two maintainers. Pin the version and hash, and run it only in the sandbox (a
  new package is also a supply-chain risk).
- **LaTeX goes through a converter.** Packages and `\def` don't convert. PHYS 207's notation (`\oint`, `\vec`, `\hat`,
  `\nabla`, `pmatrix`, `aligned`) isn't documented either way. `MathTypst` is the fallback.
- **Its behavior differs from CE in places.** Simultaneous animations on one mobject add their movements, and
  `.animate` applies its calls when the animation starts. Claude knows it only from its docs (`llms.txt`, its
  `skills/manimgx`), not from training.
- **It needs Python 3.13 or newer.** That's fine in its own container, which never touches mnemosyne's 3.12
  environment.

**Bake-off (the first task of V2).** Render the same two scenes in both engines, in the sandbox on the Obelisk:
- MATH 309: a 2×2 matrix as a transformation, with its eigenvectors staying on their span;
- PHYS 207: flux through a Gaussian surface, with \(\oint \vec E\cdot d\vec A = Q_\text{enc}/\varepsilon_0\).

Judge on four things: the formulas match the course's notation exactly, `inspect` catches real layout problems, render
time on the Obelisk's CPU, and how many rounds Claude needs to get a clean clip. ManimGX wins ties. Since the APIs
match, scenes port between the two either way.

**ManimCE** is the fallback. Its releases are stable and versioned, and its official Docker image
(`manimcommunity/manim`, pinned to a release tag) renders headless with TeX Live included. Models also write it more
reliably than anything else, because most public Manim code and docs are CE.

**ManimGL** is 3b1b's own engine. It isn't a candidate for generating:
- It renders through OpenGL and is built around an interactive window. It has no official image, and headless use needs
  Mesa+Xvfb or EGL work.
- Its API also changes without versioning.
- It's still useful for **reading** 3b1b's scene code (`mnemosyne_video_source`). The two dialects aren't compatible, so
  a 3b1b scene gets *translated* to the chosen engine, not run.

### What gets animated

Clips of 10–40 s, silent, one idea each: the canon's atomicity applied to motion. Pick ideas where a still image loses
the point. Some examples:
- a matrix as a transformation, and eigenvectors staying on their span;
- Fourier partial sums and the Gibbs overshoot;
- heat-equation modes decaying at different rates;
- flux through a Gaussian surface;
- phasors;
- a rigid body's moment balance (MEEN 221);
- a Bateman decay chain filling and draining.

### Where it renders: a sandbox on the Obelisk

Rendering runs code a model wrote, so it's isolated:

```
docker run --rm --network none --read-only --tmpfs /tmp --user 1000:1000 \
  --cpus 2 --memory 2g --pids-limit 256 \
  -v <job>/in:/in:ro -v <job>/out:/out \
  mnemosyne-manim:<pinned>  timeout 180 <render command>
```

The command is `manimgx render scene.py Scene -r 1280x720 --fps 30` with ManimGX, or `manim -qm --save_sections
/in/scene.py Scene -o /out` with CE.

- **`services/manim/Dockerfile`:** the chosen engine, pinned, plus `scipy`, `pint`, `data/anchors.yaml`, and a
  small `mn_style.py`. Its colors match `shared.css`, its type sizes are legible on an iPhone, and it adds safe margins
  and a background behind labels. Most model-written Manim bugs are layout bugs: overlaps and text off-screen. The style
  module removes most of them.
- **Numbers on screen** come from `scipy.constants`, `pint`, or `anchors.yaml` inside the scene code, never typed in
  (invariant 6). A clip that goes on a card is held to the verify gate like the card itself.
- **Outputs** go to `media/render/<id>/`: the mp4, one still per section, and the scene source. A Ledger `render` row
  (migration) keeps the source, its sha, the card's `MnemosyneID` if any, and its status. The source is the record; the
  mp4 is a cache that can be re-rendered.

### The feedback loop is what makes it good

Claude can't see a render unless the tools show it one.
1. **Type-check the scene.** This step is ManimGX only.
2. **Look before rendering.** With ManimGX, `manimgx inspect` gives a storyboard plus layout errors. With CE, render a
   draft at `-ql` (480p15) and take a still at the end of every section, plus the last frame.
3. **Fix.** Claude checks each frame against what the scene was supposed to show, fixes it, and repeats.
4. **Final render** at 720p30.

A scene isn't shown to Aman until its stills pass that check.

### Scenes to learn from (Aman, 2026-10-13)

Every scene is also a worked example of Manim for him (`computing::manim`):
- Its code carries comments on the *moves*: why this mobject, why this order, how the layout was kept clear.
- The `/watch` page shows the code beside the clip.
- When a scene teaches a technique he hasn't seen, `mnemosyne-unpack` turns it into a write-up and a drill in his metis
  curriculum, with cards through the canon. Not every scene does this; only the ones with a new move.

### Steps

- **V2: Claude Code only, no new tool.** `scripts/render.py` sends a scene to the Obelisk over ssh and runs the sandbox.
  Aman watches on a tailnet page, `/watch/<id>`, next to `/review`.
  - *Done when:* a 20 s clip for a real weak card is rendered, Claude catches and fixes at least one layout problem from
    its own stills, and Aman watches it on the iPhone over Tailscale.
- **V3: an MCP tool.** `mnemosyne_render(scene_code)` returns the stills and the `/watch` link.
  - It goes on **mcp-local only** first. Code execution from the cloud instance waits for the security review that
    CLAUDE.md requires for anything claude.ai can reach.
- **V4: clips on cards.** This needs a deliberate desktop session, because adding a field is a schema change and forces
  a full sync. The session gets a checklist in `docs/desktop-sessions/`.
  - **Proposed:** a `Visual` field on M-Basic and M-Cloze, rendered on the back only, muted, looping, and tagged
    `mode::visual`, so walks never get it.
  - **Canon:** a clip illustrates the card's answer and never adds a fact the card doesn't hold. Clips are ≤ 20 s and
    ≤ 2 MB (h.264, `mn_` prefix), stored once through `store_once`.
  - *Done when:* AnkiMobile plays it. Desktop doesn't count.
  - **Before V4,** a card can link its clip from `Elaboration` with no schema change at all.
  - **Whether V4 earns a desktop session** is decided by evidence. `/watch` counts how often each clip is opened, and
    from where (a card link or a session). The weekly audit reads those counts, along with whether the cards linked to
    a clip stop lapsing.

---

## Invariants check

| Invariant | How this keeps it |
|---|---|
| No collection writes outside anki-core; no schema from server | V1–V3 never touch Anki. V4's field is added on Desktop by Aman. |
| No paid spend (2026-10-07) | Watching happens in Aman's Claude session. Fetching, frames, Whisper, and rendering are local. Nothing uses the TAMU allowance unless he asks for a video to be carded by the pipeline. |
| Cloud tools only propose | The watch tools are read-only. Render runs code, so it waits for the security review before reaching claude.ai. |
| Numbers verified | Scene numbers come from scipy/pint/anchors, and clips on cards go through the gate. |
| Scheduled writes only at 04:15 | Nothing is scheduled. Fetches and renders run on request. |

## Open questions for Aman

1. **Keeping copies.** Downloading from YouTube is against YouTube's terms, even for private study. OCW's 18.06 and the
   3b1b code are CC BY-NC-SA, which is fine for this. Options:
   - (a) frames only and the video deleted after cutting (proposed);
   - (b) keep 480p copies;
   - (c) transcripts only, with frames fetched live per request.
2. **PHYS 207 sources.** Which lecture series do you trust for E&M? I'll propose candidates chapter by chapter against
   Young & Freedman.

Answered 2026-10-13:
- Clips live on the tailnet page.
- Start with MATH 309 and PHYS 207.
- Scenes double as Manim lessons.

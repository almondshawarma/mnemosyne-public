---
name: mnemosyne-clips
description: Make an animated clip for Aman's /watch page. Use whenever he asks for an animation, a video, a clip, a Manim scene, a visualization, or a picture that moves of anything he studies ("make a video showing the four subspaces", "animate the Compton shift", "show me this in 3D", "visualize the field", "draw how A moves the plane"), or when an idea is motion and a weak card needs another angle. Also for dissecting an animation he admires ("study this 3b1b span", "why does this look so good", "break down the motion here"): the study-mode loop. Writes a ManimCE scene in his house style (mn_style, his amanim library, his design tokens) and publishes it through mnemosyne_clip_submit, which renders it on the Obelisk; a video rendered in Claude's own sandbox never reaches him.
---

# mnemosyne-clips

A clip is a short Manim animation of one idea from his courses, published on `/watch` (the review page's server, same
token). He watches on his phone. The clip *is* its code: the Obelisk renders it in a sandbox, keeps the code, and can
render it again after the video is cleared.

**Publish through the Obelisk, never your own sandbox.** A chat can't upload a video file. If you can run Manim
yourself, use it for drafts only; the clip he sees is the one `mnemosyne_clip_submit` renders.

## Where you are

- **In a chat (claude.ai, Telegram) with the Helios tools:** follow [clips.md](clips.md) end to end:
  `mnemosyne_clips` → `mnemosyne_clip_toolkit` → write → `mnemosyne_clip_submit` → `mnemosyne_clip_status` → fix
  until the stills are clean → give him the link. If the tools are missing, say so in one line; don't hand him a
  video from your sandbox instead.
- **In Claude Code, for a clip that lives in the repo:**
  - Write `scenes/<course>/<name>.py` on `scenes/mn_style.py`, with the `CLIP` block (title, courses, topics, what to
    watch for, the card ids it's for) at the top.
  - `uv run scripts/render.py scenes/<course>/<name>.py` renders a draft in the sandbox and copies each section's last
    frame to `.render/<slug>/stills/`. `--amanim ../amanim` sends your working copy of amanim with it.
  - Read every still. Fix overlaps, cramped labels, wrong signs, and any `MN-LAYOUT` line before he sees it. Then
    render with `--final`, which publishes it.
  - amanim is `~/OneDrive/Documents/Projects/amanim` here (`~/helios/amanim` on the Obelisk, pulled nightly).
    Improve it when a scene needs something reusable, with tests, and push.

## Pattern-match first: the gallery

`scenes/gallery/` has one short demo per technique, with its stills. The toolkit lists them; read one with
`mnemosyne_clip_status("gallery-<name>")` (its code and stills), or open `scenes/gallery/<name>.py` in Claude Code.
Borrow the pattern, not the scene: compute your own numbers and views.

| Technique | Entry |
|---|---|
| Which palette roles look alike, and their contrast: **see it before choosing colors** | `gallery-palette` |
| A plane and its normal line; the computed edge-on view where the right angle is true | `gallery-plane-and-line` |
| An orbit that stops on a computed view | `gallery-orbit-stop` |
| A 3×3 matrix moving space (the 3D `linear-map`) | `gallery-matrix-3d` |
| A parametric surface as a wireframe | `gallery-parametric-surface` |
| A potential surface over its contour map | `gallery-potential-surface` |
| A 3D vector field | `gallery-vector-field-3d` |
| A moving camera zooming into a detail | `gallery-zoom` |
| A live plot driven by a ValueTracker | `gallery-live-plot` |
| The Mathieu stability diagram, and the motion it predicts (amanim.plots) | `gallery-mathieu` |
| A circuit with currents moving along its wires | `gallery-circuit` |
| Labels placed automatically in a crowded 3D picture, and again for a new view | `gallery-label-placement` |
| Feynman diagrams that move, in the glowing look (diagram, reveal, flow, depth plate, bloom) | `gallery-feynman` |

Anything 3D: read [3d.md](3d.md) first (computed views, render cost, what 3D can't show).

## What makes a clip worth making

- **It's computational science first (Aman, 2026-10-24; metis D-014).** Every clip teaches a computation as well as
  a concept. Tell him four things:
  - **What's computed:** the physics or the measurement.
  - **The method:** RK4, a conservation law, a pyramid, a median baseline, and so on.
  - **How it was verified:** an invariant the code didn't use, or a synthetic case with a known answer.
  - **What it costs.**

  New methods go into amanim with tests against physics invariants. When something clever happens, offer
  `mnemosyne-unpack`. The look matters too, as what makes the computed picture readable, but it's never the point.
- **The idea is motion,** or it has a shape: a matrix moving the plane, field lines crossing a surface, a mesh walk
  becoming a row of `[A | b]`, a subspace and its complement in 3D.
- **Check what exists first:** `mnemosyne_clips`, or `scenes/<course>/*.py` in the repo.
- **Write in his style.** amanim is his technique library (calculate what you teach), and `brand.py` holds his design
  tokens. Reuse what's there; mark reusable pieces `# harvest:`. Leave the tokens alone.
- **Use his course's notation,** the same as his cards (search them first), and compute every number from the
  scene's inputs; never type one in.
- **Comment the moves.** He learns Manim from these: say why this mobject, why this order, how the layout stays
  clear. When a scene uses a technique worth learning (an updater, `always_redraw` vs. a snapshot, a ValueTracker),
  offer `mnemosyne-unpack`.

## Dissecting a video

How Aman builds taste: take apart a span of an animation he loves (3Blue1Brown, motion design) to see why it works.
The findings go into the taste guide, `TASTE.md` in the amanim repo (started 2026-10-24); this section is the loop,
not the guide. Read the guide before writing a clip in the glowing look.

1. **He picks a span** he loves, 10–90 s (120 at most). [references.md](references.md) lists what he's flagged
   so far: the glowing look (Arithmetes, Knowable) and his subjects (fields, particles, plasma).
2. **He writes what he thinks works before any analysis.** This is his metis contract: predict first. Ask for it, and
   don't show any measurement until he has written it down.
3. **Run the study, then go through it with him.**
   - In Claude Code: `uv run scripts/watch.py <link> --study 1:20-1:50 [--fps 6]`. It runs on this machine (about a
     minute) into `.watch/<id>/study/<start>-<end>/`. Add `--on obelisk` to use the sandbox there instead. Any link
     yt-dlp reads works, including Vimeo, Instagram Reels, and a direct .mp4. So does a saved video file, credited
     with `--creator`, `--title`, and `--link`.
   - In a chat: `mnemosyne_video_study(video, start, end)` queues the study (YouTube only). The same call returns it
     once it's done. `look="1:25-1:30"` sends that stretch's filmstrips; `look="1:27.5"` sends a full frame.
   - Read `motion.png` and the shot sheets first. Then read `study.json` and open filmstrips only where something
     happens.
   - Go through it together, comparing each point with what he predicted:
     - **Composition and hierarchy:** is there one focal point? Where does the ink sit (centroid, thirds), and how
       much of the frame is negative space?
     - **Palette and contrast:** how few colors, how much background, and the contrast of the boldest ink.
     - **Motion:** easing (`peak_at` near 0.5 is an ease in-out), anticipation, staggering (a build that runs across
       one filmstrip row), and identity-preserving transforms vs. fades. The 3:40 unroll in "But what is a neural
       network?" is one continuous transform, not a cut.
     - **Pacing:** shot lengths, and the hold after each payoff.
     - **Typography:** read it from full frames.
     - **Sync:** match caption starts (the ticks under the plot's axis) against the moves.
4. **The findings go into the taste guide** (amanim's `TASTE.md`). Each one says what it is, why it works, the
   evidence (the study's `attribution.credit` and a timestamped link, never committed frames), and how amanim does
   it.
5. **He recreates one shot as a study** in his own style, credited "after <creator>, <title>, <time>".

**What the study measures, and its limits:**
- **Claude reads frames and transcripts, not video.** Timing comes from frame differences at the video's own rate
  (up to 30 a second):
  - energy is how much of the picture changes (a speed, smoothed over 1/6 s, so repeated frames don't make a comb);
  - a hold is when no pixel visibly changes;
  - a cut is a one-frame spike that replaced the picture. A whole-screen clear counts as a cut; a fade never does.
- **Size and speed blur together:** a big shape moving slowly can read like a small one moving fast. Check the
  filmstrip before naming an easing.
- **There's no sound:** music, sound design, and the voice's tone are missing.
- **Times are within a frame** of the original.

**Credit every source:** cite `attribution.credit` with any finding or frame. Dense frames stay in gitignored folders
(`.watch/`, `media/`) and are never committed. If a guide is ever public, it links timestamps and uses only a few
attributed stills for commentary.

## After he watches

- **Watching is not studying.** Ask the retrieval question the clip was for.
- **A clip may be code only.** Its video is cleared some days after it was last watched (14 by default; he sets each
  clip on the page). Its code stays, and the page's "Render it again" brings it back in about a minute.
- **A card can link its clip** in `Elaboration`: `<a href="http://<obelisk>:8110/watch?via=card#<slug>">▶ clip</a>`.
  The page counts opens by where they came from, and the weekly audit reads the counts. Cards go through
  `mnemosyne-cards` and his review, never straight from a clip.

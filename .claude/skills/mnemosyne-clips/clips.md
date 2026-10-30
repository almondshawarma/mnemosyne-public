# Making an animated clip for Aman's /watch page

Any Claude chat with the Helios tools can publish a clip. Send the scene's code with `mnemosyne_clip_submit`. The
Obelisk renders it in a throwaway sandbox and puts it on `/watch`, where he watches on his phone. You can't upload a
video file; the code is the clip, and it's kept so the clip can be rendered again later.

## The loop

1. **Check `mnemosyne_clips`** for one that already exists.
1. **Read `mnemosyne_clip_toolkit`.** It returns Aman's own style, read live from the Obelisk:
   - amanim, his technique library: computed fields, `lorentz_path`, Feynman lines, `sine_path`, 3D views and shapes
     (`amanim.threed`), circuits, plots (`plot_axes`, `live_graph`, `rk4`, `phase_portrait`, the Mathieu
     diagram), each with its signature and doc;
   - the house helpers and palette roles;
   - the gallery: one short demo per technique. Read the closest one with `mnemosyne_clip_status("gallery-<name>")`;
   - harvest candidates.

   It changes as he works, so read it every time and reuse before rewriting.
2. **Write the scene,** using the house style below. Use his course's notation; search his cards for it first.
   Compute every number from the scene's own inputs; never type a result in.
3. **Draft it locally if you can.** If your own sandbox can run Manim, render it at `-ql` first and look at the
   frames. That's a draft only: the clip he sees is the one the Obelisk renders from your submission.
4. **Submit:** `mnemosyne_clip_submit(code, title, courses="phys207, math309", topics=..., watch_for=...)`.
   `watch_for` is one or two sentences on what to notice.
5. **Check the status:** `mnemosyne_clip_status(slug)`. A clip renders in roughly as long as it runs, so a 3-minute
   walkthrough takes a few minutes.
   - **failed:** read the error, fix the code, and submit again with `replace=slug`.
   - **published:** look at every still. Check for overlapping labels, text past the edge, cramped math, a wrong sign
     or number, and any `layout_warnings`. Fix and resubmit with `replace=slug` until they're clean.
6. **Give him the `/watch` link,** say what to watch for, and ask the retrieval question afterwards. Watching is not
   studying.

## The environment

- **ManimCE 0.21.0** (`from manim import *` works).
- **Python 3.14,** with numpy and scipy. LaTeX comes from the image's TeX Live (amsmath, physics).
- **Rendered at 720p30.** Each `self.next_section("name")` gives a still and a chapter button on the page.
- **No network, and no files** but your scene and `mn_style.py`.
- **Packages:** matplotlib is there too (for amanim's equipotentials).
- **Limits:** 870 s of rendering at 720p30, 200 KB of code, and 20 submissions a day. A 3D clip can run out of
  time long before it runs out of content: [3d.md](3d.md) has the cost of each piece.

## Aman's style: amanim + his design tokens

Every render has two of his own layers beside the scene.

- **amanim** (`from amanim import ...`) is his technique library, and its rule is: calculate what you teach.
  - A field, a trajectory, or a wavelength on screen comes from the physics: `coulomb_field`, `field_lines`,
    `lorentz_path(q_over_m, E, B, r0, v0)`, `sine_path(wavelength=...)`.
  - Set dressing (a glow) may be faked.
  - **Feynman diagrams:** build them with `diagram(vertices, edges)`. An edge is `(from, to, kind, label)`, and kind
    is fermion, antifermion, photon, gluon, boson, or scalar.
    - Give fermion lines their glow with `diagram(..., halos={"fermion": {...}, "antifermion": {...}})`.
    - Add `d.unfold(clock, start=, speed=)` to the scene; no play draws it. A "now" sweeps along the time axis, lines
      draw up to it (into a vertex together, out of it together), vertices flash as it reaches them, and waves
      travel while they draw.
    - `d.flow(clock)` then adds sparks riding forward in time.
    - Don't `play(d.reveal())` then flow: the waves draw frozen and snap into motion (Compton, 2026-10-26).
    - Say what a diagram is (taste guide 30): one term of the amplitude, and the terms add. Read in time, each is
      only one of its two vertex orderings. Compton's u-channel emits before it absorbs; that's why its photons cross.
    - `gallery-feynman` shows all of it.

## The glowing look (Aman, 2026-10-24)

He prefers glowy, flowing, spacey work over 3b1b's flat look: Arithmetes (custom ManimGL shaders) and Knowable.
Cairo can't add light, so the look comes in layers:
- **`"look": "ethereal"` in the `CLIP` block** puts the render through `amanim.post` after Manim: bloom (thin
  strokes glow), slight colour fringing, a vignette, grain, and a cool lift of the blacks. `"soft"` is gentler, for
  text-heavy clips, since big bright areas bloom hard. It costs about 0.1 s a frame. The stills show it, so check
  them.
- **Never fully still.** Keep something moving through holds: `flow(clock)`, `ride` (sparks along any path), a slow
  drift.
- **Backgrounds are optional, and sparing.** There doesn't have to be anything behind the action (Aman). When there
  is, use `depth_plate(group)`: faint math out of focus, drifting slower than the foreground; the layout check skips
  it. Keep it away from where things happen, and never let it repeat the foreground. A frame the action already
  fills needs none.
- **Glow inside the scene:** `halo(mob)` goes behind a line, and `spark` makes a point of light.
- **Ease:** use `settle` (fast out, long settle) for arrivals instead of a symmetric ease.
- **Physics never waits for the animation.** Light and particles move on a clock at their computed speeds, and leave
  the frame instead of stopping when a `play` ends. Use **`clock = self.clock()`**, the time of the frame being
  drawn. Not a `lambda m, dt: m.increment_value(dt)` tracker: Manim gives every play's first frame dt = 0, so that
  clock repeats a frame at every play, wait and caption (Faraday v2, seen in study mode as a dip at each one). If
  you ever write your own clock, its updater must take `dt`: a `wait()` with no time-based updater anywhere is one
  frozen frame.
- **Never FadeIn or FadeOut anything that moves.** Manim pauses an animated mobject's updaters until the animation
  ends, so a wave brought in with `FadeIn` stands still for the fade and then snaps forward (Faraday v1: the wave at
  0:01, the phasors at 0:07, the electrons at 0:18, all Aman's notes). Fade it through a tracker instead:
  `m.add_updater(fader(m, show.get_value))` then `self.play(show.animate.set_value(1))`, or `pulse(..., gain=)`, or
  read the tracker inside the `always_redraw` function. mn_style prints `MN-MOTION` when a play froze something
  that was moving; treat it like a layout warning.
- **To show a turn, look along its axis.** A rotation about an axis lying in the screen draws as a change of size,
  not a turn: Faraday v1's 88° twist drew as a 16° swing and a 28% growth ("the wave got bigger"). Choose the view by
  what the viewer must see (the drawn angle and length over the whole motion, with `amanim.threed.project`), sampled
  finely, and assert it. Optimizing a stand-in (the plane never edge-on) passed and still failed.
- **Phasors and the thing they explain are one computation.** An inset's arrows come from the same function as the
  main picture, at a point it marks, and an `assert` ties them (Faraday's R + L = the wave's own vector at the probe).
  Don't sweep a probe through a fast-varying phase: the arrows spin like an aliased wheel (Faraday v1 at 0:26).
  Change the physics (a density ramp) and keep the probe still.
- **Bind what touches.** Waves meet vertices and particles at a node: `sine_path(pin=)`, which photons in a
  `diagram` get automatically. A packet flows in and out: `wave_packet(absorb_at=, emit_from=)`.
- **What absorbs, shows it.** A particle's glow, size, and whiteness follow the energy it holds:
  - `react(mob, clock, energy_held(steps), at=position_fn, flares=[Flare(t, strength, direction)], hum=0.25,
    gain=show.get_value)`.
  - Build the steps from the physics: `compton_steps` for Compton, which ends at T_e/E₀; for an atom, +E then −E
    after a lifetime.
  - `react` owns position, size, colour, and opacity, so don't add `move_to` or `fader` updaters beside it.
  - `compton-light` is the example.
- **Glow by role:** the subject brightest, actors medium and pulsing out of phase (`pulse(mob, clock, phase=)`),
  structure dim and steady. Pulse the one quantity to read.
- **Overlap transitions:** build the next shot while the last fades (`LaggedStart(FadeOut(old), new, lag_ratio=0.3)`).
- **Check the motion, not just the stills:** a study of your draft (`scripts/watch.py <draft.mp4> --study`) shows
  stops, teleports, and empty frames as drops and spikes in the motion curve. That's how the Compton jank was found.
- **Fewer hues:** one or two accents glowing on near-black, and colour that means something (amber for positive
  charge, blue for negative).
- **His design tokens** live in `brand.py`; mn_style reads them. Use the role names (`ACCENT`, `FLOW`, ...),
  never hex values, and never restyle them. They're his branding.
- **Harvest:** when your scene builds something reusable (a circuit symbol, a kinematics helper), put
  `# harvest: <what it is>` on the line above it.
  - The toolkit lists these marks.
  - A Claude Code session can then move the piece into amanim, so the next scene borrows it instead of
    rewriting it.

## Sound, for an exported video (Aman, 2026-10-25)

/watch clips are silent. A video he posts gets sound, which he adds himself in DaVinci Resolve: regal, majestic, and
ethereal, like orange glowing in space, sunlike, with buzzing energy. A scene makes that easy by saying where the
sounds belong:
- **`self.cue(label, at=, kind=, strength=)`** marks a moment.
  - Cue every amanim `Flare` when it's built: `self.cue("absorb", at=f.at, kind="absorb", strength=f.strength)`.
  - Kinds set the marker's colour: hit or absorb red, emit yellow, move green, reveal cyan.
  - Sections, captions, and the title and credit cards cue themselves.
- **`self.tone(name, fn)`** samples a computed pitch every frame. `fn()` returns a frequency ratio to the base (D4 by
  default), or `(ratio, loudness)`, or None.
  - Make the ratio the physics: a photon's energy over E₀. In `compton-light` the scattered photon sounds a perfect
    fifth below the incoming one at 60° (λ′ = 1.5 λ₀), and the sweep glides it to an octave and a fifth at 180°.
  - Make the loudness what's on screen.
  - `voice="buzz"` makes an ambient bed instead: a faint, harmonic-rich hum with air, around −15 dBFS at most. Give it
    a low base (D2, 73.42 Hz) and a loudness that follows the energy in the scene. In `compton-light` it follows what
    the electron holds. It keeps playing under the end card.
- **`uv run scripts/render.py <scene> --publish`** brings back, beside the video in `exports/`:
  - `<slug>-markers.edl`: in Resolve, Timeline > Import > Timeline Markers from EDL, onto the timeline at
    01:00:00:00;
  - `<slug>-tone-<name>.wav`: one 48 kHz, 24-bit stem per tone, as long as the video, so it lines up at the
    timeline's start.
  - `--kit` writes the same kit for a draft, to check it before the long export.

## House style: `from mn_style import *`

It imports all of manim, plus:

| Name | What it is |
|---|---|
| `MnScene` | Use it instead of `Scene`. It checks the layout at every `next_section()` and at the end (text off the frame, text over text or over a `keep_clear` shape, text below `SMALL`) and reports problems as `layout_warnings` in the status. It also prints `MN-MOTION` when a play froze something that was moving. |
| `self.clock()` | The scene's clock, already added: the time of the frame being drawn. Drive all motion from it. |
| `self.note(text)` | The one caption line at the bottom, replacing the last. Text with `$...$` math. |
| `self.keep_clear(*mobs)` | Shapes text must not cover (a circuit, a surface). |
| `MnScene3D` | `MnScene` with a 3D camera (it is a `ThreeDScene`). Use it for anything 3D; don't mix `MnScene` with `ThreeDScene` yourself. Its `note()` pins the caption to the frame, and the layout check projects labels through the camera. See [3d.md](3d.md). |
| `self.pin(m)` / `self.label(text, at)` / `self.axes(names)` | 3D only: an overlay fixed to the frame (inset, heading, equation); a label at a 3D point that always faces the camera; quiet axes with facing labels. Each is registered, not shown: `FadeIn` it. |
| `self.place(text, at, toward=, avoid=)` | A label at the first clear spot around a point: in the frame, off other text and kept-clear shapes. Works in 2D and 3D. Prefer it to hand-placed offsets. |
| `self.clean(*keep)` | Fade out everything but `keep`, caption included. Call it at the *start* of a section. |
| `MnZoomScene` | `MnScene` on a `MovingCameraScene`: animate `self.camera.frame` to zoom into a detail. Its `note()` and `pin()` keep the caption and overlays the same size on screen while everything else zooms. Thin stroke widths by the zoom factor first. |
| `self.bring_in(build, anim=FadeIn, extra=[...])` | Animate in a live mobject that `build()` redraws every frame. Use it whenever the number of parts can change between frames (field lines crossing a surface); animating an `always_redraw` directly crashes then. |
| `tex(*parts, size=BODY, color=INK)` | `MathTex` in the house size and color. |
| `words(text, size=BODY, color=INK)` | A `Tex` line (prose, `$...$` math inside). |
| `backed(m)` | `m` on a background-colored plate, so grid or field lines don't cut through text. |
| `arrange_rows(rows)` | Equations stacked, left-aligned, the way a derivation reads on paper. |
| Colors (roles) | `GROUND` (background), `INK`, `MUTED`, `GRID`, `ACCENT` (what the clip is about), `FLOW` (anything that flows: fields, beams), `WARM`, `STRUCT` (apparatus, plates), `WARN` (a second actor, violet), and mnemosyne's teaching pair `GOOD` (outward, the answer) and `BAD` (inward, the trap). Fewer hues than roles: see below. |
| Sizes | `TITLE` 48, `BODY` 40, `SMALL` 32 (the smallest; nothing smaller), `SAFE` 0.3 (text keeps this far from the edge). |

## Colors: fewer hues than roles

Rendered with his tokens, `ACCENT` and `WARM` are both orange, and `MUTED` is a grey close to `FLOW`'s blue. `STRUCT`
(1.7:1 against `GROUND`) and `GRID` are too dark to carry a line; use them for fills and background structure. So when
a scene needs several things told apart, the line colors that read as different are **`ACCENT`, `FLOW`, `GOOD`,
`WARN`, `BAD`**, with `INK` for a neutral one. (`WARN` is mnemosyne's own violet since 2026-10-23; before that it was
`FLOW`.) `gallery-palette` shows all of it as rendered, with each role's contrast; look before you choose. Use the role
names, never hex.

## Rules that keep clips good on a phone

- **Write the `CLIP` block** at the top: `CLIP = {"title": ..., "courses": [...], "topics": [...], "watch_for": ...,
  "scene": "ClassName", "look": "ethereal"}` (`look` is optional; see "The glowing look"). The tool's arguments win
  where they differ.
- **Credits for an export:** a clip that may be posted gets `"credits": [...]`: the textbook and section the physics
  comes from, the constants' source, whose work the look was learned from, and "typical values" for illustrative
  numbers. Keep each under about 60 characters, or it wraps.
  - A Claude Code session exports with `scripts/render.py <scene> --publish`. That's 1080p60, with mn_style's title
    card (the CLIP title; the part after a colon becomes the subtitle) and its end card ("every number computed",
    the credits, the tools).
  - The file lands in `exports/`, with the text for the upload form. /watch clips never get the cards.
- **One idea per section,** with `self.next_section("short name")` before each. Name the sections the way he'd name
  the steps.
- **Clean up at the start of a section, never the end.** /watch shows each section's last frame as its still and its
  chapter button. A section that ends on a fade-out shows a blank still, and one that ends by moving the camera back
  shows the view *before* the one it was about. So a section ends on its payoff (the edge-on view, the finished
  equation), and the next one begins with `self.clean()` or the camera move. The check warns when a section's last
  frame is nearly empty, much emptier than the section was, ends on a fade-out, or ends on a camera move back.
- **Text:** no text smaller than `SMALL`; the layout check reports any (including text scaled below it). Glyphs that
  are part of a picture rather than text to read (⊗ field markers, the sign inside a charge) get `.backdrop = True`. Captions stay
  under about 70 characters, or they wrap onto two lines. Use `backed()` for anything over a grid. A bare `%` in
  `words()` is escaped for you (in LaTeX it starts a comment, which failed a render).
- **Layout:** keep the algebra on one side and the picture on the other; don't stack them.
- **Comments say why** a move was made, not what the line does. He reads the code to learn Manim.
- **Updaters and changing counts:** set a number's value before fading it in. A count that goes from 0 to 12 mid-fade
  changes its glyph count and breaks the fade. Call `.update()` after `add_updater`.
- **Notation:** MathJax-style LaTeX in his course's notation, the same as on his cards. For example, Young & Freedman
  writes `\oint\vec E\cdot d\vec A = Q_{enc}/\epsilon_0`.

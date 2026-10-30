"""House style for mnemosyne's Manim scenes, and a layout check that runs at every section break.

Scenes are ManimCE (the version is pinned in scripts/render.py) and start with `from mn_style import *`, which also
brings in all of manim. They render in a sandbox on the Obelisk and play on /watch, mostly on an iPhone. So:

- Colors are Aman's design tokens (brand.py, generated from his helios_tokens.yaml), the same look as his own
  videos. Technique comes from amanim, his library, which every render has beside the scene: `from amanim import ...`.
- Text is never smaller than SMALL. At 720p on a phone held sideways that's about the size of body text.
- Anything that must stay readable gets `backed()`: a ground-colored plate behind it, so grid lines don't cut it.
- `MnScene` checks the layout at each `next_section()` and at the end: text pushed off the frame, and text overlapping
  text or a mobject passed to `keep_clear()`. Problems print as `MN-LAYOUT ...` lines, which render.json collects. The
  stills show the rest.
- It also checks each section's last frame, which /watch shows as the section's still: nearly empty, nothing left but
  the caption, or ended on a fade-out or a camera move back. Clean up at the start of a section, never the end.
- Motion: `self.clock()` is the scene's clock (the drawn frame's own time, so it never stalls at a play), and every
  play is checked for freezing something that was moving: FadeIn/FadeOut pause a mobject's updaters, so it stands
  still and then snaps. Those print as `MN-MOTION ...`, which render.json collects too. Fade moving things through a
  tracker (amanim's `fader`).
- Text below SMALL is reported too, scaled or not.
- Sound: `self.cue(label, at=, kind=, strength=)` marks a moment for a sound (a flare, a hit), and `self.tone(name,
  fn)` samples a computed pitch every frame. Sections, captions, and the cards are cued by themselves. They print as
  `MN-CUE` / `MN-TONE` lines; job.py keeps them in render.json, and `scripts/render.py --publish` turns them into a
  marker EDL for Resolve and one WAV stem per tone (scripts/cues.py).
- `MnScene3D` is the same for a 3D camera. Its caption, `pin()`ned overlays, and `label()`s stay readable however the
  camera turns, and the check runs on what the camera draws (projected), not on world coordinates.

Write scenes as worked examples. Comments say *why* a move was made (why this order, why this mobject, how the layout
stays clear), because Aman learns Manim from them too.

Tests: `uv run --with manim==0.21.0 pytest scenes/tests` (manim isn't one of mnemosyne's own dependencies).
"""

import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from itertools import combinations
from typing import Any, TypeVar

import numpy as np
from manim import *
from manim.animation.animation import prepare_animation
from manim.mobject.mobject import _AnimationBuilder

# ---------- colors: Aman's design tokens, when brand.py sits beside this file ----------
# brand.py is generated from his helios_tokens.yaml (gitignored; every render copies it in). Its PALETTE names roles,
# not colors: bg, accent, ink, ground, flow, warm, muted. Those values are his branding: use them exactly as they
# come, never adjust them here. Without brand.py (a fresh clone), the palette falls back to mnemosyne's own.
try:
    from brand import PALETTE as _TOKENS  # pyright: ignore[reportMissingImports]
except ImportError:
    _TOKENS = {}
_FALLBACK = {"bg": "#0f141b", "ink": "#e3e8ef", "muted": "#97a3b4", "accent": "#8aa4f2", "flow": "#e2a743",
             "warm": "#c6884c", "ground": "#444a54"}
_T = {**_FALLBACK, **_TOKENS}
BRANDED = bool(_TOKENS)

GROUND = ManimColor(_T["bg"])        # background
INK = ManimColor(_T["ink"])          # text and main strokes
MUTED = ManimColor(_T["muted"])      # secondary text, axes
ACCENT = ManimColor(_T["accent"])    # the thing the clip is about
FLOW = ManimColor(_T["flow"])        # anything that flows: fields, beams, currents
WARM = ManimColor(_T["warm"])        # secondary warm
STRUCT = ManimColor(_T["ground"])    # structure: plates, apparatus, the frame of things
GRID = interpolate_color(GROUND, MUTED, 0.3)   # background grid lines: barely there
# Teaching semantics the tokens don't name; mnemosyne's own, kept the same in every look.
# WARN was an alias of FLOW until 2026-10-23, so a second actor couldn't be told from a flow; Aman gave it its own
# violet (8.2:1 on GROUND, CIEDE2000 over 27 from every other role: gallery-palette shows it).
WARN = ManimColor("#c792ea")         # a second actor: the other charge, the other vector
GOOD = ManimColor("#5cc08a")         # outward, positive, the answer
BAD = ManimColor("#f07b72")          # inward, negative, the trap

config.background_color = GROUND

# ---------- sizes ----------
TITLE = 48
BODY = 40
SMALL = 32       # the floor: nothing smaller
SAFE = 0.3       # text keeps this far from the frame's edge

# Drawing order: in a 3D scene Manim draws in the order things were added, so a plane added after a label would cover
# it. Labels go above the picture and pinned overlays above everything.
LABEL_Z = 5
PIN_Z = 10

# ---------- an export's cards ----------
CARDS = os.environ.get("MN_CARDS") == "1"   # set by job.py for `scripts/render.py --publish` only
CHANNEL = ""                                # the channel's name on the title card, once Aman picks one

M = TypeVar("M", bound=Mobject)


def tex(*parts: str, size: float = BODY, color: ManimColor = INK, **kw: object) -> MathTex:
    """Math in the course's notation (MathJax-style LaTeX). Several parts make separately addressable pieces."""
    return MathTex(*parts, font_size=size, color=color, **kw)


def words(text: str, size: float = BODY, color: ManimColor = INK, **kw: object) -> Tex:
    """A line of text, set by LaTeX so it matches the math beside it. Inline math goes in $...$ here. A bare % is
    escaped: in LaTeX it starts a comment, which ate the rest of a caption ("within 1% of ...") and failed the
    render."""
    return Tex(re.sub(r"(?<!\\)%", r"\\%", text), font_size=size, color=color, **kw)


def backed(m: Mobject, opacity: float = 0.88, buff: float = 0.12) -> VGroup:
    """`m` on a ground-colored plate, so a grid or field lines behind it don't cut through the letters."""
    plate = BackgroundRectangle(m, color=GROUND, fill_opacity=opacity, buff=buff)
    return VGroup(plate, m)


# ---------- the layout check ----------

TEXT_TYPES = (DecimalNumber, SingleStringMathTex, Text, MarkupText)
Box = tuple[float, float, float, float]


def _state(m: Mobject) -> np.ndarray:
    """Everything an updater can change that shows: every point of the family, and every opacity."""
    fam = m.family_members_with_points()
    if not fam:
        return np.zeros(0)
    parts = [f.points.ravel() for f in fam]
    parts += [np.array([f.get_fill_opacity(), f.get_stroke_opacity()]) for f in fam if isinstance(f, VMobject)]
    return np.concatenate(parts)


def _leaf_animations(args: Iterable[object]) -> list[Animation]:
    """The animations a play() will run, groups (LaggedStart, Succession) opened up and `.animate` built."""
    out: list[Animation] = []
    for a in args:
        if isinstance(a, Wait) or not isinstance(a, (Animation, _AnimationBuilder)):
            continue
        anim = prepare_animation(a)
        if isinstance(anim, AnimationGroup):
            out.extend(_leaf_animations(anim.animations))
        else:
            out.append(anim)
    return out


def _texts(mobs: Iterable[Mobject]) -> list[Mobject]:
    """The outermost text mobjects under `mobs`: a MathTex counts once, not once per glyph. A group marked
    `backdrop = True` (amanim's depth_plate: faint math out of focus behind everything) is set dressing, not text to
    read, so it's skipped."""
    found: list[Mobject] = []

    def walk(m: Mobject) -> None:
        if getattr(m, "backdrop", False):
            return
        if isinstance(m, TEXT_TYPES):
            found.append(m)
            return
        for sub in m.submobjects:
            walk(sub)

    for m in mobs:
        walk(m)
    return [m for m in found if _visible(m)]


def _visible(m: Mobject) -> bool:
    family = [f for f in m.get_family() if f.has_points()]
    if not family:
        return False
    return any(max(f.get_fill_opacity(), f.get_stroke_opacity()) > 0.05 for f in family if isinstance(f, VMobject))


def _overlap(a: Box, b: Box) -> float:
    ax0, ax1, ay0, ay1 = a
    bx0, bx1, by0, by1 = b
    return max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))


def _meet(a: Box, b: Box) -> bool:
    """Whether two boxes touch at all. Not an area test: a straight horizontal line's box has no height."""
    return a[0] <= b[1] and b[0] <= a[1] and a[2] <= b[3] and b[2] <= a[3]


def _name(m: Mobject) -> str:
    if isinstance(m, DecimalNumber):
        return f"number {m.get_value():g}"
    s = getattr(m, "tex_string", None) or getattr(m, "text", None) or type(m).__name__
    return repr(str(s)[:40])


def _segment_hits(p: np.ndarray, q: np.ndarray, box: Box) -> bool:
    """Whether the segment p-q passes through the box (Liang-Barsky clipping, x and y only)."""
    x0, x1, y0, y1 = box
    dx, dy = q[0] - p[0], q[1] - p[1]
    lo, hi = 0.0, 1.0
    for step, room in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
        if step == 0:
            if room < 0:
                return False
            continue
        t = room / step
        if step < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return False
    return True


def _visible_anywhere(m: Mobject) -> bool:
    return any(f.has_points() and (not isinstance(f, VMobject) or max(f.get_fill_opacity(), f.get_stroke_opacity()) > 0.05)
               for f in m.get_family())


def _related(a: Mobject, b: Mobject) -> bool:
    """Parts of one formula (a matrix's entries, a DecimalNumber's glyphs) may touch."""
    return a in b.get_family() or b in a.get_family()


class MnScene(Scene):
    """A Scene that checks its layout at every section break and at the end (see the module docstring), and asks the
    same check before drawing: `place(text, at, toward=, avoid=)` puts a label at the first clear spot around a point.
    Also `note()` (the one caption), `keep_clear()`, `clean()` (at the start of a section), and `bring_in()`."""

    def setup(self) -> None:
        super().setup()
        self._clear: list[Mobject] = []
        self._caption: Mobject | None = None
        self._section = "start"
        self._begin_section()
        if CARDS:
            self.title_card()

    # -- an export's cards (MN_CARDS=1, set by job.py for `render.py --publish`): /watch's clips never get them --

    def clip_info(self) -> dict[str, Any]:
        """The scene module's CLIP block: the title, the credits."""
        clip = getattr(sys.modules.get(type(self).__module__), "CLIP", None)
        return clip if isinstance(clip, dict) else {}

    def title_card(self) -> None:
        """The opening card: the title (its part before a colon, large; the rest beneath), and the channel's name
        once it has one. It fades out before the scene starts, and its section counts for no still."""
        head, _, rest = str(self.clip_info().get("title") or type(self).__name__).partition(":")
        main = words(head.strip(), size=TITLE + 8)
        half = max(main.width, 3.0) / 2
        line = Line(half * LEFT, half * RIGHT, color=ACCENT, stroke_width=3)
        parts: list[Mobject] = [main, line]
        if rest.strip():
            parts.append(words(rest.strip(), size=BODY, color=MUTED))
        if CHANNEL:
            parts.append(words(CHANNEL, size=SMALL, color=MUTED))
        card = self._on_screen(VGroup(*parts).arrange(DOWN, buff=0.3))
        self.cue(head.strip(), kind="title")
        self.play(FadeIn(card[0], shift=0.15 * UP), Create(card[1]), *(FadeIn(p) for p in card[2:]), run_time=1.2)
        self.wait(1.8)
        self.play(FadeOut(card), run_time=0.6)
        self._begin_section()          # the card's own section ends empty on purpose

    def end_card(self) -> None:
        """The closing card. The scene keeps moving under a cover that fades in (a FadeOut of everything would
        freeze it: MN-MOTION), then: every number computed, the clip's CLIP['credits'], and the tools."""
        cover = FullScreenRectangle(fill_color=GROUND, fill_opacity=0.0, stroke_width=0)
        cover = self._on_screen(cover).set_z_index(PIN_Z + 1)
        rows: list[Mobject] = [words("Every number in this clip is computed,", size=BODY),
                               words("and checked against an independent result.", size=BODY)]
        credits = [str(c) for c in self.clip_info().get("credits") or []]
        if credits:
            rows.append(words("Sources and credits", size=SMALL + 4, color=ACCENT))
            rows += [words(c, size=SMALL, color=MUTED) for c in credits]
        rows.append(words("Made with Manim Community Edition and amanim", size=SMALL, color=MUTED))
        card = VGroup(*rows).arrange(DOWN, buff=0.22)
        card[2:].shift(0.25 * DOWN)
        card = self._on_screen(card.move_to(ORIGIN)).set_z_index(PIN_Z + 2)
        self.add(cover)
        self.play(cover.animate.set_fill(opacity=1.0), run_time=0.8)
        self.remove(*[m for m in self.mobjects if m is not cover          # hidden now: nothing under it is drawn,
                      and not getattr(m, "mn_sound", False)])              # but the sound keeps sampling
        self._caption = None
        self.play(FadeIn(card, shift=0.1 * UP), run_time=0.8)
        self.wait(4.5)

    def _begin_section(self) -> None:
        self._plays = 0
        self._ink_peak = 0.0
        self._ink_last: float | None = None
        self._ink_before_last: float | None = None
        self._last_kind = "none"
        self._placed: list[Mobject] = []

    # -- automatic label placement: the layout check, asked before a label is drawn instead of after --

    def place(self, text: str | Mobject, at: Sequence[float] | np.ndarray, color: ManimColor = INK,
              size: float = SMALL, toward: Sequence[float] | np.ndarray | None = None, plate: bool = False,
              avoid: Sequence[Mobject] = (), gap: float = 0.15) -> Mobject:
        """A label beside the point `at`, at the first spot around it that the layout check would call clear: inside
        the frame, off every text on screen (and every label placed this section, shown or not), and off every
        kept-clear shape and anything in `avoid`.

        Spots are tried in rings around the point, `gap` from it and then further out. In each ring it starts on
        the screen direction of `toward` (a vector's own direction puts its label past the tip) or on the right,
        then turns 45 degrees at a time. When no spot is clear it keeps the least bad one, and the layout check
        reports it. In 3D the spot is judged from the camera as it is now: place labels after the camera is set,
        and again for a view that changes them. Returns the label unshown, like `label()`: FadeIn it."""
        lab = self._new_label(text, at, color, size, plate)
        anchor = np.asarray(at, dtype=float)
        right, up = self._screen_basis()
        others = [self.screen_box(t) for t in _texts([*self.mobjects, *self._placed])
                  if t not in lab.get_family()]
        shown = {id(f) for top in self.mobjects for f in top.get_family()}
        shapes = [p for c in self._clear for p in ([c] if id(c) in shown else c.submobjects) if id(p) in shown]
        shapes += list(avoid)
        base = 0.0
        if toward is not None:
            s = self._screen_direction(np.asarray(toward, dtype=float))
            if np.linalg.norm(s) > 1e-6:
                base = float(np.arctan2(s[1], s[0]))
        lab.move_to(anchor)
        x0, x1, y0, y1 = self.screen_box(lab)
        w, h = x1 - x0, y1 - y0
        best: tuple[float, np.ndarray] | None = None
        for dist in (gap, gap + 0.35, gap + 0.8):
            for k in (0, 1, -1, 2, -2, 3, -3, 4):
                ang = base + k * PI / 4
                u = np.array([np.cos(ang), np.sin(ang)])
                reach = dist + abs(u[0]) * w / 2 + abs(u[1]) * h / 2   # center so the box's edge is `dist` away
                spot = anchor + reach * (u[0] * right + u[1] * up)
                lab.move_to(spot)
                cost = self._placement_cost(self.screen_box(lab), others, shapes, lab)
                if cost == 0:
                    self._placed.append(lab)
                    return lab
                if best is None or cost < best[0]:
                    best = (cost, spot)
        assert best is not None
        lab.move_to(best[1])
        self._placed.append(lab)
        return lab

    def _new_label(self, text: str | Mobject, at: Sequence[float] | np.ndarray, color: ManimColor, size: float,
                   plate: bool) -> Mobject:
        lab: Mobject = tex(text, size=size, color=color) if isinstance(text, str) else text
        return (backed(lab) if plate else lab).move_to(np.asarray(at, dtype=float)).set_z_index(LABEL_Z)

    def _screen_basis(self) -> tuple[np.ndarray, np.ndarray]:
        """World vectors that move a label one frame unit right and one up on screen. MnScene3D projects."""
        frame = getattr(self.renderer.camera, "frame", None)
        s = frame.width / config.frame_width if frame is not None else 1.0
        return s * RIGHT, s * UP

    def _screen_direction(self, v: np.ndarray) -> np.ndarray:
        return v[:2]

    def _placement_cost(self, box: Box, others: list[Box], shapes: Sequence[Mobject], lab: Mobject) -> float:
        """0 when the spot is clear; otherwise how bad it is: off the frame, then on text, then across shapes."""
        x0, x1, y0, y1 = box
        half_w, half_h = config.frame_width / 2 - SAFE, config.frame_height / 2 - SAFE
        cost = 10 * (max(0.0, -half_w - x0) + max(0.0, x1 - half_w) + max(0.0, -half_h - y0) + max(0.0, y1 - half_h))
        cost += sum(_overlap(box, o) for o in others)
        cost += sum(0.5 for s in shapes if s not in lab.get_family() and _meet(box, self.screen_box(s))
                    and self._crosses(box, s))
        return cost

    def keep_clear(self, *mobs: Mobject) -> None:
        """Text must not overlap these (a circuit, a surface): checked like text against text."""
        self._clear.extend(mobs)

    def note(self, text: str, run_time: float = 0.6) -> None:
        """Set the caption line at the bottom, replacing the last one. One caption at a time keeps the eye free."""
        new = self._on_screen(backed(words(text, size=SMALL + 4, color=MUTED)).to_edge(DOWN, buff=SAFE))
        self.cue(text, kind="caption")                  # raw TeX: scripts/cues.py makes the marker's name plain
        if self._caption is None:
            self.play(FadeIn(new, shift=0.2 * UP), run_time=run_time)
        else:
            self.play(FadeOut(self._caption), FadeIn(new), run_time=run_time)
        self._caption = new

    def clean(self, *keep: Mobject, run_time: float = 0.6) -> None:
        """Fade out everything but `keep`, caption included. Call it at the START of a section, never at the end:
        /watch shows each section's last frame as its still, and a section that ends on a fade-out shows nothing."""
        kept = {f for k in keep for f in k.get_family()}
        gone = [m for m in self.mobjects if m not in kept and any(f.has_points() for f in m.get_family())]
        if gone:
            self.play(*(FadeOut(m) for m in gone), run_time=run_time)
        if self._caption is not None and self._caption not in kept:
            self._caption = None
        self._clear = [c for c in self._clear if c in kept]
        self._placed = [p for p in self._placed if p in kept]

    def _zoom(self) -> float:
        """World units per screen unit: 1 unless the frame is zoomed (MnZoomScene)."""
        return 1.0

    def _on_screen(self, m: M) -> M:
        """Ready `m` to be drawn in screen space. A 2D camera already draws that way; MnScene3D pins it."""
        return m

    def bring_in(self, *builds: Callable[[], Mobject], anim: type[Animation] = FadeIn,
                 extra: Sequence[Animation] = (), **kw: object) -> list[Mobject]:
        """Animate live mobjects in: each `build()` is redrawn every frame from then on (always_redraw).

        Animating a live mobject directly fails when its number of parts changes between frames (field lines that
        cross a surface once, then three times): an animation pairs up parts when it starts. So animate a snapshot,
        then swap the live one in."""
        snapshots = [b() for b in builds]
        self.play(*(anim(s) for s in snapshots), *extra, **kw)  # pyright: ignore[reportArgumentType]
        live = [always_redraw(b) for b in builds]
        self.remove(*snapshots)
        self.add(*live)
        return live

    def next_section(self, name: str = "unnamed", *args: object, **kwargs: object) -> None:
        self.check_layout()
        self.check_still()
        self._section = name
        self._begin_section()
        self.cue(name, kind="credits" if CARDS and name == "credits" else "section")   # the end card's own section
        super().next_section(name, *args, **kwargs)  # pyright: ignore[reportArgumentType]

    def tear_down(self) -> None:
        if CARDS:
            self.next_section("credits")   # checks the scene's last section first, then the card's
            self.end_card()
        self.check_layout()
        self.check_still()
        super().tear_down()

    # -- the still: each section's last frame is what /watch shows for it --

    def play(self, *args: Any, **kwargs: Any) -> None:
        frozen = self.frozen_candidates(args)
        before = self._ink_last
        super().play(*args, **kwargs)
        self.report_frozen(frozen)
        moves = [a for a in args if not isinstance(a, Wait)]
        if moves:   # a wait doesn't change what kind of ending this is
            self._last_kind = "fade-out" if all(isinstance(a, FadeOut) for a in moves) else "other"
            self._ink_before_last = before
        ink = self._ink()
        if ink is not None:
            self._plays += 1
            self._ink_last = ink
            self._ink_peak = max(self._ink_peak, ink)

    # -- motion: one clock, and an animation pauses the updaters of what it animates --

    def clock(self) -> ValueTracker:
        """The scene's clock, added to the scene: its value is the time of the frame being drawn. Drive everything
        that moves physically from it (positions, phases, pulses).

        Not the usual `tracker.add_updater(lambda m, dt: m.increment_value(dt))`: Manim steps each play through
        t = 0, 1/fps, ..., so every play's first frame comes with dt = 0. A dt clock then repeats a frame at every
        play, wait and caption, and loses 1/fps of physics each time (study mode saw it as a dip in the motion curve
        at each one, in the second Faraday draft). The renderer's own time counts the frames written, so this one
        advances exactly 1/fps per frame.

        The updater takes dt (and ignores it) on purpose: Manim renders a wait() as one frozen frame unless some
        mobject has a time-based updater (Scene.should_update_mobjects), so a clock without one froze every wait
        (the first draft of this clock did, in all four clips)."""
        # It starts at the renderer's time, not 0: an export's title card has already run before construct(), so a
        # scene that plans ahead from clock.get_value() (compton-light's photon timeline) would otherwise plan 3.6 s
        # in the past, and everything driven by it would jump at the first frame.
        c = ValueTracker(self.renderer.time)
        c.add_updater(lambda m, dt: m.set_value(self.renderer.time))
        self.add(c)
        return c

    # -- sound: moments and pitches for an exported video's sound, kept by job.py and used by render.py --publish --

    def cue(self, label: str, at: float | None = None, kind: str = "hit", strength: float = 1.0) -> None:
        """Mark a moment that wants a sound: a flare, a hit, a reveal. `at` is on the scene clock (the video's own
        time, title card included; default now), so a moment computed ahead (an amanim `Flare`) can be cued when it's
        built. `kind` picks the marker's colour in Resolve (hit, absorb, emit, move, reveal; section, caption, title
        and credits are cued by themselves), and `strength` (an energy fraction, like a Flare's) goes in its name so
        the sound can be sized to it."""
        t = self.renderer.time if at is None else float(at)
        print("MN-CUE " + json.dumps({"t": round(t, 4), "kind": kind, "label": str(label)[:160],
                                      "strength": round(float(strength), 3)}), flush=True)

    def tone(self, name: str, fn: Callable[[], float | tuple[float, float] | None], base_hz: float = 293.66,
             voice: str = "tone") -> None:
        """A computed pitch, sampled every frame, for a WAV stem: `fn()` returns a frequency ratio (to `base_hz`), or
        (ratio, loudness 0-1), or None for silence. Make the ratio the physics (a photon's energy over E₀) and the
        loudness what's on screen (how much of the packet is still in frame), so what's heard is as computed as
        what's seen. The default base is D4 (293.66 Hz); retune it in Resolve if the bed is in another key.
        `voice="buzz"` makes it an ambient bed instead (a faint harmonic hum and air, scripts/cues.py), for the
        energy in the scene: give it a low base (D2, 73.42 Hz) and a loudness that follows what's held. It keeps
        sounding under the end card."""
        print("MN-TONE-TRACK " + json.dumps({"name": str(name)[:40], "base_hz": float(base_hz), "voice": voice}),
              flush=True)

        def sample(m: Mobject, dt: float) -> None:
            v = fn()
            ratio, amp = (None, 0.0) if v is None else (v if isinstance(v, tuple) else (v, 1.0))
            if ratio is not None and not (math.isfinite(ratio) and ratio > 0):
                ratio, amp = None, 0.0
            print("MN-TONE " + json.dumps([str(name)[:40], round(self.renderer.time, 4),
                                           None if ratio is None else round(float(ratio), 6),
                                           round(float(amp), 4)]), flush=True)

        probe = Mobject()              # no points: never drawn, never checked; it's only here for its updater
        probe.mn_sound = True          # the end card keeps it, so a bed plays on under the credits
        probe.add_updater(sample)
        self.add(probe)

    # -- motion: an animation pauses the updaters of what it animates --

    def frozen_candidates(self, args: Sequence[object]) -> list[tuple[str, Mobject, np.ndarray]]:
        """Animations about to freeze something with updaters, each with a snapshot of it. Manim suspends the
        updaters of an animation's mobject until it ends (Animation.begin/finish), so a moving thing brought in with
        FadeIn stands still for the fade, then snaps to where its clock says it should be (the first Faraday clip:
        the wave at 1.6 s, the phasors at 7.8 s, the electrons at 17 s)."""
        out = []
        for anim in _leaf_animations(args):
            mob = getattr(anim, "mobject", None)
            if mob is None or not getattr(anim, "suspend_mobject_updating", False) or isinstance(mob, ValueTracker):
                continue
            if any(f.updaters for f in mob.get_family()):
                ghost = mob.copy()                  # never animated: its updaters say where the real one should be
                ghost.update(0)
                out.append((type(anim).__name__, ghost, _state(ghost)))
        return out

    def report_frozen(self, frozen: list[tuple[str, Mobject, np.ndarray]]) -> None:
        """Report the frozen ones that were alive: run each one's un-animated copy forward over the play, and if its
        updaters changed it (moved, redrew, re-lit), the real one stood still for the animation and then snapped.
        One that would have sat still anyway froze invisibly: not reported. The fix: fade it through a tracker
        (amanim's `fader`, `pulse(..., gain=)`, or a tracker inside the always_redraw function)."""
        for name, ghost, before in frozen:
            ghost.update(getattr(self, "duration", 0.0))   # dt updaters (a drift) need the play's length
            after = _state(ghost)
            if after.shape != before.shape or np.abs(after - before).max() > 1e-3:
                print(f"MN-MOTION [{self._section}] {name} froze a {type(ghost).__name__} that was moving: it stood "
                      "still for the animation, then snapped. Fade it through a tracker its updater reads (fader)",
                      flush=True)

    def _ink(self) -> float | None:
        """How much of the frame just drawn isn't background: a share of pixels, 0 to 1."""
        if self.renderer.skip_animations:
            return None
        px = getattr(self.renderer.camera, "pixel_array", None)
        if px is None:
            return None
        sub = np.asarray(px[::6, ::6, :3], dtype=np.int16)   # every 6th pixel is plenty to judge emptiness
        bg = np.array(ManimColor(config.background_color).to_int_rgb(), dtype=np.int16)
        return float((np.abs(sub - bg).max(axis=2) > 24).mean())

    def still_problems(self) -> list[str]:
        """What's wrong with the section's last frame as its still, if anything."""
        final, peak, before = self._ink_last, self._ink_peak, self._ink_before_last
        if not self._plays or final is None:
            return []
        problems: list[str] = []
        # Pixels for "empty" (a text-only still is about 1% ink, a blank one 0), structure for "only the caption".
        # A share of the section's fullest frame would be wrong: an edge-on plane is a line, and that's the point.
        shown = [m for m in self.mobjects if m is not self._caption and _visible_anywhere(m)]
        if final < 0.003:
            problems.append("the still is nearly empty")
        elif not shown and self._caption is not None and peak > final:
            problems.append("the still shows only the caption")
        if self._last_kind == "fade-out" and before and final < 0.75 * before:
            problems.append(f"the section ends on a fade-out that clears {1 - final / before:.0%} of the frame")
        return problems

    def check_still(self) -> None:
        for p in self.still_problems():
            print(f"MN-LAYOUT [{self._section}] {p}: clean up at the start of the next section, not the end of this "
                  "one", flush=True)

    # -- where things are on screen: every check below runs on what the camera draws --

    def screen_points(self, m: Mobject, points: np.ndarray | None = None) -> np.ndarray:
        """`m`'s points (or `points`, taken as m's) as the camera draws them, in frame units with (0, 0) at the
        center. A 3D camera projects them, except pinned mobjects (already screen-space) and labels (moved to their
        projected center, unrotated); a moving camera's frame pans and zooms them."""
        cam: Any = self.renderer.camera
        if hasattr(cam, "reset_rotation_matrix"):   # a 3D camera caches its rotation until it draws a frame
            cam.reset_rotation_matrix()
        pts = cam.transform_points_pre_display(m, m.points if points is None else points)
        frame = getattr(cam, "frame", None)
        if frame is not None:   # MovingCamera: the frame is a rectangle in the scene; scale it to the full frame
            pts = (pts - frame.get_center()) * (config.frame_width / frame.width)
        return pts

    def screen_box(self, m: Mobject) -> Box:
        """The box `m` covers on screen: x0, x1, y0, y1 in frame units."""
        parts = [self.screen_points(f) for f in m.get_family() if f.has_points()]
        if not parts:
            c = m.get_center()
            return c[0], c[0], c[1], c[1]
        pts = np.vstack(parts)
        return float(pts[:, 0].min()), float(pts[:, 0].max()), float(pts[:, 1].min()), float(pts[:, 1].max())

    def _crosses(self, box: Box, shape: Mobject) -> bool:
        """Whether any point of the shape's outline, as drawn, lies inside the text's box (a box overlap alone is too
        coarse: a label inside a loop overlaps the loop's box without touching its wire)."""
        s = np.linspace(0, 1, 9)
        for f in shape.get_family():
            if isinstance(f, VMobject) and f.has_points():
                # Sample along each Bézier (its anchors alone can miss a bend), project the samples, and test the
                # segments between them against the box: points alone step right over a small label on a long line.
                for curve in f.get_curve_functions():
                    pts = self.screen_points(f, np.array([curve(t) for t in s]))
                    if any(_segment_hits(pts[i], pts[i + 1], box) for i in range(len(pts) - 1)):
                        return True
        return False

    def check_layout(self) -> None:
        texts = _texts(self.mobjects)
        boxes = {id(t): self.screen_box(t) for t in texts}
        half_w, half_h = config.frame_width / 2 - SAFE + 0.05, config.frame_height / 2 - SAFE + 0.05
        for t in texts:
            x0, x1, y0, y1 = boxes[id(t)]
            if x0 < -half_w or x1 > half_w or y0 < -half_h or y1 > half_h:
                print(f"MN-LAYOUT [{self._section}] off the frame: {_name(t)}", flush=True)
            size = getattr(t, "font_size", None)       # tracks scaling: a 40 scaled by 0.8 is 32
            size = None if size is None else size / self._zoom()   # as drawn: a zoomed-in frame enlarges text
            if size is not None and size < SMALL - 0.5:
                print(f"MN-LAYOUT [{self._section}] text below the SMALL floor ({size:.0f} < {SMALL}): {_name(t)}",
                      flush=True)
        for a, b in combinations(texts, 2):
            if _overlap(boxes[id(a)], boxes[id(b)]) > 0.01 and not _related(a, b):
                print(f"MN-LAYOUT [{self._section}] text overlaps text: {_name(a)} x {_name(b)}", flush=True)
        # A kept-clear shape counts by whichever of its parts are on screen: a group is often animated in part by part
        # (split_parallelogram's arrows, then its dashed sides), so the group itself is never added to the scene.
        shown = {id(f) for top in self.mobjects for f in top.get_family()}
        for c in self._clear:
            parts = [p for p in c.submobjects if id(p) in shown] if id(c) not in shown else [c]
            if not parts:
                continue
            for t in texts:
                if t in c.get_family():
                    continue
                for p in parts:
                    if _meet(boxes[id(t)], self.screen_box(p)) and self._crosses(boxes[id(t)], p):
                        print(f"MN-LAYOUT [{self._section}] text overlaps a kept-clear shape: {_name(t)}", flush=True)
                        break


class MnScene3D(MnScene, ThreeDScene):
    """MnScene with a 3D camera. Everything MnScene does, plus what a tilted camera breaks:

    - `note()` pins its caption to the frame, so it doesn't lie in the floor in perspective;
    - `pin(m)` does the same for any overlay: an inset diagram, a heading, an equation;
    - `label(text, at)` puts a label at a 3D point that always faces the camera;
    - `axes(names)` is a quiet set of axes with facing labels.

    The camera starts at phi=0, theta=-90 degrees, which is the flat 2D view, so a 2D section needs nothing special.
    Compute views with amanim.threed (`oblique_view`, `edge_on_view`) instead of guessing angles: a guessed angle can
    lie inside a plane and draw it as a line."""

    # MRO: MnScene3D -> MnScene -> ThreeDScene -> Scene. MnScene defines no __init__, so ThreeDScene's runs and sets
    # camera_class=ThreeDCamera; MnScene's setup, next_section, and tear_down call super(), which reaches ThreeDScene
    # and Scene. scenes/tests checks that the camera really is a ThreeDCamera.

    def pin(self, m: M, *more: Mobject) -> M:
        """Fix overlays to the frame (they ignore the camera) and draw them above everything. They're registered,
        not shown: animate them in (FadeIn) or `add` them. Returns the first, so `g = self.pin(diagram)` reads.
        A pinned DecimalNumber may change its digits: parts it grows later are pinned too (`update_mobjects`)."""
        for x in (m, *more):
            x.set_z_index(PIN_Z)
            self._pinned.append(x)
        self.add_fixed_in_frame_mobjects(m, *more)
        self.remove(m, *more)
        return m

    def update_mobjects(self, dt: float) -> None:
        """After the updaters run, register any parts they built: the camera fixes mobjects one by one, so a
        DecimalNumber's new digits would otherwise be drawn in 3D, in perspective, somewhere else."""
        super().update_mobjects(dt)
        cam: Any = self.renderer.camera
        for root in self._pinned:
            cam.fixed_in_frame_mobjects.update(root.get_family())
        for root in self._labels:
            if root not in cam.fixed_orientation_mobjects:
                continue
            center = cam.fixed_orientation_mobjects[root]
            for f in root.get_family():
                cam.fixed_orientation_mobjects.setdefault(f, center)

    def _on_screen(self, m: M) -> M:
        return self.pin(m)

    def _view(self) -> tuple[float, float, float]:
        cam: Any = self.renderer.camera
        return cam.get_phi(), cam.get_theta(), cam.get_zoom()

    def setup(self) -> None:
        self._pinned: list[Mobject] = []
        self._labels: list[Mobject] = []
        super().setup()

    def _begin_section(self) -> None:
        super()._begin_section()
        self._view0 = self._view()
        self._camera_moved = False

    def move_camera(self, *args: Any, **kwargs: Any) -> None:
        before = self._view()
        super().move_camera(*args, **kwargs)
        if not np.allclose(before, self._view(), atol=1e-3):
            self._camera_moved = True
            self._last_kind = "camera"

    def still_problems(self) -> list[str]:
        problems = super().still_problems()
        cam: Any = self.renderer.camera
        if any(t.updaters for t in (cam.phi_tracker, cam.theta_tracker)):
            problems.append("the camera is still turning when the section ends (stop the ambient rotation first)")
        elif self._last_kind == "camera" and self._camera_moved and np.allclose(self._view(), self._view0, atol=1e-3):
            problems.append("the section ends on a camera move back to where it began, so the still misses the view "
                            "it was about")
        return problems

    def _new_label(self, text: str | Mobject, at: Sequence[float] | np.ndarray, color: ManimColor, size: float,
                   plate: bool) -> Mobject:
        return self.label(text, at, color=color, size=size, plate=plate)

    def _screen_basis(self) -> tuple[np.ndarray, np.ndarray]:
        # The camera's rotation takes world to screen: its first two rows are the world directions of screen
        # right and up. A facing label is drawn at its projected center, so a world offset along them moves it on
        # screen by about offset * zoom (perspective changes that a little; place() checks the drawn box anyway).
        cam: Any = self.renderer.camera
        cam.reset_rotation_matrix()
        rot = cam.get_rotation_matrix()
        return rot[0] / cam.get_zoom(), rot[1] / cam.get_zoom()

    def _screen_direction(self, v: np.ndarray) -> np.ndarray:
        cam: Any = self.renderer.camera
        cam.reset_rotation_matrix()
        return (cam.get_rotation_matrix() @ v)[:2]

    def label(self, text: str | Mobject, at: Sequence[float] | np.ndarray, color: ManimColor = INK,
              size: float = SMALL, plate: bool = False) -> Mobject:
        """A label at the 3D point `at` that always faces the camera (it moves with the point, never tilts).
        `plate=True` backs it, for a label over a plane's grid. Registered, not shown, like `pin`."""
        lab: Mobject = tex(text, size=size, color=color) if isinstance(text, str) else text
        if plate:
            lab = backed(lab)
        lab.move_to(np.asarray(at, dtype=float)).set_z_index(LABEL_Z)
        self.add_fixed_orientation_mobjects(lab)
        self.remove(lab)
        self._labels.append(lab)
        return lab

    def axes(self, names: Sequence[str] = ("x", "y", "z"), extent: float = 3.0,
             color: ManimColor = MUTED) -> tuple[VGroup, VGroup]:
        """Axes through the origin (one unit is one frame unit) and a facing label past each positive end. The
        labels come back separately: fade out the ones a view stacks on top of each other (edge-on views do that).

        They're amanim's `axes_3d`, three plain arrows: ManimCE's ThreeDAxes costs about nine times as much to
        draw while the camera moves. Build a ThreeDAxes yourself when you need its c2p (a plotted surface)."""
        from amanim import axes_3d  # beside every render; imported here so mn_style loads without it

        ax = axes_3d(extent, color=color)
        labels = VGroup(*(self.label(n, (extent + 0.4) * d, color=color) for n, d in zip(names, (RIGHT, UP, OUT),
                                                                                         strict=True)))
        return ax, labels


def _follower(frame: Mobject) -> Callable[[Mobject], None]:
    """An updater that keeps a mobject fixed to a moving camera's frame. A closure, not a method: Manim deep-copies a
    mobject's updaters when it copies the mobject (FadeOut does), and a method would drag the scene along."""

    def follow(x: Mobject) -> None:
        s = frame.width / config.frame_width
        x.scale(s / x.mn_scale)  # pyright: ignore[reportAttributeAccessIssue]
        x.mn_scale = s  # pyright: ignore[reportAttributeAccessIssue]
        x.move_to(frame.get_center() + s * x.mn_offset)  # pyright: ignore[reportAttributeAccessIssue]

    return follow


class MnZoomScene(MnScene, MovingCameraScene):
    """MnScene with a camera that pans and zooms (`self.camera.frame.animate.scale(0.4).move_to(detail)`).

    A moving camera zooms everything in the scene, the caption included, so `note()` and `pin(m)` keep their
    mobjects fixed to the frame: an updater rescales and moves each one with `camera.frame` every frame. Place a
    pinned mobject where it belongs on the full frame, before the zoom; the layout check measures it there."""

    def pin(self, m: M, *more: Mobject) -> M:
        """Fix overlays to the frame: they stay the same size and place on screen however the camera zooms. They're
        returned unshown, like MnScene3D's: animate them in."""
        frame = self.camera.frame
        follow = _follower(frame)
        for x in (m, *more):
            s = frame.width / config.frame_width
            # Where it sits and how big it is on the full, unzoomed frame; the updater keeps it there on screen.
            x.mn_offset = (x.get_center() - frame.get_center()) / s  # pyright: ignore[reportAttributeAccessIssue]
            x.mn_scale = s  # pyright: ignore[reportAttributeAccessIssue]
            x.set_z_index(PIN_Z)
            x.add_updater(follow)
        return m

    def _zoom(self) -> float:
        return self.camera.frame.width / config.frame_width

    def _on_screen(self, m: M) -> M:
        # The caption is built for the full frame; scale it to the frame as it is now, then keep it there.
        frame = self.camera.frame
        s = frame.width / config.frame_width
        m.scale(s, about_point=ORIGIN).shift(frame.get_center())
        return self.pin(m)


def arrange_rows(rows: Sequence[Mobject], buff: float = 0.35) -> VGroup:
    """Equations stacked and aligned on their left edges: how a derivation reads on paper."""
    return VGroup(*rows).arrange(DOWN, aligned_edge=LEFT, buff=buff)

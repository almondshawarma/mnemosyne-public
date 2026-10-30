"""Helmholtz coils: where the field is flat.

Channel series 2 ("Instruments I built"): the prediction Aman's coil-and-magnetometer rig will test.

What's computed (amanim.magnetostatics, tested in amanim's tests/test_blocks.py against the textbook loop field, the
Helmholtz centre field, the 144/125 fourth-order droop, and ∇·B = 0):
- B everywhere in the cross-section, by the Biot–Savart law summed over each coil's 128 straight pieces.
- The field lines: streamlines of B (amanim.fields.field_lines, RK4 on the unit direction).
- The on-axis profile B(z)/B(0) and the region where |B| is within 1% of the centre's, for every spacing as it slides.
- The rig's numbers (R = 15 cm, 50 turns, 1 A), in SI with CODATA μ0.

Units on screen: the coil radius R is 1, and the current is scaled by 1/μ0 so B is order 1 (only shapes and ratios
are drawn; the SI numbers come from the same function with real units).

Motion rule: everything that changes while it's shown is an always_redraw read from trackers, and fades through a
tracker, never FadeIn (which pauses updaters: see mn_style's MN-MOTION check).
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from amanim import field_lines, glow, halo, pulse_factor, settle
from amanim.magnetostatics import MU0, biot_savart, helmholtz, loop
from amanim.plots import plot_axes
from mn_style import *

CLIP = {
    "title": "Helmholtz coils: where the field is flat",
    "courses": ["phys207"],
    "topics": ["physics::em::magnetism", "physics::em::field-lines", "principle::superposition"],
    "watch_for": "Slide the coils together and apart: too far, the field dips in the middle; too close, it peaks. At "
                 "a spacing equal to the radius the curvature of the two loops' fields cancels, and the green region "
                 "where B is within 1% of its centre value is largest.",
    "scene": "Helmholtz",
    "look": "ethereal",
}

# ---- the physics, in coil radii ----
N_SEG = 128
UNIT = 1 / MU0                     # current scaled so B is order 1 on screen
ZS = np.linspace(-1.5, 1.5, 241)   # the axis, for the profile


def coils_at(spacing: float) -> list[np.ndarray]:
    return helmholtz(1.0, spacing=spacing, n=N_SEG)


def b_cross(spacing: float | None, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """B in the cross-section through the axis, as (B along the axis, B across it): screen x is the axis z, screen y
    is the radial direction. One loop when spacing is None."""
    pts = np.stack([y, np.zeros_like(x), x], axis=-1)
    coils = [loop(1.0, n=N_SEG)] if spacing is None else coils_at(spacing)
    b = biot_savart(pts.reshape(-1, 3), coils, current=UNIT).reshape(*pts.shape)
    return np.stack([b[..., 2], b[..., 0]], axis=-1)


def profile(spacing: float | None) -> np.ndarray:
    """B_z on the axis over ZS, divided by its value at the centre."""
    b = b_cross(spacing, ZS, np.zeros_like(ZS))[:, 0]
    return b / b[len(b) // 2]


def flat_half_width(spacing: float | None, tol: float = 0.01) -> float:
    """How far along the axis from the centre B stays within `tol` of its centre value."""
    p = profile(spacing)
    mid = len(ZS) // 2
    off = np.abs(p[mid:] - 1) > tol
    return float(ZS[mid + int(np.argmax(off))]) if off.any() else float(ZS[-1])


# A check the scene relies on: the flat region is widest at the Helmholtz spacing.
_W = {s: flat_half_width(s) for s in (0.8, 1.0, 1.2)}
assert _W[1.0] > _W[0.8] and _W[1.0] > _W[1.2], _W

# ---- the rig, in SI: R = 15 cm, 50 turns, 1 A ----
RIG_R, RIG_N, RIG_I = 0.15, 50, 1.0
RIG_B0 = float(biot_savart((0, 0, 0), helmholtz(RIG_R, n=512), current=RIG_I, turns=RIG_N)[2])
RIG_FLAT_CM = 100 * RIG_R * flat_half_width(1.0)

# ---- the screen: the cross-section on the left, the profile on the right ----
S, C = 1.35, np.array([-3.25, -0.15, 0.0])    # screen units per coil radius, and where the centre sits
GX, GY = np.linspace(-1.25, 1.25, 71), np.linspace(-1.05, 1.05, 61)
MX, MY = np.meshgrid(GX, GY)


def to_screen(x, y) -> np.ndarray:
    return C + S * np.array([x, y, 0.0])


# harvest: closed contour paths of a gridded scalar at one level (matplotlib's engine, no figure shown)
def contour_paths(X: np.ndarray, Y: np.ndarray, Z: np.ndarray, level: float) -> list[np.ndarray]:
    fig, ax = plt.subplots()
    cs = ax.contour(X, Y, Z, levels=[level])
    segs = [np.asarray(s) for s in cs.allsegs[0] if len(s) > 2]
    plt.close(fig)
    return segs


class Helmholtz(MnScene):
    def construct(self) -> None:
        clock = self.clock()             # the wires' glow breathes on it: the picture stays alive in holds
        spacing = ValueTracker(1.6)
        two = ValueTracker(0.0)          # 0: one loop; 1: both
        show_live = ValueTracker(0.0)    # the profile
        show_flat = ValueTracker(0.0)    # the flat region, in both views

        # ---- the cross-section's frame: the axis, and the profile's axes ----
        axis_line = DashedLine(to_screen(-1.3, 0), to_screen(1.3, 0), color=MUTED, stroke_width=1.5,
                               stroke_opacity=0.5, dash_length=0.08)
        ax = plot_axes((-1.5, 1.5, 0.5), (0.2, 1.3, 0.2), x_length=5.0, y_length=3.0, color=MUTED,
                       labels=(r"z/R", r"B/B_0"), label_size=SMALL).move_to((3.55, -0.6, 0))   # room above for text
        one_line = DashedLine(ax.c2p(-1.5, 1.0), ax.c2p(1.5, 1.0), color=MUTED, stroke_width=1.2,
                              stroke_opacity=0.5, dash_length=0.06)

        def wires() -> VGroup:
            """Each coil's two crossings of the cross-section: current out of the screen at the top (⊙), into it
            at the bottom (⊗), glowing like current."""
            g = VGroup()
            zs = [0.0] if two.get_value() < 1e-3 else [-spacing.get_value() / 2, spacing.get_value() / 2]
            fade = [1.0] if len(zs) == 1 else [1.0, 1.0]
            if 0 < two.get_value() < 1:          # the second coil arrives: the first slides from 0 to its place
                s = spacing.get_value() * two.get_value()
                zs, fade = [-s / 2, s / 2], [1.0, two.get_value()]
            for z, op in zip(zs, fade, strict=True):
                for y, out in ((1.0, True), (-1.0, False)):
                    p = to_screen(z, y)
                    k = pulse_factor(clock.get_value(), rate=0.5, depth=0.35, phase=2.0 * z + y)
                    g.add(glow(p, FLOW, radius=0.09, layers=5, max_op=0.22 * op * k, spread=2.2))
                    g.add(Circle(radius=0.11, color=FLOW, stroke_width=2.5, stroke_opacity=op).move_to(p))
                    mark = Dot(p, radius=0.03, color=FLOW, fill_opacity=op) if out else VGroup(
                        Line(p + 0.06 * (UL), p + 0.06 * DR, color=FLOW, stroke_width=2.5, stroke_opacity=op),
                        Line(p + 0.06 * UR, p + 0.06 * DL, color=FLOW, stroke_width=2.5, stroke_opacity=op))
                    g.add(mark)
            return g

        def current_spacing() -> float | None:
            if two.get_value() < 1e-3:
                return None
            return spacing.get_value() * max(two.get_value(), 1e-3)

        def live_profile() -> VGroup:
            op = show_live.get_value()
            p = profile(current_spacing())
            pts = [ax.c2p(z, float(np.clip(v, 0.2, 1.3))) for z, v in zip(ZS, p, strict=True)]
            curve = VMobject(stroke_color=ACCENT, stroke_width=4, stroke_opacity=op)
            curve.set_points_smoothly(pts)
            return VGroup(halo(curve, layers=3, width=8, max_op=0.2 * op), curve)

        def flat_region() -> VGroup:
            """Where |B| is within 1% of its centre value, in the cross-section, and the same stretch of the axis
            on the profile."""
            op = show_flat.get_value()
            sp = current_spacing()
            b = b_cross(sp, MX, MY)
            mag = np.linalg.norm(b, axis=-1)
            centre = mag[len(GY) // 2, len(GX) // 2]
            closeness = np.abs(mag / centre - 1) / 0.01                 # 1 on the region's edge
            g = VGroup()
            segs = contour_paths(MX, MY, closeness, 1.0)
            segs = [max(segs, key=len)] if segs else []                  # the region round the centre, not the
            for seg in segs:                                             # fragments where its tips leave the grid
                v = VMobject(stroke_color=GOOD, stroke_width=2.5, stroke_opacity=0.9 * op, fill_color=GOOD,
                             fill_opacity=0.16 * op)
                v.set_points_as_corners([to_screen(x, y) for x, y in seg])
                g.add(v)
            w = flat_half_width(sp)
            band = Line(ax.c2p(-w, 1.0), ax.c2p(w, 1.0), color=GOOD, stroke_width=7, stroke_opacity=0.8 * op)
            g.add(halo(band, layers=3, width=10, max_op=0.25 * op), band)
            return g

        # ---- 1 · one loop ----
        self.next_section("one loop")
        lines_one = field_lines(lambda p: np.append(b_cross(None, np.array(p[0]), np.array(p[1])), 0.0),
                                seeds=[(0.0, y) for y in (-0.75, -0.45, -0.15, 0.15, 0.45, 0.75)]
                                + [(0.0, y) for y in (-1.6, 1.6)],
                                color=FLOW, ds=0.03, max_steps=420, bounds=(-1.9, 1.9, -1.9, 1.9),
                                stroke_width=1.6, opacity=0.45, mag_cap=1e9,
                                c2p=lambda x, y: to_screen(x, y))
        self.add(always_redraw(wires), always_redraw(live_profile))
        self.play(Create(axis_line), Create(lines_one, lag_ratio=0), run_time=1.6, rate_func=settle)
        self.play(FadeIn(ax), FadeIn(one_line), show_live.animate.set_value(1.0), run_time=1.2)
        self.note("One loop: the field is strongest at its centre and falls off along the axis.")
        self.wait(4.0)

        # ---- 2 · two loops, too far apart ----
        self.next_section("too far apart")
        lines_far = field_lines(lambda p: np.append(b_cross(1.6, np.array(p[0]), np.array(p[1])), 0.0),
                                seeds=[(0.0, y) for y in (-0.75, -0.45, -0.15, 0.15, 0.45, 0.75)]
                                + [(0.0, y) for y in (-1.7, 1.7)],
                                color=FLOW, ds=0.03, max_steps=480, bounds=(-1.9, 1.9, -1.9, 1.9),
                                stroke_width=1.6, opacity=0.45, mag_cap=1e9,
                                c2p=lambda x, y: to_screen(x, y))
        self.play(FadeOut(lines_one), two.animate(rate_func=smooth).set_value(1.0), run_time=2.0)
        self.play(Create(lines_far, lag_ratio=0), run_time=1.2, rate_func=settle)
        self.note("Add a second coil, far apart: the field sags in the middle.")
        self.wait(3.5)

        # ---- 3 · slide them: the spacing that's flattest ----
        self.next_section("the flattest spacing")
        readout = DecimalNumber(1.6, num_decimal_places=2, font_size=SMALL, color=INK)
        d_lab = tex(r"d=", size=SMALL).to_corner(UL, buff=0.45)
        r_lab = tex(r"R", size=SMALL)

        show_d = ValueTracker(0.0)

        def read(m: DecimalNumber) -> None:
            m.set_value(spacing.get_value()).next_to(d_lab, RIGHT, buff=0.1).set_opacity(show_d.get_value())
            r_lab.next_to(m, RIGHT, buff=0.08)

        read(readout)
        readout.add_updater(read)                      # live, so it fades through show_d, never FadeIn
        self.add(readout)
        self.play(FadeOut(lines_far), FadeIn(d_lab), FadeIn(r_lab), show_d.animate.set_value(1.0), run_time=0.8)
        self.add(always_redraw(flat_region))
        self.play(show_flat.animate.set_value(1.0), run_time=0.8)
        self.note("Green: where B is within 1% of its centre value.")
        self.play(spacing.animate.set_value(0.6), run_time=3.5, rate_func=smooth)
        self.note("Too close, and it peaks instead.")
        self.play(spacing.animate.set_value(1.0), run_time=3.0, rate_func=settle)
        lines_h = field_lines(lambda p: np.append(b_cross(1.0, np.array(p[0]), np.array(p[1])), 0.0),
                              seeds=[(0.0, y) for y in (-0.75, -0.45, -0.15, 0.15, 0.45, 0.75)]
                              + [(0.0, y) for y in (-1.7, 1.7)],
                              color=FLOW, ds=0.03, max_steps=480, bounds=(-1.9, 1.9, -1.9, 1.9),
                              stroke_width=1.6, opacity=0.45, mag_cap=1e9, c2p=lambda x, y: to_screen(x, y))
        self.play(Create(lines_h, lag_ratio=0), run_time=1.2, rate_func=settle)
        self.note("At d = R the flat region is widest: a Helmholtz pair.")
        self.wait(3.0)

        # ---- 4 · why: the curvature cancels ----
        self.next_section("why it's flat")
        series = tex(r"B(z)=B_0\left[1-\tfrac{144}{125}\left(\tfrac{z}{R}\right)^4+\dots\right]")
        why = backed(VGroup(series, words(r"no $z^2$ term: each loop's curvature cancels the other's", size=SMALL,
                                          color=MUTED)).arrange(DOWN, buff=0.2), opacity=0.85, buff=0.25)
        why.to_edge(UP, buff=0.35).shift(1.6 * RIGHT)
        self.play(FadeOut(d_lab), show_d.animate.set_value(0.0), FadeOut(r_lab), FadeIn(why, shift=0.15 * DOWN),
                  run_time=1.0,
                  rate_func=settle)
        # Off the axis the fourth-order term is r⁴P₄(cos θ), zero at θ ≈ 31° and 70°: the green star's points.
        self.note(r"Flat to fourth order; the 1\% region reaches out where that term vanishes.")
        self.wait(5.0)

        # ---- 5 · what the rig should read ----
        self.next_section("what the rig should read")
        rows = arrange_rows([
            words(rf"$R=15$ cm, 50 turns, 1 A: $B_0={RIG_B0 * 1e6:.0f}\,\mu$T", size=SMALL + 2),
            words(rf"within 1\% for $|z|<{RIG_FLAT_CM:.1f}$ cm of the centre", size=SMALL + 2, color=GOOD),
            words(r"Earth adds about $50\,\mu$T: measure it with the coils off, then subtract", size=SMALL,
                  color=MUTED),
        ], buff=0.25)
        panel = backed(rows, opacity=0.88, buff=0.3).to_edge(UP, buff=0.35).shift(1.6 * RIGHT)
        self.play(FadeOut(why), FadeIn(panel, shift=0.15 * DOWN), run_time=1.0, rate_func=settle)
        self.note("A prediction to test: map the field with the magnetometer and compare.")
        self.wait(5.0)

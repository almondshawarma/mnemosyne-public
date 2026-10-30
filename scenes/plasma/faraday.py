"""Faraday rotation: a magnetized plasma turns the plane of polarized light.

Storyboard: faraday_storyboard.md, beside this file. The physics is all amanim.plasma (cold-plasma waves along B,
Chen §4.18), each piece tested against an independent fact in amanim's tests/test_plasma.py:
- n_R and n_L from the cold-plasma dispersion relation, at ω = 3ω_c and ω_p = 1.7ω_c (normalized), so the twist fits
  on screen (about 88° over the column). A real tokamak laser runs at ω ≈ 600ω_c and turns about 21° over 2 m.
- The field everywhere is half an R wave plus half an L wave, with phases integrated through vacuum → plasma → vacuum
  (`accumulated_phase`, `linear_from_phases`). The plane of polarization is wherever the sum points.
- The inset's R and L arrows are the same two halves, at the probe, and their sum is the 3D wave's own vector there
  (asserted below), so the inset and the wave can't drift apart.
- Electrons gyrate the way `lorentz_path` says an electron does in B along the beam, and the R half turns with them.
- The two real-world angles in the last shot come from `plasma.rotation` with CODATA constants.

Time is slowed; the wave and the electrons keep their true 3:1 ratio of rates.

Motion rule (v2, after Aman's review): nothing that moves is ever FadeIn'd or FadeOut'd. Manim pauses an animated
mobject's updaters until the animation ends, so the first version froze the wave, the phasors and the electrons for
each fade and then snapped them forward. Everything live fades through a tracker its own redraw reads.
"""

from functools import lru_cache

import numpy as np
from amanim import glow, halo, pulse, pulse_factor, settle, spark, vec3
from amanim.plasma import C_RM, accumulated_phase, linear_from_phases, n_rl, rotation
from amanim.threed import View, project
from mn_style import *
from scipy import constants

CLIP = {
    "title": "Faraday rotation: a magnetized plasma turns light",
    "courses": [],
    "topics": ["plasma::waves", "plasma::diagnostics", "physics::optics::wave",
               "physics::em::radiation"],
    "watch_for": "The two halves of the light, one turning with the electrons and one against, travel at different "
                 "speeds in the plasma, so their sum's plane turns. Outside the plasma it stops turning.",
    "scene": "FaradayRotation",
    "look": "ethereal",
    # On an export's end card and in its upload text (render.py --publish).
    "credits": [
        "Waves along B: Chen, Introduction to Plasma Physics, sec. 4.18",
        "Constants: CODATA 2018",
        "Tokamak and ionosphere numbers: typical values",
        "The glowing look learned from Arithmetes and Knowable",
    ],
}

# ---- the physics, normalized: ω_c = 1 ----
W, WP, WC = 3.0, 1.7, 1.0                     # wave, plasma, cyclotron frequencies (R cutoff at 2.28: above it)
N_R, N_L = (float(v) for v in n_rl(W, WP, WC))
LAM = 1.6                                     # vacuum wavelength on screen
K0 = 2 * np.pi / LAM
X0, X1 = -2.2, 3.0                            # the plasma column along the beam
TWIST_RATE = K0 * (N_L - N_R) / 2             # dψ/dx inside, rad per unit
PSI_TOTAL = TWIST_RATE * (X1 - X0)
OMEGA_S = 2 * np.pi * 0.5                     # the wave turns at 0.5 Hz on screen ...
OMEGA_C_S = OMEGA_S * WC / W                  # ... and the electrons gyrate 3x slower: the true ratio
AMP = 1.0                                     # |E| on screen
XS = np.linspace(-6.0, 6.0, 61)               # where the E vectors are drawn
FINE = np.linspace(-6.0, 6.0, 2401)           # where the phases are integrated
PROBE = 4.6                                   # the inset looks here, past the column: what comes out

# ---- the view: ahead of the beam and to its side, so a turn about the beam draws as a turn ----
# v1 looked at the beam from the side. The plane never went edge-on, but a turn about an axis lying in the screen
# draws as a change of length: 88° of rotation showed as a 16° swing and a 28% growth ("the wave got bigger").
# What the viewer must see is the drawn angle, so that's what's optimized, sampled finely.
VIEW = View(np.radians(63), np.radians(-38))
BEAM = np.array([1.0, 0, 0])


def frame(beta0: float) -> tuple[np.ndarray, np.ndarray]:
    """Physics x-hat and y-hat in the scene, for a start polarization tilted beta0: (E0, E1, beam) right-handed, so
    physics rotation from x to y (with the electrons, B along +x) is a turn from E0 toward E1."""
    e0 = np.array([0.0, -np.sin(beta0), np.cos(beta0)])
    return e0, np.cross(BEAM, e0)


def drawn_turn(beta0: float) -> tuple[float, float]:
    """How the polarization draws over the whole twist, from this view: (shortest/longest drawn length, the screen
    angle it sweeps in degrees). 1 and 88.5° would be a turn drawn exactly as it is."""
    e0, e1 = frame(beta0)
    p = np.array([project(np.cos(q) * e0 + np.sin(q) * e1, VIEW) for q in np.linspace(0, PSI_TOTAL, 90)])
    lengths = np.linalg.norm(p, axis=1)
    ang = np.unwrap(np.arctan2(p[:, 1], p[:, 0]))
    return float(lengths.min() / lengths.max()), float(np.degrees(abs(ang[-1] - ang[0])))


def readability(beta0: float) -> float:
    ratio, sweep = drawn_turn(beta0)
    return min(ratio / 0.8, sweep / 75)


BETA0 = max(np.radians(np.arange(-180, 180, 1.0)), key=readability)
E0, E1 = frame(BETA0)
_RATIO, _SWEEP = drawn_turn(BETA0)
assert _RATIO > 0.75 and _SWEEP > 70, "from this view the turn would draw as a change of size, not a turn"
assert np.linalg.norm(project(BEAM, VIEW)) > 0.6, "the beam would be too foreshortened to read"


def to_scene(e: np.ndarray) -> np.ndarray:
    """Physics transverse (e_x, e_y) arrays of shape (..., 2) to scene vectors (..., 3)."""
    return e[..., :1] * E0 + e[..., 1:2] * E1


def end_on(v: np.ndarray) -> np.ndarray:
    """A scene vector as seen looking back down the beam (from +x, z up): (right, up) = (y, z). It's a rotation of the
    physics (e_x, e_y) plane, so the inset shows the true end-on view, the same way round as the 3D one."""
    return np.array([v[1], v[2], 0.0])


@lru_cache(maxsize=16)
def phases(dens: float) -> tuple[np.ndarray, np.ndarray]:
    """φ_R(x), φ_L(x) at XS for a column whose density is `dens` (0 to 1) of full: ω_p² scales with n_e."""
    n_r, n_l = (float(v) for v in n_rl(W, WP * np.sqrt(max(dens, 0.0)), WC))
    phi_r = accumulated_phase(FINE, lambda x: np.where((x > X0) & (x < X1), K0 * n_r, K0))
    phi_l = accumulated_phase(FINE, lambda x: np.where((x > X0) & (x < X1), K0 * n_l, K0))
    return np.interp(XS, FINE, phi_r), np.interp(XS, FINE, phi_l)


def halves_at(x: float, t: float, dens: float) -> tuple[np.ndarray, np.ndarray]:
    """The R and L halves of E at x, as physics (e_x, e_y): R counter-clockwise about +B, L clockwise."""
    phi_r, phi_l = phases(round(dens, 4))
    a = OMEGA_S * t - float(np.interp(x, XS, phi_r))
    b = OMEGA_S * t - float(np.interp(x, XS, phi_l))
    return 0.5 * AMP * np.array([np.cos(a), np.sin(a)]), 0.5 * AMP * np.array([np.cos(b), -np.sin(b)])


def field(t: float, dens: float) -> np.ndarray:
    """E at every XS, as scene vectors."""
    phi_r, phi_l = phases(round(dens, 4))
    return to_scene(linear_from_phases(OMEGA_S * t, phi_r, phi_l, AMP))


def psi_out(dens: float) -> float:
    """The angle the polarization has turned by, past the column."""
    phi_r, phi_l = phases(round(dens, 4))
    return float((phi_l[-1] - phi_r[-1]) / 2)


# The inset's arrows and the 3D wave are one field: at the probe, R + L is the wave's own vector, at any time and
# density (XS has a point at the probe, so no interpolation separates them).
assert np.isclose(XS, PROBE).any()
for _t, _d in ((0.0, 0.0), (0.37, 0.5), (1.9, 1.0)):
    _r, _l = halves_at(PROBE, _t, _d)
    assert np.allclose(to_scene(_r + _l), field(_t, _d)[np.argmin(abs(XS - PROBE))], atol=1e-9)


class FaradayRotation(MnScene3D):
    def construct(self) -> None:
        self.set_camera_orientation(*VIEW, zoom=0.92)
        clock = self.clock()                  # the frame's own time: no stall at play boundaries
        dens = ValueTracker(0.0)              # the plasma's density, 0 to 1 of full
        vis = ValueTracker(1.0)               # how bright the 3D picture is (dimmed for the last shot)
        show_light, show_inset, show_turn = ValueTracker(0.0), ValueTracker(0.0), ValueTracker(0.0)
        t, d = clock.get_value, lambda: dens.get_value()

        def wave() -> VGroup:
            """The E vectors along the beam, and the curve through their tips: the light, the hottest thing here."""
            op = show_light.get_value() * vis.get_value()
            e = field(t(), d())
            axis_pts = np.stack([XS, np.zeros_like(XS), np.zeros_like(XS)], axis=-1)
            tips = axis_pts + e
            spokes = VGroup(*(Line(a, b, color=ACCENT, stroke_width=2.4, stroke_opacity=0.7 * op)
                              for a, b in zip(axis_pts, tips, strict=True)))
            curve = VMobject(stroke_color=ACCENT, stroke_width=4.5, stroke_opacity=op)
            curve.set_points_smoothly(tips)
            return VGroup(halo(curve, layers=3, width=9, max_op=0.22 * op), spokes, curve)

        def ribbon() -> VMobject:
            """The plane of polarization as a faint strip: ±|E| along the direction the field points at each x."""
            op = show_light.get_value() * vis.get_value()
            phi_r, phi_l = phases(round(d(), 4))
            psi = (phi_l - phi_r) / 2
            dirs = to_scene(np.stack([np.cos(psi), np.sin(psi)], axis=-1))
            axis_pts = np.stack([XS, np.zeros_like(XS), np.zeros_like(XS)], axis=-1)
            top, bottom = axis_pts + AMP * dirs, axis_pts - AMP * dirs
            strip = VMobject(stroke_color=ACCENT, stroke_width=1.2, stroke_opacity=0.25 * op,
                             fill_color=ACCENT, fill_opacity=0.07 * op)
            strip.set_points_as_corners([*top, *bottom[::-1], top[0]])
            return strip

        beam_axis = Line((-6.3, 0, 0), (6.3, 0, 0), color=MUTED, stroke_width=1.5, stroke_opacity=0.5)

        # ---- 1 · light, polarized ----
        self.next_section("light, polarized")
        self.add(always_redraw(ribbon), always_redraw(wave))
        self.play(Create(beam_axis), show_light.animate(rate_func=settle).set_value(1.0), run_time=1.6)
        self.note("Linearly polarized light: E swings back and forth in one plane.")
        self.wait(5.0)

        # ---- 2 · two circles make a line: the inset, looking back down the beam ----
        self.next_section("two circles make a line")
        c_in, r_in = np.array([-5.3, -1.0, 0.0]), 1.15     # big enough to read the arrows on a phone
        start_dir = end_on(to_scene(np.array([1.0, 0.0])))
        start_ang = float(np.arctan2(start_dir[1], start_dir[0]))

        def inset_vectors() -> VGroup:
            """R from the centre, L from R's tip, their sum from the centre, at the probe: the wave's own vector
            there, seen end-on. R turns counter-clockwise here, with the electrons."""
            op = show_inset.get_value()
            r, lv = halves_at(PROBE, t(), d())
            k = r_in / AMP
            r3, l3 = end_on(to_scene(r)) * k, end_on(to_scene(lv)) * k
            psi = psi_out(d())
            line = end_on(to_scene(np.array([np.cos(psi), np.sin(psi)]))) * r_in
            g = VGroup(
                DashedLine(c_in - line, c_in + line, color=ACCENT, stroke_width=1.5, stroke_opacity=0.5,
                           dash_length=0.06),
                Arrow(c_in, c_in + r3, buff=0, color=FLOW, stroke_width=4, max_tip_length_to_length_ratio=0.25),
                Arrow(c_in + r3, c_in + r3 + l3, buff=0, color=WARN, stroke_width=4,
                      max_tip_length_to_length_ratio=0.25),
                Arrow(c_in, c_in + r3 + l3, buff=0, color=ACCENT, stroke_width=5,
                      max_tip_length_to_length_ratio=0.2))
            for f in g.family_members_with_points():    # tips included: Arrow's own opacity args miss them
                f.set_fill(opacity=f.get_fill_opacity() * op).set_stroke(opacity=f.get_stroke_opacity() * op)
            return g

        def probe_mark() -> VGroup:
            """The wave's vector at the probe, drawn bright: the 3D twin of the inset's R+L arrow."""
            op = show_inset.get_value() * vis.get_value()
            e = field(t(), d())[np.argmin(abs(XS - PROBE))]
            base = np.array([PROBE, 0.0, 0.0])
            spoke = Line(base, base + e, color=ACCENT, stroke_width=6, stroke_opacity=op)
            return VGroup(halo(spoke, layers=3, width=10, max_op=0.3 * op), spoke,
                          glow(base, ACCENT, radius=0.06, layers=5, max_op=0.25 * op, spread=2.0))

        ring = Circle(radius=r_in, color=MUTED, stroke_width=1.2, stroke_opacity=0.5).move_to(c_in)
        inset_title = VGroup(words("looking back", size=SMALL, color=MUTED),
                             words("down the beam", size=SMALL, color=MUTED)).arrange(DOWN, aligned_edge=LEFT,
                                                                                      buff=0.08)
        inset_title.next_to(ring, RIGHT, buff=0.3).align_to(ring, UP)
        legend = VGroup(tex(r"R", size=SMALL, color=FLOW), tex(r"L", size=SMALL, color=WARN),
                        tex(r"R+L", size=SMALL, color=ACCENT)).arrange(RIGHT, buff=0.35).next_to(
            inset_title, DOWN, buff=0.15, aligned_edge=LEFT)
        inset = self.pin(VGroup(ring, inset_title, legend))
        self.add(self.pin(always_redraw(inset_vectors)), always_redraw(probe_mark))
        self.play(FadeIn(inset), show_inset.animate(rate_func=settle).set_value(1.0), run_time=1.2)
        self.note("Any line is two opposite circles, added: half R, half L.")
        self.wait(6.0)

        # ---- 3 · enter the plasma ----
        self.next_section("enter the plasma")
        R = 1.3
        lines = VGroup()
        for ang in np.linspace(0, 2 * np.pi, 7)[:-1]:
            off = R * (np.cos(ang) * E0 + np.sin(ang) * E1)
            lines.add(Line(off + (X0 - 0.3) * RIGHT, off + (X1 + 0.3) * RIGHT, color=MUTED, stroke_width=2,
                           stroke_opacity=0.55))
        column = VGroup(halo(lines, layers=2, width=6, max_op=0.08), lines,
                        *(ParametricFunction(lambda u, x=x: np.array([x, 0, 0]) + R * (np.cos(u) * E0
                                                                                        + np.sin(u) * E1),
                                             t_range=(0, 2 * np.pi), color=MUTED, stroke_width=1.5,
                                             stroke_opacity=0.35) for x in (X0, X1)))
        b_arrow = vec3((1.6, 0, 0), (X0 + 0.2, 0, 0) + (R + 0.5) * OUT, color=INK, stroke_width=4)
        b_label = self.label(r"\vec B", (X0 + 2.1, 0, 0) + (R + 0.75) * OUT, color=INK)
        rng = np.random.default_rng(7)
        GYRO = 0.28                                    # the gyration radius on screen: big enough to see it turn
        centres = [(x, 0.95 + 0.15 * rng.random(), rng.random() * 2 * np.pi) for x in np.linspace(X0 + 0.3,
                                                                                                  X1 - 0.3, 10)]
        electrons, orbits, appear = VGroup(), VGroup(), []
        for k_, (x, rad, ang) in enumerate(centres):
            centre = np.array([x, 0, 0]) + rad * (np.cos(ang) * E0 + np.sin(ang) * E1)
            orbits.add(ParametricFunction(lambda u, c=centre: c + GYRO * (np.cos(u) * E0 + np.sin(u) * E1),
                                          t_range=(0, 2 * np.pi), color=FLOW, stroke_width=1.2,
                                          stroke_opacity=0.28))
            e = spark(FLOW, radius=0.075)
            on = ValueTracker(0.0)

            def gyrate(m, centre=centre, k_=k_):
                g = OMEGA_C_S * clock.get_value() + k_                     # counter-clockwise about +B (lorentz_path)
                m.move_to(centre + GYRO * (np.cos(g) * E0 + np.sin(g) * E1))
            gyrate(e)
            e.add_updater(gyrate)
            e.add_updater(pulse(e, clock, rate=0.6, depth=0.5, phase=1.7 * k_, gain=on.get_value))
            electrons.add(e)
            appear.append(on)
        self.add(electrons)                            # invisible (gain 0) but already moving
        self.play(FadeIn(column), FadeIn(orbits), Create(b_arrow), FadeIn(b_label), run_time=1.6, rate_func=settle)
        self.play(LaggedStart(*(on.animate.set_value(1.0) for on in appear), lag_ratio=0.12), run_time=1.6)
        self.note("In B, electrons gyrate one way. The R half turns with them.")
        self.wait(4.5)

        # ---- 4 · the plane turns ----
        self.next_section("the plane turns")
        x_out = X1 + 0.15

        def turned() -> VGroup:
            """The start direction as a ghost at the exit, the exit direction glowing and breathing, and the arc
            between them: the rotation, drawn where it happens."""
            op = show_turn.get_value() * vis.get_value()
            psi = psi_out(d())
            c = np.array([x_out, 0.0, 0.0])
            ghost_d = to_scene(np.array([1.0, 0.0])) * (AMP + 0.25)
            out_d = to_scene(np.array([np.cos(psi), np.sin(psi)])) * (AMP + 0.25)
            k = pulse_factor(t(), rate=0.8, depth=0.55)
            ghost = DashedLine(c - ghost_d, c + ghost_d, color=MUTED, stroke_width=3, stroke_opacity=0.7 * op,
                               dash_length=0.1)
            bar = Line(c - out_d, c + out_d, color=GOOD, stroke_width=5, stroke_opacity=op * k)
            parts = [ghost, halo(bar, layers=4, width=10, max_op=0.3 * op * k), bar]
            if abs(psi) > 1e-3:
                parts.append(ParametricFunction(
                    lambda u: c + to_scene(np.array([np.cos(u), np.sin(u)])) * (AMP + 0.45),
                    t_range=(0.0, psi), color=GOOD, stroke_width=3, stroke_opacity=0.8 * op))
            return VGroup(*parts)

        def entry_bar() -> Line:
            op = show_turn.get_value() * vis.get_value()
            dd = to_scene(np.array([1.0, 0.0])) * (AMP + 0.25)
            c = np.array([X0 - 0.15, 0.0, 0.0])
            return Line(c - dd, c + dd, color=MUTED, stroke_width=3, stroke_opacity=0.6 * op)

        def inset_arc() -> VMobject:
            psi, op = psi_out(d()), show_turn.get_value() * show_inset.get_value()
            if abs(psi) < 1e-3:
                return VMobject()
            return Arc(radius=r_in * 0.75, start_angle=start_ang, angle=psi, arc_center=c_in, color=GOOD,
                       stroke_width=4, stroke_opacity=op)

        readout = DecimalNumber(0, num_decimal_places=0, unit=r"^\circ", font_size=SMALL, color=GOOD)
        psi_lab = tex(r"\psi=", size=SMALL, color=GOOD)

        def read(m: DecimalNumber) -> None:
            m.set_value(np.degrees(psi_out(d())))
            m.next_to(psi_lab, RIGHT, buff=0.08)
            m.set_opacity(show_turn.get_value() * show_inset.get_value())

        psi_lab.next_to(legend, DOWN, buff=0.2, aligned_edge=LEFT)
        psi_lab.add_updater(lambda m: m.set_opacity(show_turn.get_value() * show_inset.get_value()))
        read(readout)
        readout.add_updater(read)
        self.add(always_redraw(entry_bar), always_redraw(turned), self.pin(always_redraw(inset_arc)),
                 self.pin(psi_lab), self.pin(readout))
        eq = self.pin(tex(r"\psi=\frac{\omega}{2c}\,(n_L-n_R)\,L").to_corner(UR, buff=0.4))
        n_text = self.pin(tex(rf"n_R={N_R:.2f}\quad n_L={N_L:.2f}", size=SMALL, color=MUTED).next_to(eq, DOWN,
                                                                                                buff=0.2))
        self.play(show_turn.animate.set_value(1.0), run_time=1.0)
        self.note("Fill the column with plasma: R and L now travel at different speeds.")
        self.play(dens.animate.set_value(1.0), run_time=6.0, rate_func=smooth)
        self.play(FadeIn(eq), FadeIn(n_text), run_time=1.0)
        self.note(rf"The line turns {np.degrees(PSI_TOTAL):.0f}$^\circ$ inside, and stops turning outside.")
        self.wait(5.0)

        # ---- 5 · what it measures ----
        self.next_section("what it measures")
        psi_tok = rotation(118.8e-6, 1e20, 0.5, 2.0)
        psi_ion = rotation(constants.c / 150e6, 1e17 / 1.0, 50e-6, 1.0)   # TEC is n_e integrated: pass L = 1 m
        formula = tex(r"\psi = C\,\lambda^2\!\int n_e B_\parallel\,dl").move_to(1.9 * UP)
        constant = tex(rf"C=\frac{{e^3}}{{8\pi^2\epsilon_0 m_e^2c^3}}={C_RM / 1e-13:.2f}\times10^{{-13}}",
                       size=SMALL, color=MUTED).next_to(formula, DOWN, buff=0.3)

        def result(head: str, detail: str) -> VGroup:
            return VGroup(words(head, size=SMALL + 2), words(detail, size=SMALL, color=MUTED)).arrange(
                DOWN, buff=0.12)

        rows = arrange_rows([
            result(rf"Tokamak polarimetry: $\psi\approx{np.degrees(psi_tok):.0f}^\circ$",
                   r"$\lambda=118.8\,\mu$m laser, $n_e=10^{20}$ m$^{-3}$, $B_\parallel=0.5$ T over 2 m"),
            result(rf"Your ionosphere station: $\psi\approx{psi_ion:.1f}$ rad",
                   r"150 MHz, TEC $=10^{17}$ m$^{-2}$, $B_\parallel=50\,\mu$T"),
        ], buff=0.4).next_to(constant, DOWN, buff=0.5)
        panel = self.pin(backed(VGroup(formula, constant, rows), opacity=0.82, buff=0.35))
        self.play(vis.animate.set_value(0.12), show_inset.animate.set_value(0.0),
                  *(on.animate.set_value(0.0) for on in appear),
                  FadeOut(inset), FadeOut(eq), FadeOut(n_text), FadeOut(orbits), column.animate.fade(0.8),
                  b_arrow.animate.fade(0.7), FadeOut(b_label), run_time=1.2)
        self.play(FadeIn(panel, shift=0.2 * UP), run_time=1.2, rate_func=settle)
        self.note(r"Measure $\psi$, and you've measured $\int n_e B_\parallel\,dl$: that's polarimetry.")
        self.wait(5.0)

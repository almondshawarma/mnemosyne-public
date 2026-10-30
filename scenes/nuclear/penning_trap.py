"""A Penning trap's three motions, and how one trajectory gives them all back.

Channel series 3 ("Trapped"): the trap Aman's lab (TAMUTRAP, Cyclotron Institute) holds ions in.

What's computed (amanim.particles and amanim.spectral, tested in amanim's tests/test_blocks.py):
- The three eigenfrequencies of the ideal trap from its parameters: ω_z² = qV0/(md²), ω_± = (ω_c ± √(ω_c² − 2ω_z²))/2.
- The particle's path as the sum of the three eigenmotions (two circles turning the same way, and the bounce), with
  each one's amplitude on a tracker so they can be shown one at a time. That the sum IS the motion is asserted below
  against an independent Boris integration of q(E + v×B) from the same start.
- The spectrum in the last shot is measured from the Boris run (Hann window, zero-padded FFT, parabolic peaks), not
  read back from the formulas; then the Brown–Gabrielse invariance theorem, ω_c² = ω₊² + ω_z² + ω₋², turns the three
  measured peaks back into ω_c = qB/m.

Units: q/m = 1, B = 10, V0 = 8, d = 1, so ω₊ = 9.58, ω_z = 2.83, ω₋ = 0.42; the screen runs at 1.07 physics units a
second (one magnetron turn in about 14 s). A real trap's ω₊/ω₋ is thousands; here it's 23, so all three fit on screen.

Motion rule: everything moves off self.clock() (no stall at play boundaries) and fades through trackers.
"""

import numpy as np
from amanim import glow, pulse_factor, settle, spark, vec3
from amanim.particles import boris, penning_E, penning_frequencies, penning_modes
from amanim.plots import plot_axes
from amanim.spectral import peak_frequencies, spectrum
from mn_style import *

CLIP = {
    "title": "A Penning trap's three motions",
    "courses": [],
    "topics": ["physics::em::motion-in-field", "plasma::fundamentals", "principle::superposition"],
    "watch_for": "The bounce along B, the fast circle across it, and the slow drift of that circle's centre around "
                 "the trap: three motions added. Then one trajectory's spectrum shows three peaks, and the "
                 "invariance theorem turns them back into the cyclotron frequency, qB/m: how a Penning trap weighs "
                 "an ion.",
    "scene": "PenningTrap",
    "look": "ethereal",
}

Q_M, BZ, V0, D = 1.0, 10.0, 8.0, 1.0
F = penning_frequencies(Q_M, BZ, V0, D)
RHO_P, RHO_M, Z0 = 0.17, 0.75, 0.45          # the cyclotron and magnetron radii, the axial amplitude
TS = 1.07                                     # physics time per screen second
SCALE = 2.1                                   # screen units per physics unit


def modes(t: float, amp_p: float, amp_m: float, amp_z: float) -> dict[str, np.ndarray]:
    m = penning_modes(Q_M, BZ, V0, D, rho_plus=RHO_P * amp_p, rho_minus=RHO_M * amp_m, z0=Z0 * amp_z,
                      t=np.array([t]))
    return {k: v[0] * SCALE for k, v in m.items()}


# The sum of the three modes is the motion: check it against Boris from the same start, over a whole clip's worth.
_dt, _steps = 0.002, 30000
_t = np.arange(_steps + 1) * _dt
_m = penning_modes(Q_M, BZ, V0, D, rho_plus=RHO_P, rho_minus=RHO_M, z0=Z0, t=_t)
_sum = _m["plus"] + _m["minus"] + _m["z"]
_v0 = np.array([0.0, -(RHO_P * F["plus"] + RHO_M * F["minus"]), 0.0])
_rs, _ = boris(Q_M, penning_E(V0, D), (0, 0, BZ), _sum[0], _v0, dt=_dt, steps=_steps)
assert np.max(np.linalg.norm(_rs - _sum, axis=1)) < 0.02, "the three modes don't add up to the true motion"

# What the last shot measures: the Boris run's spectrum, and the three peaks in it.
W_SPEC, X_SPEC = spectrum(_rs[:, 0], _dt)
_, Z_SPEC = spectrum(_rs[:, 2], _dt)
W_MINUS, W_PLUS = peak_frequencies(_rs[:, 0], _dt, 2, min_separation=0.5)
(W_Z,) = peak_frequencies(_rs[:, 2], _dt, 1)
WC_MEASURED = float(np.sqrt(W_PLUS**2 + W_Z**2 + W_MINUS**2))
assert abs(WC_MEASURED - Q_M * BZ) < 0.02 * Q_M * BZ


def electrodes() -> VGroup:
    """The ideal trap's electrodes, hyperboloids of the potential's own shape, as a faint wireframe: the ring
    ρ²/2 − z² = 1 and the endcaps z² − ρ²/2 = 1 (so d = 1)."""
    g = VGroup()
    zs = np.linspace(-0.85, 0.85, 40)
    rs = np.linspace(0.0, 1.35, 40)
    ring_r = np.sqrt(2 * (1 + zs**2))
    cap_z = np.sqrt(1 + rs**2 / 2)
    for a in np.linspace(0, 2 * np.pi, 13)[:-1]:
        c, s = np.cos(a), np.sin(a)
        for pts in (np.stack([ring_r * c, ring_r * s, zs], axis=-1),
                    np.stack([rs * c, rs * s, cap_z], axis=-1), np.stack([rs * c, rs * s, -cap_z], axis=-1)):
            v = VMobject(stroke_color=MUTED, stroke_width=1.2, stroke_opacity=0.22)
            v.set_points_smoothly(pts * SCALE)
            g.add(v)
    for z in (-0.85, 0.0, 0.85):
        g.add(Circle(radius=np.sqrt(2 * (1 + z**2)) * SCALE, color=MUTED, stroke_width=1.2,
                     stroke_opacity=0.3).shift(z * SCALE * OUT))
    for z in (1.0, -1.0):
        g.add(Circle(radius=1.35 * SCALE, color=MUTED, stroke_width=1.0, stroke_opacity=0.2).shift(
            np.sign(z) * np.sqrt(1 + 1.35**2 / 2) * SCALE * OUT))
    return g


class PenningTrap(MnScene3D):
    def construct(self) -> None:
        self.set_camera_orientation(phi=np.radians(66), theta=np.radians(-58), zoom=1.0)
        clock = self.clock()
        amp_z, amp_p, amp_m = ValueTracker(0.0), ValueTracker(0.0), ValueTracker(0.0)
        show_p, show_arms = ValueTracker(0.0), ValueTracker(0.0)
        history: list[tuple[float, np.ndarray]] = []

        def now() -> dict[str, np.ndarray]:
            return modes(TS * clock.get_value(), amp_p.get_value(), amp_m.get_value(), amp_z.get_value())

        def particle() -> VGroup:
            m = now()
            pos = m["plus"] + m["minus"] + m["z"]
            t = clock.get_value()
            if not history or t > history[-1][0] + 1e-9:
                history.append((t, pos))
            op = show_p.get_value()
            s = spark(ACCENT, radius=0.07).move_to(pos)
            for f in s.family_members_with_points():          # scale each layer, so the glow keeps its falloff
                f.set_fill(opacity=f.get_fill_opacity() * op).set_stroke(opacity=f.get_stroke_opacity() * op)
            return s

        def trail() -> VGroup:
            """Where the particle has been in the last 9 s: older is fainter."""
            t = clock.get_value()
            pts = [p for (tt, p) in history if t - tt < 9.0]
            g = VGroup()
            if len(pts) < 4:
                return g
            chunks = np.array_split(np.arange(len(pts)), 10)
            for i, idx in enumerate(chunks):
                if len(idx) == 0:                        # fewer points than chunks, early on
                    continue
                seg =[pts[j] for j in range(max(idx[0] - 1, 0), idx[-1] + 1)]
                if len(seg) < 2:
                    continue
                v = VMobject(stroke_color=ACCENT, stroke_width=2.5, stroke_opacity=0.08 + 0.6 * (i + 1) / 10)
                v.set_points_smoothly(seg) if len(seg) > 2 else v.set_points_as_corners(seg)
                g.add(v)
            return g

        def arms() -> VGroup:
            """The motion taken apart: the magnetron arm from the centre to the circle's centre, the cyclotron arm
            from there, and the axial offset up to the particle."""
            op = show_arms.get_value()
            m = now()
            g_c = m["minus"]
            tip = g_c + m["plus"]
            pos = tip + m["z"]
            k = pulse_factor(clock.get_value(), rate=0.5, depth=0.25)
            parts = VGroup(
                Line(ORIGIN, g_c, color=WARN, stroke_width=4, stroke_opacity=op),
                Line(g_c, tip, color=FLOW, stroke_width=4, stroke_opacity=op),
                DashedLine(tip, pos, color=GOOD, stroke_width=3, stroke_opacity=op, dash_length=0.08),
                glow(g_c, WARN, radius=0.05, layers=4, max_op=0.25 * op * k, spread=2.0))
            return parts

        b_arrow = vec3((0, 0, 2.4), (4.1, 0, -1.2), color=INK, stroke_width=4)   # outside the ring electrode
        b_label = self.label(r"\vec B", (4.45, 0, 0.8), color=INK)

        # ---- 1 · the trap ----
        self.next_section("the trap")
        trap = electrodes()
        self.add(always_redraw(trail), always_redraw(arms), always_redraw(particle))
        self.play(Create(trap, lag_ratio=0.02), run_time=2.0, rate_func=settle)
        self.play(Create(b_arrow), FadeIn(b_label), show_p.animate.set_value(1.0), amp_z.animate.set_value(0.25),
                  run_time=1.0)                          # a small bob from the start: never fully still
        self.note(r"A Penning trap: a strong $\vec B$, and electrodes that push a charge back.")
        self.wait(3.0)

        # ---- 2 · along B: the axial bounce ----
        self.next_section("axial")
        legend = VGroup(
            VGroup(Line(ORIGIN, 0.4 * RIGHT, color=GOOD, stroke_width=4),
                   tex(rf"\omega_z={F['z']:.2f}\ \text{{axial}}", size=SMALL, color=GOOD)).arrange(RIGHT),
            VGroup(Line(ORIGIN, 0.4 * RIGHT, color=FLOW, stroke_width=4),
                   tex(rf"\omega_+={F['plus']:.2f}\ \text{{cyclotron}}", size=SMALL, color=FLOW)).arrange(RIGHT),
            VGroup(Line(ORIGIN, 0.4 * RIGHT, color=WARN, stroke_width=4),
                   tex(rf"\omega_-={F['minus']:.2f}\ \text{{magnetron}}", size=SMALL, color=WARN)).arrange(RIGHT),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.18).to_corner(UL, buff=0.4)
        self.pin(legend)
        self.play(amp_z.animate.set_value(1.0), FadeIn(legend[0]), show_arms.animate.set_value(1.0), run_time=2.0,
                  rate_func=settle)
        self.note(r"Along $\vec B$, the electric field acts like a spring: the axial bounce.")
        self.wait(4.0)

        # ---- 3 · across B: the fast circle ----
        self.next_section("cyclotron")
        self.play(amp_p.animate.set_value(1.0), FadeIn(legend[1]), run_time=2.0, rate_func=settle)
        self.note(r"Across $\vec B$, it circles fast: the cyclotron motion.")
        self.wait(4.0)

        # ---- 4 · the slow drift of that circle ----
        self.next_section("magnetron")
        self.play(amp_m.animate.set_value(1.0), FadeIn(legend[2]), run_time=3.0, rate_func=settle)
        self.note("And the circle's centre drifts slowly around: the magnetron motion.")
        self.wait(9.0)

        # ---- 5 · read them back from one trajectory ----
        self.next_section("read them back")
        sx = plot_axes((0, 11, 1), (0, 1.45, 0.5), x_length=5.4, y_length=2.1, color=MUTED,
                       labels=(r"\omega", r"|X(\omega)|"), label_size=SMALL)
        norm_x = np.sqrt(X_SPEC / X_SPEC.max())                       # √ so the small cyclotron peak shows
        norm_z = np.sqrt(Z_SPEC / Z_SPEC.max())
        keep = W_SPEC < 11
        spec_x = VMobject(stroke_color=ACCENT, stroke_width=2.5)
        spec_x.set_points_as_corners([sx.c2p(w, a) for w, a in zip(W_SPEC[keep], norm_x[keep], strict=True)])
        spec_z = VMobject(stroke_color=GOOD, stroke_width=2.5)
        spec_z.set_points_as_corners([sx.c2p(w, a) for w, a in zip(W_SPEC[keep], norm_z[keep], strict=True)])
        tags = VGroup(*(tex(s, size=SMALL, color=c).next_to(sx.c2p(w, 1.0), UP, buff=0.05)
                        for s, w, c in ((rf"{W_MINUS:.2f}", W_MINUS, WARN), (rf"{W_Z:.2f}", W_Z, GOOD),
                                        (rf"{W_PLUS:.2f}", W_PLUS, FLOW))))
        theorem = tex(rf"\sqrt{{\omega_+^2+\omega_z^2+\omega_-^2}}={WC_MEASURED:.2f}=\frac{{qB}}{{m}}",
                      size=SMALL, color=INK)
        panel = backed(VGroup(VGroup(sx, spec_x, spec_z, tags), theorem).arrange(DOWN, buff=0.3), opacity=0.86,
                       buff=0.25).to_corner(UR, buff=0.3)
        self.pin(panel)
        right = np.array([-np.sin(np.radians(-58)), np.cos(np.radians(-58)), 0.0])   # screen-right, in the world
        self.move_camera(frame_center=3.0 * right, run_time=1.2, rate_func=settle,       # the trap steps aside
                         added_anims=[FadeIn(panel[0]), FadeIn(sx), FadeOut(b_arrow), FadeOut(b_label)])
        self.play(Create(spec_x), Create(spec_z), run_time=1.6, rate_func=settle)
        self.play(FadeIn(tags), run_time=0.6)
        self.note("Its spectrum, measured from one simulated trajectory, has three peaks.")
        self.wait(2.5)
        self.play(FadeIn(theorem, shift=0.1 * UP), run_time=0.8)
        self.note("Together they give back qB/m: how a Penning trap weighs an ion.")
        self.wait(5.0)

"""Compton scattering in the glowing look: the collision as it happens, the shift growing with angle, and both
Feynman diagrams moving.

A remake of the nuen201 Compton clip (L09, slides 10-14) to test the look Aman chose on 2026-10-24: near-black,
lines that glow (`"look": "ethereal"` puts the render through amanim.post's bloom), something always moving, packets
of light instead of endless waves. No backdrop: the action fills the frame, and a background is optional (Aman:
"there doesn't have to be something").

Everything is computed (amanim.scattering): λ' from the Compton formula, the electron's recoil angle φ and momentum
from momentum conservation, and even the speeds: the photon moves at c and the electron at β = p_e c / E_e, so at
θ = 60° it covers 0.66 of the photon's distance in the same time. λ₀ = λ_C (a 511 keV gamma), so the stretch is
easy to see.
"""

import numpy as np
from amanim import (
    compton_lambda,
    compton_recoil,
    diagram,
    glow,
    halo,
    settle,
    sine_path,
    spark,
    wave_packet,
)
from mn_style import *
from scipy import constants

CLIP = {
    "title": "Compton scattering, in light",
    "courses": ["nuen201"],
    "topics": ["physics::quantum::wave-particle", "physics::particle::feynman-diagrams",
               "principle::conservation-of-momentum", "principle::conservation-of-energy"],
    "watch_for": "The scattered packet is visibly longer in wavelength, more so the further back it goes; the "
                 "electron leaves slower than the photon (β = 0.66 at 60°); and the two diagrams, absorb-then-emit "
                 "and emit-then-absorb, that QED adds.",
    "scene": "ComptonLight",
    "look": "ethereal",
}

LAMBDA_C_PM = constants.h / (constants.m_e * constants.c) * 1e12
LAM0 = 1.0                              # λ₀ in units of λ_C
WL0 = 0.42                              # λ₀ on screen, scene units
THETA = np.radians(60)                  # the collision's scattering angle
E_AT = np.array([-2.0, 0.4, 0.0])       # the electron, for the collision
PACKET = 2.6                            # a packet's length, scene units
TRAVEL = 4.2                            # how far the scattered photon travels in the collision shot


def unit(a: float) -> np.ndarray:
    return np.array([np.cos(a), np.sin(a), 0.0])


def beta(theta: float, lam0: float = LAM0) -> float:
    """The recoiling electron's speed over c: β = p_e c / E_e, with p_e c in units of m_e c² = |p_e|/|p₀| / λ₀."""
    _, pe = compton_recoil(theta, lam0)
    pc = pe / lam0
    return pc / float(np.hypot(pc, 1.0))


class ComptonLight(MnScene):
    def construct(self) -> None:
        # No backdrop: the collision and the triangle fill the frame, and empty space is the background here.
        clock = ValueTracker(0.0)                   # ambient time: waves travel, the electron breathes
        clock.add_updater(lambda m, dt: m.increment_value(dt))
        self.add(clock)

        # ---- a photon arrives ----
        self.next_section("a photon arrives")
        electron = spark(FLOW, radius=0.13).move_to(E_AT)
        aura = glow(E_AT, FLOW, radius=0.2, layers=8, max_op=0.12, spread=2.4)
        aura.add_updater(lambda m: m.set_height(0.2 * 2 * 3.4 * (1 + 0.06 * np.sin(3 * clock.get_value())))
                         .move_to(electron))
        e_label = tex(r"e^-", size=SMALL, color=FLOW)
        e_label.add_updater(lambda m: m.next_to(electron, UP + LEFT, buff=0.12))
        self.play(FadeIn(aura), FadeIn(electron, scale=0.5), FadeIn(e_label), run_time=1.0, rate_func=settle)
        start = E_AT + 6.0 * LEFT
        arrive = ValueTracker(0.0)

        def incoming() -> VMobject:
            front = start + arrive.get_value() * (E_AT - start)
            return wave_packet(front - PACKET / 2 * RIGHT, RIGHT, wavelength=WL0 * LAM0, length=PACKET, amp=0.2,
                               color=ACCENT, stroke_width=5)

        photon_in = always_redraw(incoming)
        g_label = tex(r"\gamma,\ \lambda_0", size=SMALL, color=ACCENT)
        g_label.add_updater(lambda m: m.next_to(photon_in, UP, buff=0.18))
        self.add(photon_in, g_label)
        self.play(arrive.animate.set_value(1.0), run_time=2.6, rate_func=linear)   # light: constant speed
        self.note(r"A photon ($\lambda_0$) meets a free electron at rest.")

        # ---- the collision: the photon leaves at θ, longer; the electron recoils at φ, slower ----
        self.next_section("the collision")
        phi, _ = compton_recoil(THETA, LAM0)
        lam1 = compton_lambda(THETA, LAM0)
        d_out, d_e = unit(-THETA), unit(phi)
        b = beta(THETA)
        go = ValueTracker(0.0)

        def outgoing() -> VMobject:
            front = E_AT + go.get_value() * TRAVEL * d_out
            return wave_packet(front - PACKET / 2 * d_out, d_out, wavelength=WL0 * lam1, length=PACKET, amp=0.2,
                               color=ACCENT, stroke_width=5)

        self.remove(photon_in, g_label)
        flash_at = glow(E_AT, INK, radius=0.12, layers=8, max_op=0.35, spread=2.0)
        self.play(FadeIn(flash_at, scale=0.3), run_time=0.15)
        photon_out = always_redraw(outgoing)
        trail = TracedPath(electron.get_center, stroke_color=FLOW, stroke_width=4, dissipating_time=1.2)
        electron.add_updater(lambda m: m.move_to(E_AT + go.get_value() * TRAVEL * b * d_e))
        guide_out = DashedLine(E_AT, E_AT + 3.4 * d_out, color=MUTED, stroke_width=2, dash_length=0.08)
        guide_e = DashedLine(E_AT, E_AT + 3.0 * d_e, color=MUTED, stroke_width=2, dash_length=0.08)
        axis = DashedLine(E_AT + 2.2 * LEFT, E_AT + 3.4 * RIGHT, color=MUTED, stroke_width=2, dash_length=0.08)
        self.add(photon_out, trail)
        self.play(FadeOut(flash_at, scale=3.0), go.animate.set_value(1.0), run_time=2.6, rate_func=linear)
        ref = Line(E_AT, E_AT + RIGHT)
        arcs = VGroup(Angle(ref, Line(E_AT, E_AT + d_out), radius=0.9, other_angle=True, color=WARM, stroke_width=4),
                      Angle(ref, Line(E_AT, E_AT + d_e), radius=1.2, color=FLOW, stroke_width=4))
        names = VGroup(tex(r"\theta", size=SMALL, color=WARM).move_to(E_AT + 1.25 * unit(-THETA / 2)),
                       tex(r"\varphi", size=SMALL, color=FLOW).move_to(E_AT + 1.6 * unit(phi / 2)),
                       tex(r"\lambda' = %.1f\,\lambda_0" % (lam1 / LAM0), size=SMALL, color=ACCENT)
                       .move_to(E_AT + 2.2 * d_out + 1.25 * unit(-THETA + PI / 2)))
        self.play(Create(axis), Create(guide_out), Create(guide_e), Create(arcs), FadeIn(names), run_time=1.2,
                  rate_func=settle)
        self.note(rf"Out at $\theta$ with a longer $\lambda'$; the electron recoils at $\varphi$, at $\beta={b:.2f}$.")
        self.wait(2.2)

        # ---- the shift grows with angle; the momentum triangle stays closed ----
        self.next_section("the shift with angle")
        electron.clear_updaters()
        self.play(*(FadeOut(m) for m in (photon_out, trail, guide_out, guide_e, arcs, names, axis)),
                  electron.animate.move_to(E_AT + 1.0 * LEFT), run_time=1.0, rate_func=settle)
        here = E_AT + 1.0 * LEFT
        theta = ValueTracker(np.radians(20))

        def ray() -> VMobject:
            t = theta.get_value()
            d = unit(-t)
            return sine_path(here + 0.2 * d, here + 2.6 * d, wavelength=WL0 * compton_lambda(t, LAM0), amp=0.16,
                             snap=False, phase=-6 * clock.get_value(), color=ACCENT, stroke_width=5)

        def ray_in() -> VMobject:
            return sine_path(here + 3.2 * LEFT + 0.12 * UP, here + 0.2 * LEFT + 0.12 * UP, wavelength=WL0 * LAM0,
                             amp=0.16, snap=False, phase=-6 * clock.get_value(), color=ACCENT,
                             stroke_width=4).set_stroke(opacity=0.45)

        tri_at, p0_len = np.array([1.3, -1.7, 0.0]), 2.4

        def triangle() -> VGroup:
            t = theta.get_value()
            p1 = p0_len * LAM0 / compton_lambda(t, LAM0) * unit(-t)
            tip0 = tri_at + p0_len * RIGHT
            arrows = VGroup(
                Arrow(tri_at, tip0, buff=0, color=ACCENT, stroke_width=5, max_tip_length_to_length_ratio=0.1),
                Arrow(tri_at, tri_at + p1, buff=0, color=ACCENT, stroke_width=5,
                      max_tip_length_to_length_ratio=0.15).set_opacity(0.7),
                Arrow(tri_at + p1, tip0, buff=0, color=FLOW, stroke_width=5, max_tip_length_to_length_ratio=0.15))
            return arrows

        formula = tex(r"\lambda'-\lambda_0=\frac{h}{m_ec}\,(1-\cos\theta)").to_corner(UR, buff=0.45)
        box = SurroundingRectangle(formula, color=GOOD, buff=0.14)
        readout = always_redraw(lambda: tex(
            rf"\theta={np.degrees(theta.get_value()):.0f}^\circ\quad \lambda'/\lambda_0="
            rf"{compton_lambda(theta.get_value(), LAM0) / LAM0:.2f}", size=SMALL).next_to(box, DOWN, buff=0.3))
        tri_name = tex(r"\vec p_0=\vec p\,'+\vec p_e", size=SMALL, color=MUTED).next_to(tri_at + p0_len / 2 * RIGHT,
                                                                                       UP, buff=0.25)
        out_ray, in_ray, tri = always_redraw(ray), always_redraw(ray_in), always_redraw(triangle)
        self.play(FadeIn(in_ray), FadeIn(out_ray), FadeIn(tri), FadeIn(tri_name), Write(formula), run_time=1.4)
        self.play(Create(box), FadeIn(readout), run_time=0.8)
        self.play(theta.animate.set_value(PI), run_time=6.5, rate_func=linear)
        self.note(rf"At $180^\circ$, $\Delta\lambda=2h/m_ec={2 * LAMBDA_C_PM:.2f}$ pm: backscatter loses the most.")
        self.wait(2.0)

        # ---- the two diagrams QED adds ----
        self.next_section("both diagrams")
        self.play(*(FadeOut(m) for m in (in_ray, out_ray, tri, tri_name, readout, electron, aura, e_label)),
                  VGroup(formula, box).animate.scale(0.8).to_corner(UR, buff=0.35), run_time=1.0, rate_func=settle)
        mk = lambda t, c, s: tex(t, size=SMALL, color=c)
        s_ch = diagram({"a": (-4.3, -0.3), "b": (-2.2, -0.3)}, [
            ((-5.6, -1.8), "a", "fermion", r"e^-"), ((-5.6, 1.2), "a", "photon", r"\gamma"),
            ("a", "b", "fermion"),
            ("b", (-0.9, -1.8), "fermion", r"e^-"), ("b", (-0.9, 1.2), "photon", r"\gamma'"),
        ], color=INK, tex=mk)
        u_ch = diagram({"a": (2.2, -0.3), "b": (4.3, -0.3)}, [
            ((0.9, -1.8), "a", "fermion", r"e^-"), ((0.9, 1.2), "b", "photon", r"\gamma"),
            ("a", "b", "fermion"),
            ("b", (5.6, -1.8), "fermion", r"e^-"), ("a", (5.6, 1.2), "photon", r"\gamma'"),
        ], color=INK, tex=mk)
        for d in (s_ch, u_ch):
            for e in d.edges:
                if e.kind == "photon":
                    e.line.set_color(ACCENT)
        halos = VGroup(*(halo(e.line, layers=4, width=8, max_op=0.18) for d in (s_ch, u_ch) for e in d.edges
                         if e.kind == "fermion"))
        plus = tex("+", color=MUTED).move_to(0.1 * RIGHT + 0.3 * DOWN)
        self.play(s_ch.reveal(run_time=2.6, flash_color=ACCENT), u_ch.reveal(run_time=2.6, flash_color=ACCENT),
                  FadeIn(halos, run_time=2.6), FadeIn(plus))
        flow = ValueTracker(0.0)
        sparks = VGroup(s_ch.flow(flow, color=FLOW, spacing=1.1), u_ch.flow(flow, color=FLOW, spacing=1.1))
        self.play(FadeIn(sparks), flow.animate(rate_func=linear).set_value(2.5), run_time=2.5)
        self.note("Absorb then emit, or emit then absorb: QED adds both.")
        self.play(flow.animate(rate_func=linear).set_value(6.0), run_time=3.5)

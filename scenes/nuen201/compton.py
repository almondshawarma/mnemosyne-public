"""Compton scattering as a two-particle collision: what conservation of momentum and energy force on the scattered
photon, how the shift grows with angle, why the wavelength shift is the fundamental one, and the Feynman diagram.

For NUEN 201 L09 (re-sent 2026-10-13), slides 10-14. Aman's notes there: "what is the more 'fundamental' change?",
his x/y momentum equations, "highest energy shift @ 180°, photon loses most energy (backscattering)", and
"h/m_e c (fermi?)". The notation is the slide's: λ₀, λ', θ, φ, p_e, m_e.

Every angle, length, and number is computed: λ' from the Compton formula, φ and p_e from momentum conservation, and
λ_C, m_e c² from scipy.constants. The incoming photon has λ₀ = λ_C (a 511 keV gamma) so the stretch is easy to see;
for Compton's own X-rays the same Δλ is a few percent of λ₀.
"""

import numpy as np
from amanim import fermion, photon, sine_path, vertex
from mn_style import *
from scipy import constants

CLIP = {
    "title": "Compton scattering: the collision, the shift, the diagram",
    "courses": ["nuen201"],
    "topics": ["physics::quantum::wave-particle", "physics::particle::feynman-diagrams",
               "principle::conservation-of-momentum", "principle::conservation-of-energy"],
    "watch_for": "The momentum triangle closing at every angle, Δλ growing to 2λ_C at 180° whatever λ₀ is, and the "
                 "Feynman diagram as the same collision told in two vertices.",
    "scene": "Compton",
}

LAMBDA_C = constants.h / (constants.m_e * constants.c)          # metres
REST_KEV = constants.m_e * constants.c ** 2 / constants.e / 1e3  # m_e c², keV
LAM0 = 1.0                                                       # λ₀ in units of λ_C (a 511 keV gamma)

E_AT = np.array([-3.3, 0.4, 0.0])    # where the electron sits
WL0 = 0.42                           # λ₀ drawn on screen, scene units
RAY = 3.0                            # length of the outgoing photon drawn
P0_LEN = 2.6                         # |p₀| drawn in the momentum triangle
TRI_AT = np.array([1.2, -1.15, 0.0]) # the triangle's origin (right half, below the algebra)


def lam_prime(theta: float, lam0: float = LAM0) -> float:
    """λ' in units of λ_C: λ' = λ₀ + (h/m_e c)(1 − cos θ)."""
    return lam0 + (1 - np.cos(theta))


# harvest: Compton kinematics (recoil angle and momentum), with lam_prime above: a Compton preset in amanim
def recoil(theta: float, lam0: float = LAM0) -> tuple[float, float]:
    """The electron's recoil angle φ (above the axis) and |p_e|/|p₀|, from p₀ = p' + p_e with the photon leaving
    below the axis at θ (as on the slide)."""
    r = lam0 / lam_prime(theta, lam0)            # |p'|/|p₀| = λ₀/λ'
    px, py = 1 - r * np.cos(theta), r * np.sin(theta)
    return float(np.arctan2(py, px)), float(np.hypot(px, py))


def unit(angle: float) -> np.ndarray:
    return np.array([np.cos(angle), np.sin(angle), 0.0])


class Compton(MnScene):
    def construct(self) -> None:
        theta = ValueTracker(np.radians(60))
        flow = ValueTracker(0.0)                 # phase of the travelling waves

        electron = VGroup(Circle(radius=0.17, color=FLOW, fill_opacity=1, stroke_width=0),
                          tex("-", size=SMALL - 4, color=GROUND)).move_to(E_AT)
        axis = DashedLine(E_AT + 3.1 * LEFT, E_AT + 3.3 * RIGHT, color=MUTED, stroke_width=2, dash_length=0.1)

        def incoming() -> VMobject:
            return sine_path(E_AT + 3.1 * LEFT, E_AT + 0.25 * LEFT, wavelength=WL0, amp=0.16,
                             phase=-flow.get_value(), snap=False, color=ACCENT)

        def scattered() -> VMobject:
            t = theta.get_value()
            d = unit(-t)                          # below the axis, as on the slide
            return sine_path(E_AT + 0.25 * d, E_AT + RAY * d, wavelength=WL0 * lam_prime(t), amp=0.16,
                             phase=-flow.get_value(), snap=False, color=ACCENT)

        def recoiling() -> VMobject:
            phi, pe = recoil(theta.get_value())
            return Arrow(E_AT, E_AT + 2.0 * pe * unit(phi), buff=0.18, color=FLOW, stroke_width=6,
                         max_tip_length_to_length_ratio=0.18)

        def angles() -> VGroup:
            t = theta.get_value()
            phi, _ = recoil(t)
            ref = Line(E_AT, E_AT + RIGHT)
            th = Angle(ref, Line(E_AT, E_AT + unit(-t)), radius=0.75, other_angle=True, color=WARM, stroke_width=4)
            ph = Angle(ref, Line(E_AT, E_AT + unit(phi)), radius=1.05, color=FLOW, stroke_width=4)
            mid_t, mid_p = unit(-t / 2), unit(phi / 2)
            return VGroup(th, ph, tex(r"\theta", size=SMALL, color=WARM).move_to(E_AT + 1.05 * mid_t),
                          tex(r"\varphi", size=SMALL, color=FLOW).move_to(E_AT + 1.4 * mid_p))

        def triangle() -> VGroup:
            """p₀ = p' + p_e, head to tail, to scale: |p'| = |p₀| λ₀/λ'."""
            t = theta.get_value()
            p_prime = P0_LEN * LAM0 / lam_prime(t) * unit(-t)
            tip0 = TRI_AT + P0_LEN * RIGHT
            a0 = Arrow(TRI_AT, tip0, buff=0, color=ACCENT, stroke_width=5, max_tip_length_to_length_ratio=0.1)
            a1 = Arrow(TRI_AT, TRI_AT + p_prime, buff=0, color=ACCENT, stroke_width=5,
                       max_tip_length_to_length_ratio=0.15).set_opacity(0.7)
            a2 = Arrow(TRI_AT + p_prime, tip0, buff=0, color=FLOW, stroke_width=5, max_tip_length_to_length_ratio=0.15)
            return VGroup(a0, a1, a2,
                          tex(r"\vec p_0", size=SMALL, color=ACCENT).next_to(a0, UP, buff=0.1),
                          tex(r"\vec p\,'", size=SMALL, color=ACCENT).next_to(a1.get_center(), LEFT, buff=0.2),
                          tex(r"\vec p_e", size=SMALL, color=FLOW).next_to(a2.get_center(), DR, buff=0.12))

        flow.add_updater(lambda m, dt: m.increment_value(5.0 * dt))   # the waves keep travelling, even in waits
        self.add(flow)

        # ---- before: a photon meets an electron at rest ----
        self.next_section("before")
        self.play(FadeIn(electron), Create(axis))
        (wave_in,) = self.bring_in(incoming)
        lam0_lbl = tex(r"\lambda_0", size=SMALL, color=ACCENT).move_to(E_AT + 1.7 * LEFT + 0.55 * UP)
        self.play(FadeIn(lam0_lbl))
        self.note(r"A photon ($\lambda_0$) meets a free electron at rest: treat it as a collision of two particles.")
        self.wait(2)

        # ---- after: the photon leaves at θ with a longer λ', the electron recoils at φ ----
        self.next_section("after")
        # a faint copy of the incoming wave stays, so λ' can be compared with λ₀ by eye
        ghost = incoming().set_stroke(opacity=0.3)
        self.add(ghost)
        self.remove(wave_in)
        self.play(lam0_lbl.animate.set_opacity(0.5), run_time=0.6)
        wave_out, push, ang = self.bring_in(scattered, recoiling, angles)
        lamp_lbl = always_redraw(lambda: tex(r"\lambda'", size=SMALL, color=ACCENT).move_to(
            E_AT + 2.2 * unit(-theta.get_value()) + 0.5 * unit(-theta.get_value() + PI / 2)))
        self.play(FadeIn(lamp_lbl))
        self.note(r"After: the photon leaves at $\theta$ with a longer $\lambda'$; the electron recoils at $\varphi$.")
        self.wait(2)

        # ---- momentum and energy: Aman's x and y equations, with the electron's momentum p_e ----
        self.next_section("conservation")
        eqs = arrange_rows([
            tex(r"x:\ \frac{h}{\lambda_0} = p_e\cos\varphi + \frac{h}{\lambda'}\cos\theta", size=SMALL),
            tex(r"y:\ 0 = p_e\sin\varphi - \frac{h}{\lambda'}\sin\theta", size=SMALL),
            tex(r"\frac{hc}{\lambda_0} + m_ec^2 = \frac{hc}{\lambda'} + \sqrt{(p_ec)^2 + (m_ec^2)^2}", size=SMALL),
        ], buff=0.28).to_corner(UR, buff=0.4)
        for e in eqs:
            self.play(Write(e), run_time=1.1)
        (tri,) = self.bring_in(triangle)
        self.note(r"Momentum closes a triangle, $\vec p_0 = \vec p\,' + \vec p_e$, and energy is conserved too.")
        self.wait(2)
        result = tex(r"\lambda' - \lambda_0 = \frac{h}{m_ec}\,(1-\cos\theta)", size=BODY)
        result.move_to(eqs).to_edge(RIGHT, buff=0.5)
        box = SurroundingRectangle(result, color=GOOD, buff=0.14)
        self.play(ReplacementTransform(eqs, result), run_time=1.5)
        self.play(Create(box))
        self.note(r"Eliminate $\varphi$ and $p_e$ (square and add $x$, $y$; use the energy line): only $\theta$ is left.")
        self.wait(2.5)

        # ---- sweep θ: the shift grows to 2λ_C at backscatter, the triangle stays closed ----
        self.next_section("sweep")
        readout = always_redraw(lambda: backed(tex(
            rf"\theta = {np.degrees(theta.get_value()):.0f}^\circ \quad \lambda'/\lambda_0 = {lam_prime(theta.get_value()):.2f}",
            size=SMALL)).next_to(box, DOWN, buff=0.35))
        self.add(readout)
        self.play(theta.animate.set_value(np.radians(5)), run_time=2.5)
        self.play(theta.animate.set_value(np.radians(180)), run_time=6, rate_func=linear)
        self.note(r"At $180^\circ$ (backscatter) $\Delta\lambda = 2h/m_ec$: the photon loses the most energy.")
        self.wait(2)
        self.play(theta.animate.set_value(np.radians(90)), run_time=2)

        # ---- the Compton wavelength: picometres, not fermis ----
        self.next_section("lambda C")
        pm, fm = LAMBDA_C * 1e12, LAMBDA_C * 1e15
        lam_c = tex(rf"\frac{{h}}{{m_ec}} = {pm:.3f}\ \mathrm{{pm}} = {fm:.0f}\ \mathrm{{fm}}", size=SMALL + 2,
                    color=GOOD)
        lam_c.next_to(box, DOWN, buff=1.05).to_edge(RIGHT, buff=0.5)
        self.play(FadeIn(lam_c, shift=0.2 * UP))
        self.note(rf"$h/m_ec$ is about {pm:.1f} pm, not fermis: {fm:.0f} fm.")
        self.wait(2.5)

        # ---- the fundamental change: Δλ doesn't care what λ₀ was ----
        self.next_section("which shift")
        self.play(*[FadeOut(m) for m in (wave_out, push, ang, lamp_lbl, tri, readout, lam_c, electron, axis, ghost,
                                          lam0_lbl)])
        flow.clear_updaters()
        rows = VGroup()
        for k, lam0 in enumerate((1.0, 2.5)):
            y = 1.2 - 2.1 * k
            before = sine_path((-6.4, y, 0), (-2.1, y, 0), wavelength=WL0 * lam0, amp=0.16, snap=False, color=ACCENT)
            after = sine_path((-1.2, y, 0), (3.1, y, 0), wavelength=WL0 * lam_prime(PI / 2, lam0), amp=0.16,
                              snap=False, color=ACCENT)
            df = 1 - lam0 / lam_prime(PI / 2, lam0)        # Δf/f₀ = 1 − λ₀/λ'
            arrow = Arrow((-2.0, y, 0), (-1.3, y, 0), buff=0, color=MUTED, stroke_width=3)
            label = tex(rf"\lambda_0 = {lam0:g}\,\lambda_C:\ \ \Delta\lambda = \lambda_C,\ \ \Delta f/f_0 = {df:.0%}"
                        .replace("%", r"\%"), size=SMALL, color=INK).move_to((-1.6, y - 0.6, 0))
            rows.add(VGroup(before, arrow, after, label))
        self.play(FadeIn(rows, lag_ratio=0.2))
        self.note(r"Same $\theta$, two $\lambda_0$: same $\Delta\lambda$, different $\Delta f$. $\Delta\lambda$ is fundamental.")
        self.wait(3)

        # ---- the Feynman diagram: the same collision, two vertices ----
        self.next_section("Feynman diagram")
        self.play(FadeOut(rows), FadeOut(result), FadeOut(box))
        v1, v2 = np.array([-1.8, -0.2, 0]), np.array([1.4, -0.2, 0])
        legs = VGroup(
            fermion((-4.6, -2.0, 0), v1, color=FLOW), photon((-4.6, 1.6, 0), v1, color=ACCENT),
            fermion(v1, v2, color=FLOW),
            fermion(v2, (4.2, -2.0, 0), color=FLOW), photon(v2, (4.2, 1.6, 0), color=ACCENT),
            vertex(v1, color=INK, radius=0.08), vertex(v2, color=INK, radius=0.08))
        names = VGroup(tex(r"e^-", size=SMALL, color=FLOW).move_to((-4.95, -2.25, 0)),
                       tex(r"\gamma", size=SMALL, color=ACCENT).move_to((-4.95, 1.85, 0)),
                       tex(r"e^-", size=SMALL, color=FLOW).move_to((4.55, -2.25, 0)),
                       tex(r"\gamma\,'", size=SMALL, color=ACCENT).move_to((4.55, 1.85, 0)))
        time_arrow = VGroup(Arrow((-4.6, 2.7, 0), (3.6, 2.7, 0), buff=0, color=MUTED, stroke_width=2,
                                  max_tip_length_to_length_ratio=0.03),
                            words("time", size=SMALL, color=MUTED).move_to((4.3, 2.7, 0)))
        self.play(Create(legs, lag_ratio=0.25), FadeIn(names), run_time=3)
        self.note(r"Absorb, then emit. A second diagram emits first; QED adds both.")
        self.play(FadeIn(time_arrow))
        self.wait(3)

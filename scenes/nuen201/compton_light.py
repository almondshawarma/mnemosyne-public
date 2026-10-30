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

The electron reacts to what it absorbs and emits (amanim.reaction, Aman's idea on 2026-10-25): its glow, size, and
whiteness follow the energy it holds, E₀ in as the packet flows in, E' out as the scattered packet flows out, so it
leaves glowing with its recoil energy T_e. During the angle sweep the resting electron glows with T_e(θ)/E₀, brighter
toward backscatter. The glow is the energy bookkeeping, not decoration.
"""

import numpy as np
from amanim import (
    Flare,
    compton_energies,
    compton_lambda,
    compton_recoil,
    compton_steps,
    diagram,
    energy_held,
    fader,
    glow,
    react,
    settle,
    sine_path,
    spark,
    wave_packet,
)
from mn_style import *
from scipy import constants

LAMBDA_C_PM = constants.h / (constants.m_e * constants.c) * 1e12
LAM0 = 1.0                              # λ₀ in units of λ_C
WL0 = 0.42                              # λ₀ on screen, scene units
THETA = np.radians(60)                  # the collision's scattering angle

CLIP = {
    "title": "Compton scattering, in light",
    "courses": ["nuen201"],
    "topics": ["physics::quantum::wave-particle", "physics::particle::feynman-diagrams",
               "principle::conservation-of-momentum", "principle::conservation-of-energy"],
    "watch_for": "The electron flares as the packet flows in, holding all of E₀, and leaves still glowing with what "
                 "it kept, T_e = E₀ − E′; the scattered packet is visibly longer in wavelength, more so the further "
                 "back it goes, and the electron keeps more; it leaves slower than the photon (β = 0.66 at 60°); and "
                 "the two diagrams, absorb-then-emit and emit-then-absorb, that QED adds.",
    "scene": "ComptonLight",
    "look": "ethereal",
}
E_AT = np.array([-2.0, 0.4, 0.0])       # the electron, for the collision
PACKET = 2.6                            # a packet's length, scene units
APPROACH = 4.5                          # how far the photon travels to reach the electron, scene units
SPEED = 2.0                             # light's speed on screen, units a second; the electron moves at beta times it
UNFOLD = 1.6                            # how fast "now" sweeps along a diagram's time axis, units a second


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
        clock = self.clock()                        # ambient time: waves travel, the electron breathes
        # Live things (anything with an updater) fade through these, never FadeIn/FadeOut: Manim freezes an animated
        # mobject's updaters until the animation ends (the MN-MOTION check).
        show_e, show_elab, show_g = ValueTracker(0.0), ValueTracker(0.0), ValueTracker(1.0)

        # ---- a photon arrives, and flows into the electron ----
        self.next_section("a photon arrives")
        start = E_AT + APPROACH * LEFT
        travel = ValueTracker(0.0)                  # how far the photon has come, scene units: it never stops
        phi, _ = compton_recoil(THETA, LAM0)
        lam1 = compton_lambda(THETA, LAM0)
        d_out, d_e = unit(-THETA), unit(phi)
        b = beta(THETA)
        absorbed_at, emitted_at = APPROACH, APPROACH + PACKET   # travel when the front arrives, when the tail is in
        # Light leaves the moment the electron has faded in, so every time below is known now, on the scene clock.
        t_go = clock.get_value() + 1.0
        travel.add_updater(lambda m: m.set_value(SPEED * max(0.0, clock.get_value() - t_go)))
        t_in, t_out, flow = t_go + absorbed_at / SPEED, t_go + emitted_at / SPEED, PACKET / SPEED

        def recoil_at() -> np.ndarray:
            """The electron sits until the photon starts to leave, then moves at its computed speed, β c."""
            return E_AT + max(0.0, travel.get_value() - emitted_at) * b * d_e

        # The electron shows the energy it holds (amanim.reaction): E₀ flows in with the packet, E' flows out with the
        # scattered one, and it leaves holding T_e = E₀ − E', so its glow settles at T_e/E₀, computed, not chosen. It
        # flares and is jolted along +x (the photon's momentum) when the front hits, and again toward φ as it recoils,
        # and it hums while it holds energy (restless when excited, calm at rest).
        held = energy_held(compton_steps(THETA, LAM0, t_in=t_in, t_out=t_out, duration=flow))
        hits = [Flare(t_in, 0.6, RIGHT), Flare(t_out, 0.35, d_e)]
        electron = spark(FLOW, radius=0.13).move_to(E_AT)
        electron.add_updater(react(electron, clock, held, at=recoil_at, flares=hits, size=0.25, whiten=0.45,
                                   brighten=0.5, shake=0.07, hum=0.25, gain=show_e.get_value))
        aura = glow(E_AT, FLOW, radius=0.2, layers=8, max_op=0.12, spread=2.4)
        aura.add_updater(react(aura, clock, held, at=recoil_at, flares=hits, size=0.7, whiten=0.25, brighten=1.0,
                               shake=0.03, breathe=0.06, hum=0.25, gain=show_e.get_value))
        e_label = tex(r"e^-", size=SMALL, color=FLOW)
        e_label.add_updater(lambda m: m.next_to(recoil_at(), UP + LEFT, buff=0.25))
        e_label.add_updater(fader(e_label, show_elab.get_value))
        self.add(travel, aura, electron, e_label)

        # Sound, for an export (render.py --publish writes these as Resolve markers and WAV stems). A marker at each
        # hit, named with its strength, and two computed tones. A photon's pitch is its energy over E₀, so the scattered
        # one sounds λ₀/λ' lower: at 60° with λ₀ = λ_C that's 2/3, a perfect fifth down; the sweep glides it to 1/3 (an
        # octave and a fifth) at 180°. Loudness is how much of each packet is still there.
        for f, kind in zip(hits, ("absorb", "emit")):
            self.cue(kind, at=f.at, kind=kind, strength=f.strength)
        exit_d = (E_AT[1] + config.frame_height / 2) / np.sin(THETA)    # from the electron to the frame's bottom
        t_front_out = t_go + (emitted_at + exit_d) / SPEED
        sweep: dict[str, ValueTracker] = {}                              # filled when the angle sweep starts

        def ramp(t: float, start: float) -> float:
            return float(np.clip((t - start) / flow, 0.0, 1.0))

        def photon_tone() -> tuple[float, float] | None:
            t = clock.get_value()
            if sweep and sweep["show"].get_value() > 0:                  # the faint incoming ray of the sweep
                return 1.0, 0.45 * sweep["show"].get_value()
            return (1.0, 1.0 - ramp(t, t_in)) if t >= t_go else None

        def scattered_tone() -> tuple[float, float] | None:
            t = clock.get_value()
            if sweep and sweep["show"].get_value() > 0:
                return LAM0 / compton_lambda(sweep["theta"].get_value(), LAM0), 0.9 * sweep["show"].get_value()
            loud = ramp(t, t_out) - ramp(t, t_front_out)
            return (LAM0 / lam1, loud) if loud > 0 else None

        t_open = clock.get_value()

        def ambience() -> tuple[float, float]:
            """The bed: a faint buzz of particle energy, always there and swelling with what the electron holds (E₀
            while the photon is in it, T_e/E₀ as it leaves; during the sweep, what it would keep at that angle)."""
            t = clock.get_value()
            if sweep and sweep["show"].get_value() > 0:
                e0, _, t_e = compton_energies(sweep["theta"].get_value(), LAM0)
                level = t_e / e0 * sweep["show"].get_value()
            else:
                level = held(t) if not sweep else 0.0
            return 1.0, min(1.0, (t - t_open) / 1.5) * (0.35 + 0.65 * level)   # fades in over the first 1.5 s

        self.tone("photon", photon_tone)
        self.tone("scattered", scattered_tone)
        self.tone("ambience", ambience, base_hz=73.42, voice="buzz")    # D2: two octaves under the photon's D4
        self.play(show_e.animate.set_value(1.0), show_elab.animate.set_value(1.0), run_time=1.0, rate_func=settle)

        def incoming() -> VMobject:
            front = start + travel.get_value() * RIGHT
            return wave_packet(front - PACKET / 2 * RIGHT, RIGHT, wavelength=WL0 * LAM0, length=PACKET, amp=0.2,
                               color=ACCENT, stroke_width=5, absorb_at=E_AT)

        def outgoing() -> VMobject:
            front = E_AT + (travel.get_value() - emitted_at) * d_out
            return wave_packet(front - PACKET / 2 * d_out, d_out, wavelength=WL0 * lam1, length=PACKET, amp=0.2,
                               color=ACCENT, stroke_width=5, emit_from=E_AT)

        photon_in, photon_out = always_redraw(incoming), always_redraw(outgoing)
        g_label = tex(r"\gamma,\ \lambda_0", size=SMALL, color=ACCENT)
        g_label.add_updater(lambda m: m.move_to(start + (travel.get_value() - PACKET / 2) * RIGHT + 0.55 * UP))
        g_label.add_updater(fader(g_label, show_g.get_value))
        self.add(photon_in, photon_out, g_label)
        self.wait(0.9)
        self.note(r"A photon ($\lambda_0$) meets a free electron at rest.")
        self.wait((absorbed_at - SPEED * 1.5) / SPEED)                     # the front reaches the electron
        self.play(show_g.animate.set_value(0.0), run_time=PACKET / SPEED)   # ... and flows in, label riding it

        # ---- the collision: the photon flows out at θ, longer; the electron recoils at φ, slower ----
        self.next_section("the collision")
        trail = TracedPath(electron.get_center, stroke_color=FLOW, stroke_width=4, dissipating_time=1.2)
        self.add(trail)
        ref = Line(E_AT, E_AT + RIGHT)
        guides = VGroup(DashedLine(E_AT + 2.2 * LEFT, E_AT + 3.4 * RIGHT, color=MUTED, stroke_width=2,
                                   dash_length=0.08),
                        DashedLine(E_AT, E_AT + 3.4 * d_out, color=MUTED, stroke_width=2, dash_length=0.08),
                        DashedLine(E_AT, E_AT + 3.0 * d_e, color=MUTED, stroke_width=2, dash_length=0.08))
        arcs = VGroup(Angle(ref, Line(E_AT, E_AT + d_out), radius=0.9, other_angle=True, color=WARM, stroke_width=4),
                      Angle(ref, Line(E_AT, E_AT + d_e), radius=1.2, color=FLOW, stroke_width=4))
        names = VGroup(tex(r"\theta", size=SMALL, color=WARM).move_to(E_AT + 1.25 * unit(-THETA / 2)),
                       tex(r"\varphi", size=SMALL, color=FLOW).move_to(E_AT + 1.6 * unit(phi / 2)),
                       tex(r"\lambda' = %.1f\,\lambda_0" % (lam1 / LAM0), size=SMALL, color=ACCENT)
                       .move_to(E_AT + 2.2 * d_out + 1.25 * unit(-THETA + PI / 2)))
        self.wait(0.8)                                                     # it starts to leave
        self.play(Create(guides), Create(arcs), FadeIn(names), show_elab.animate.set_value(0.0), run_time=1.4,
                  rate_func=settle)
        self.note(rf"Out at $\theta$ with a longer $\lambda'$; the electron recoils at $\varphi$, at $\beta={b:.2f}$.")
        self.wait(0.6)                                                     # the section ends on the payoff

        # ---- the shift grows with angle; the momentum triangle stays closed ----
        self.next_section("the shift with angle")
        self.wait(0.6)                                                     # both still moving, leaving the frame
        here = E_AT + 1.0 * LEFT
        theta = ValueTracker(np.radians(20))
        e2 = spark(FLOW, radius=0.13).move_to(here)                       # a new electron at rest, a new collision
        aura2 = glow(here, FLOW, radius=0.2, layers=8, max_op=0.12, spread=2.4)
        e2_label = tex(r"e^-", size=SMALL, color=FLOW).next_to(e2, UP + LEFT, buff=0.12)
        show2 = ValueTracker(0.0)
        sweep.update(theta=theta, show=show2)                             # the tones follow the sweep from here

        def kept(_: float) -> float:
            """What the electron keeps, T_e/E₀, at the angle being swept: it grows toward backscatter."""
            e0, _, t_e = compton_energies(theta.get_value(), LAM0)
            return t_e / e0

        e2.add_updater(react(e2, clock, kept, size=0.25, whiten=0.45, brighten=0.5, shake=0, gain=show2.get_value))
        aura2.add_updater(react(aura2, clock, kept, size=0.7, whiten=0.25, brighten=1.0, shake=0, breathe=0.06,
                                gain=show2.get_value))

        def ray() -> VMobject:
            t = theta.get_value()
            d = unit(-t)
            return sine_path(here + 0.2 * d, here + 2.6 * d, wavelength=WL0 * compton_lambda(t, LAM0), amp=0.16,
                             snap=False, phase=-6 * clock.get_value(), color=ACCENT, stroke_width=5,
                             pin=(True, False))                            # tied to the electron by a node

        def ray_in() -> VMobject:
            return sine_path(here + 3.2 * LEFT + 0.12 * UP, here + 0.2 * LEFT + 0.12 * UP, wavelength=WL0 * LAM0,
                             amp=0.16, snap=False, phase=-6 * clock.get_value(), color=ACCENT,
                             stroke_width=4, pin=(False, True)).set_stroke(opacity=0.45)

        tri_at, p0_len = np.array([1.3, -1.7, 0.0]), 2.4

        def triangle() -> VGroup:
            t = theta.get_value()
            p1 = p0_len * LAM0 / compton_lambda(t, LAM0) * unit(-t)
            tip0 = tri_at + p0_len * RIGHT
            return VGroup(
                Arrow(tri_at, tip0, buff=0, color=ACCENT, stroke_width=5, max_tip_length_to_length_ratio=0.1),
                Arrow(tri_at, tri_at + p1, buff=0, color=ACCENT, stroke_width=5,
                      max_tip_length_to_length_ratio=0.15).set_opacity(0.7),
                Arrow(tri_at + p1, tip0, buff=0, color=FLOW, stroke_width=5, max_tip_length_to_length_ratio=0.15))

        formula = tex(r"\lambda'-\lambda_0=\frac{h}{m_ec}\,(1-\cos\theta)").to_corner(UR, buff=0.45)
        box = SurroundingRectangle(formula, color=GOOD, buff=0.14)
        readout = always_redraw(lambda: tex(
            rf"\theta={np.degrees(theta.get_value()):.0f}^\circ\quad \lambda'/\lambda_0="
            rf"{compton_lambda(theta.get_value(), LAM0) / LAM0:.2f}", size=SMALL).next_to(box, DOWN, buff=0.3))
        tri_name = tex(r"\vec p_0=\vec p\,'+\vec p_e", size=SMALL, color=MUTED).next_to(tri_at + p0_len / 2 * RIGHT,
                                                                                       UP, buff=0.25)
        out_ray, in_ray, tri = always_redraw(ray), always_redraw(ray_in), always_redraw(triangle)
        show_read = ValueTracker(0.0)
        for live in (in_ray, out_ray, tri):
            live.add_updater(fader(live, show2.get_value))
        readout.add_updater(fader(readout, show_read.get_value))
        self.add(aura2, e2, in_ray, out_ray, tri, readout)
        old = VGroup(guides, arcs, names)
        # the old shot fades while the new one builds: no empty frame between them
        self.play(LaggedStart(FadeOut(old, run_time=0.8),
                              AnimationGroup(show2.animate.set_value(1.0), FadeIn(e2_label),
                                             FadeIn(tri_name), Write(formula), run_time=1.4),
                              lag_ratio=0.35), rate_func=settle)
        self.remove(photon_in, photon_out, trail, electron, aura, e_label)   # long gone off the frame
        travel.clear_updaters()
        self.play(Create(box), show_read.animate.set_value(1.0), run_time=0.8)
        self.cue("sweep to 180°", kind="move")
        self.play(theta.animate.set_value(PI), run_time=6.5, rate_func=linear)
        self.cue("backscatter", kind="hit", strength=kept(0.0))           # it keeps the most here: 2/3 of E₀
        self.note(rf"At $180^\circ$, $\Delta\lambda=2h/m_ec={2 * LAMBDA_C_PM:.2f}$ pm: backscatter loses the most.")
        self.wait(2.0)

        # ---- the two diagrams QED adds ----
        self.next_section("both diagrams")
        mk = lambda t, c, s: tex(t, size=SMALL, color=c)
        glowing = {"fermion": {"layers": 4, "width": 8, "max_op": 0.18}}
        s_ch = diagram({"a": (-4.3, -0.3), "b": (-2.2, -0.3)}, [
            ((-5.6, -1.8), "a", "fermion", r"e^-"), ((-5.6, 1.2), "a", "photon", r"\gamma"),
            ("a", "b", "fermion"),
            ("b", (-0.9, -1.8), "fermion", r"e^-"), ("b", (-0.9, 1.2), "photon", r"\gamma'"),
        ], color=INK, tex=mk, halos=glowing)
        u_ch = diagram({"a": (2.2, -0.3), "b": (4.3, -0.3)}, [
            ((0.9, -1.8), "a", "fermion", r"e^-"), ((0.9, 1.2), "b", "photon", r"\gamma"),
            ("a", "b", "fermion"),
            ("b", (5.6, -1.8), "fermion", r"e^-"), ("a", (5.6, 1.2), "photon", r"\gamma'"),
        ], color=INK, tex=mk, halos=glowing)
        for d in (s_ch, u_ch):
            for e in d.edges:
                if e.kind == "photon":
                    e.line.set_color(ACCENT)
        plus = tex("+", color=MUTED).move_to(0.1 * RIGHT + 0.3 * DOWN)
        leaving = VGroup(tri_name, e2_label)                              # the still ones; the live ones fade below
        # Both diagrams unfold along their time axis (left to right) on the scene clock (amanim's Diagram.unfold): a
        # "now" sweeps right, every line draws up to it, each vertex flashes when it's reached. On the left (s-channel)
        # the electron and photon arrive together, the electron absorbs, then emits γ' and both leave together. On the
        # right (u-channel) it emits γ' at the first vertex while γ is still on its way, and absorbs γ at the second:
        # that's why its photon lines cross. Each diagram is one term of the amplitude, and the terms add; read in time
        # like this, each is also only one of its two orderings (the other makes and annihilates a pair). Waves travel
        # while they draw, so nothing snaps when the sparks start (reveal() drew them frozen, then they jumped).
        t_unfold = clock.get_value() + 0.4
        spans = [s_ch.when(p) for e in s_ch.edges for p in (e.start, e.end)]
        sweep_s = (max(spans) - min(spans)) / UNFOLD                        # how long the sweep takes
        live = VGroup(s_ch.unfold(clock, start=t_unfold, speed=UNFOLD, flash_color=ACCENT),
                      u_ch.unfold(clock, start=t_unfold, speed=UNFOLD, flash_color=ACCENT))
        for v, what in (("a", "s absorbs, u emits"), ("b", "s emits, u absorbs")):   # the same times on both sides
            self.cue(what, at=t_unfold + (s_ch.when(s_ch.vertices[v]) - min(spans)) / UNFOLD, kind="hit", strength=0.5)
        self.add(live)
        self.play(FadeOut(leaving), show2.animate.set_value(0.0), show_read.animate.set_value(0.0),
                  VGroup(formula, box).animate.scale(0.8).to_corner(UR, buff=0.35), FadeIn(plus), run_time=1.0,
                  rate_func=smooth)     # not settle: a fast-out fade dropped 60% in one frame, which reads as a cut
        self.wait(max(0.0, t_unfold + sweep_s - clock.get_value()))         # the sweep finishes
        # The process runs on the scene clock, so it keeps going through the caption: a tracker animated only inside
        # plays stops whenever a caption fades in (the study saw a 0.5 s stall at each one).
        sparks = VGroup(s_ch.flow(clock, color=FLOW, spacing=1.1), u_ch.flow(clock, color=FLOW, spacing=1.1))
        show_flow = ValueTracker(0.0)
        sparks.add_updater(fader(sparks, show_flow.get_value))           # they ride the lines while they fade in
        self.add(sparks)
        self.play(show_flow.animate.set_value(1.0), run_time=1.5)
        self.note("Absorb then emit, or emit then absorb: QED adds both amplitudes.")
        self.wait(3.5)

"""Gallery: Feynman diagrams that move, in the darker, glowing look.

amanim.feynman builds the diagram from named vertices and edges (`diagram`), labels external legs past their free
ends, and draws it in time order (`reveal`: each line grows from its earlier end, each vertex flashes as it's reached).
`flow(clock)` then keeps it alive: sparks ride the fermion lines forward in time (the positron's arrow points back, but
it still travels forward), and the photon and gluon lines travel, all from one ValueTracker. A blurred depth plate of
the QED Lagrangian drifts slowly behind it, for depth. `halo` gives the lines a glow inside
Cairo; amanim.post's bloom (`python -m amanim.post in.mp4 out.mp4`) is the real light, run on the render afterwards.
The physics: e⁺e⁻ → γ* → μ⁺μ⁻ in the s-channel, then the strong-force version, q q̄ → g → q q̄, with a gluon.
"""

from amanim import depth_plate, diagram, halo, settle
from mn_style import *

CLIP = {
    "title": "Gallery: Feynman diagrams that move",
    "technique": "amanim.feynman diagram/reveal/flow, depth_plate parallax, halo; bloom via amanim.post",
    "watch_for": "The positron's arrow points backward, but its spark still travels forward in time: an antiparticle "
                 "is a particle line running backward, not a particle moving backward.",
    "scene": "Feynman",
    "look": "ethereal",          # services/manim/job.py puts the render through amanim.post: bloom, fringe, vignette
}

V = {"a": (-1.3, 0.2), "b": (1.3, 0.2)}
IN_TOP, IN_BOT, OUT_TOP, OUT_BOT = (-4.2, 2.2), (-4.2, -1.8), (4.2, 2.2), (4.2, -1.8)


def qed() -> list[tuple]:
    return [(IN_TOP, "a", "fermion", r"e^-"), (IN_BOT, "a", "antifermion", r"e^+"), ("a", "b", "photon", r"\gamma"),
            ("b", OUT_TOP, "fermion", r"\mu^-"), ("b", OUT_BOT, "antifermion", r"\mu^+")]


def qcd() -> list[tuple]:
    return [(IN_TOP, "a", "fermion", r"q"), (IN_BOT, "a", "antifermion", r"\bar q"), ("a", "b", "gluon", r"g"),
            ("b", OUT_TOP, "fermion", r"q"), ("b", OUT_BOT, "antifermion", r"\bar q")]


def lagrangian() -> VGroup:
    """The faint math behind everything: big, tilted, out of focus."""
    big = MathTex(r"\mathcal{L}=\bar\psi\,(i\gamma^\mu D_\mu - m)\,\psi-\tfrac14 F_{\mu\nu}F^{\mu\nu}",
                  color=MUTED).scale(1.9).rotate(-0.12).move_to(0.6 * UP + 0.4 * LEFT)
    small = MathTex(r"D_\mu=\partial_\mu+ieA_\mu", color=MUTED).scale(1.3).rotate(-0.12).move_to(2.6 * DOWN
                                                                                                + 2.8 * RIGHT)
    return VGroup(big, small)


def styled(d: VGroup, wave_color: ManimColor) -> VGroup:
    """The propagator in its own colour, and a halo behind each fermion line (a travelling wave would leave a static
    halo behind; bloom lights it instead)."""
    d.edges[2].line.set_color(wave_color)
    return VGroup(*(halo(e.line, layers=4, width=8, max_op=0.2) for e in d.edges if e.kind.endswith("fermion")))


class Feynman(MnScene):
    def construct(self) -> None:
        self.next_section("electron and positron annihilate")
        plate = depth_plate(lagrangian(), blur=0.05, opacity=0.3)
        self.add(plate)
        plate.add_updater(lambda m, dt: m.shift(0.05 * dt * (LEFT + 0.3 * DOWN)))   # slow drift: parallax
        d = diagram(V, qed(), color=INK, label_color=INK, tex=lambda t, c, s: tex(t, color=c))
        glow_lines = styled(d, ACCENT)
        time = VGroup(Arrow(5.2 * LEFT + 3.1 * DOWN, 3.0 * LEFT + 3.1 * DOWN, buff=0, color=MUTED, stroke_width=3),
                      words("time", size=SMALL, color=MUTED).move_to(5.6 * LEFT + 3.1 * DOWN + 0.5 * RIGHT))
        time[1].next_to(time[0], UP, buff=0.12, aligned_edge=LEFT)
        self.play(d.reveal(run_time=3.5, flash_color=FLOW), FadeIn(glow_lines, run_time=3.5), FadeIn(time))
        self.note(r"$e^-e^+\to\gamma^*\to\mu^-\mu^+$, drawn in time order")

        self.next_section("the process in motion")
        clock = ValueTracker(0)
        sparks = d.flow(clock, color=FLOW)
        self.play(FadeIn(sparks), clock.animate(rate_func=linear).set_value(2.5), run_time=2.5)
        self.note("The positron's arrow points back; its spark still moves forward in time.")
        self.play(clock.animate(rate_func=linear).set_value(5.5), run_time=3)

        self.next_section("the strong force")
        g = diagram(V, qcd(), color=INK, label_color=INK, tex=lambda t, c, s: tex(t, color=c))
        g_glow = styled(g, WARM)
        for e in d.edges:
            e.line.clear_updaters()
        self.play(FadeOut(sparks), FadeOut(glow_lines), run_time=0.4)
        self.play(*(ReplacementTransform(a, b) for a, b in zip(d.edges, g.edges, strict=True)),
                  FadeIn(g_glow), run_time=1.6, rate_func=settle)
        self.add(*g.dots.values())
        self.remove(*d.dots.values())
        clock2 = ValueTracker(0)
        sparks2 = g.flow(clock2, color=FLOW)
        self.play(FadeIn(sparks2), clock2.animate(rate_func=linear).set_value(2.5), run_time=2.5)
        self.note("Same shape, the strong force: a gluon carries it.")
        self.play(clock2.animate(rate_func=linear).set_value(5.5), run_time=3)

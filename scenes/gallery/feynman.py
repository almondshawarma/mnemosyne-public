"""Gallery: Feynman diagrams that move, in the darker, glowing look.

amanim.feynman builds the diagram from named vertices and edges (`diagram`), labels external legs past their free
ends, and draws it along its time axis on the scene clock (`unfold`): a "now" sweeps right, every line draws up to it
(the electron and positron arrive together, the muons leave together), and each vertex flashes as it's reached.
`flow(clock)` then adds sparks riding the fermion lines forward in time (the positron's arrow points back, but it
still travels forward). The photon has been travelling since it was drawn: unfold never pauses an updater, so nothing
snaps into motion (the old `reveal` drew waves frozen, then they jumped when flow started). A blurred depth plate of
the QED Lagrangian drifts slowly behind it, for depth. The fermion lines' halos (`diagram(halos=...)`) draw with their
lines; amanim.post's bloom is the real light, run on the render afterwards.
The physics: e⁺e⁻ → γ* → μ⁺μ⁻ in the s-channel, then the strong-force version, q q̄ → g → q q̄, with a gluon. A
diagram drawn in time like this is one reading of it: its amplitude also counts the other order of its vertices.
"""

from amanim import depth_plate, diagram, fader, settle
from mn_style import *

CLIP = {
    "title": "Gallery: Feynman diagrams that move",
    "technique": "amanim.feynman diagram/unfold/flow, depth_plate parallax, halos; bloom via amanim.post",
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


GLOW = {"layers": 4, "width": 8, "max_op": 0.2}       # the fermion lines' halos: they draw with their lines
SWEEP = 2.4                                         # how fast "now" moves along the time axis, units a second


def build(edges: list[tuple], wave_color: ManimColor) -> VGroup:
    """A diagram with glowing fermion lines and its propagator in its own colour (a travelling wave gets no halo:
    bloom lights it instead)."""
    d = diagram(V, edges, color=INK, label_color=INK, tex=lambda t, c, s: tex(t, color=c),
                halos={"fermion": GLOW, "antifermion": GLOW})
    d.edges[2].line.set_color(wave_color)
    return d


class Feynman(MnScene):
    def construct(self) -> None:
        self.next_section("electron and positron annihilate")
        clock = self.clock()
        plate = depth_plate(lagrangian(), blur=0.05, opacity=0.3)
        self.add(plate)
        plate.add_updater(lambda m, dt: m.shift(0.05 * dt * (LEFT + 0.3 * DOWN)))   # slow drift: parallax
        d = build(qed(), ACCENT)
        time = VGroup(Arrow(5.2 * LEFT + 3.1 * DOWN, 3.0 * LEFT + 3.1 * DOWN, buff=0, color=MUTED, stroke_width=3),
                      words("time", size=SMALL, color=MUTED).move_to(5.6 * LEFT + 3.1 * DOWN + 0.5 * RIGHT))
        time[1].next_to(time[0], UP, buff=0.12, aligned_edge=LEFT)
        # unfold: no play draws the diagram, the clock does, so the photon travels while it draws. The play below
        # only brings in the time arrow; the wait lets the sweep finish (from the earliest end to the latest).
        start = clock.get_value() + 0.3
        live = d.unfold(clock, start=start, speed=SWEEP, flash_color=FLOW)
        self.add(live)
        self.play(FadeIn(time), run_time=1.0)
        span = (OUT_TOP[0] - IN_TOP[0]) / SWEEP
        self.wait(max(0.0, start + span - clock.get_value()))
        self.note(r"$e^-e^+\to\gamma^*\to\mu^-\mu^+$, drawn in time order")

        self.next_section("the process in motion")
        # The process runs on the scene clock, so it never stops for a caption; the sparks ride while they fade in
        # (FadeIn would pause their updaters, and they'd jump: MN-MOTION).
        sparks = d.flow(clock, color=FLOW)
        show = ValueTracker(0.0)
        sparks.add_updater(fader(sparks, show.get_value))
        self.add(sparks)
        self.play(show.animate.set_value(1.0), run_time=2.5)
        self.note("The positron's arrow points back; its spark still moves forward in time.")
        self.wait(3)

        self.next_section("the strong force")
        g = build(qcd(), WARM)
        self.play(show.animate.set_value(0.0), run_time=0.4)
        self.remove(sparks, live[1])                                     # the sparks, and the spent vertex flashes
        for m in d.get_family():                                         # the photon stops travelling here: it
            m.clear_updaters()                                           # becomes the gluon, halos and all
        self.play(*(ReplacementTransform(a, b) for a, b in zip(d.edges, g.edges, strict=True)),
                  run_time=1.6, rate_func=settle)
        self.add(*g.dots.values())
        self.remove(*d.dots.values())
        sparks2 = g.flow(clock, color=FLOW)
        show2 = ValueTracker(0.0)
        sparks2.add_updater(fader(sparks2, show2.get_value))
        self.add(sparks2)
        self.play(show2.animate.set_value(1.0), run_time=2.5)
        self.note("Same shape, the strong force: a gluon carries it.")
        self.wait(3)

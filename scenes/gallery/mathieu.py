"""Gallery: the Mathieu stability diagram, and the motion at one point of it, both computed and both driven by one q.

amanim.plots: `plot_axes` for two quiet plots, `mathieu_diagram` for the stable region (Floquet: |trace| < 2 over one
drive period, every grid point integrated at once), and `rk4` for the motion itself. One ValueTracker holds q; the dot
on the diagram, the curve u(ξ), the readout, and the stable/unstable word all read it, so they can't disagree. This is
the Paul trap's diagram: a = 0 is pure RF, and q past about 0.908 throws the ion out.
"""

from amanim import mathieu_diagram, mathieu_stable, plot_axes, rk4
from mn_style import *

CLIP = {
    "title": "Gallery: the Mathieu stability diagram and the motion it predicts",
    "technique": "plots: plot_axes, mathieu_diagram (stable region), rk4 driving a live curve; one ValueTracker drives "
                 "a dot, a curve, a readout, and a label",
    "watch_for": "As the dot leaves the shaded region the motion stops being a bounded wobble and grows each period.",
    "scene": "Mathieu",
}

A = 0.0                    # pure RF drive: no DC on the electrodes
XI_END = 6 * PI            # six drive periods of the Mathieu time (period pi)
U_MAX = 3.0                # the plot's height: a growing solution is drawn until it leaves


def motion(a: float, q: float) -> np.ndarray:
    """u(ξ) from u = 1, u' = 0, as points (ξ, u), stopped where |u| leaves the plot."""
    ts, xs = rk4(lambda x, t: (x[1], -(a - 2 * q * np.cos(2 * t)) * x[0]), (1.0, 0.0), XI_END, dt=0.03)
    out = np.abs(xs[:, 0]) > U_MAX
    stop = int(np.argmax(out)) if out.any() else len(ts)
    return np.column_stack([ts[:stop], xs[:stop, 0]])


class Mathieu(MnScene):
    def construct(self) -> None:
        q = ValueTracker(0.5)
        diagram_axes = plot_axes((0, 1, 0.2), (-0.6, 0.6, 0.2), x_length=5.0, y_length=4.2, color=MUTED,
                                 labels=("q", "a")).shift(3.5 * LEFT + 0.5 * UP)
        stable = mathieu_diagram(diagram_axes, color=ACCENT, opacity=0.3)
        motion_axes = plot_axes((0, XI_END, PI), (-U_MAX, U_MAX, 1), x_length=5.6, y_length=3.6, color=MUTED,
                                labels=(r"\xi", "u")).shift(3.2 * RIGHT + 0.5 * UP)

        dot = always_redraw(lambda: Dot(diagram_axes.c2p(q.get_value(), A), color=INK, radius=0.08))
        curve = always_redraw(lambda: motion_axes.plot_line_graph(
            *motion(A, q.get_value()).T, add_vertex_dots=False, line_color=FLOW, stroke_width=4))
        readout = DecimalNumber(q.get_value(), num_decimal_places=2, font_size=SMALL + 4, color=INK)
        readout.add_updater(lambda d: d.set_value(q.get_value()))
        # Bottoms aligned, not centers: the number has no descender and "q" does, so centering drops the number.
        q_label = VGroup(tex("q=", size=SMALL + 4), readout).arrange(RIGHT, buff=0.12, aligned_edge=DOWN)
        q_label.next_to(diagram_axes, DOWN, buff=0.3)
        verdict = always_redraw(lambda: words("bounded" if mathieu_stable(A, q.get_value()) else "grows",
                                              size=SMALL + 4, color=GOOD if mathieu_stable(A, q.get_value()) else BAD)
                                .next_to(motion_axes, DOWN, buff=0.3))

        self.next_section("the diagram")
        self.play(Create(diagram_axes), FadeIn(stable))
        self.note(r"Where $u''+(a-2q\cos 2\xi)\,u=0$ stays bounded: the shaded region.")

        self.next_section("inside")
        self.play(FadeIn(dot), Create(motion_axes), FadeIn(q_label), Create(curve), FadeIn(verdict))
        self.note(r"Inside, at $q=0.5$: a fast wiggle on a slow, bounded oscillation.")

        self.next_section("across the edge")
        self.play(q.animate.set_value(0.95), run_time=4, rate_func=linear)
        self.note(r"Past $q\approx0.908$ the amplitude grows every period: the ion leaves.")
        self.wait(1)

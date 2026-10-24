"""Gallery: a plot driven by a ValueTracker: change the parameter, and the curve and its numbers follow.

One ValueTracker holds the damping ratio ζ. The curve (always_redraw), the envelope, and the readout all read it, so a
single `tracker.animate.set_value(...)` moves everything at once. The readout's value is set before it fades in (the
house rule on changing counts).
"""

from mn_style import *

CLIP = {
    "title": "Gallery: a live plot driven by a ValueTracker",
    "technique": "plots: Axes + always_redraw(plot) + DecimalNumber, all reading one ValueTracker",
    "watch_for": "As ζ grows the oscillation dies faster, and at ζ = 1 it stops oscillating at all.",
    "scene": "LivePlot",
}

W0 = 2 * PI       # natural angular frequency, rad/s: one oscillation a second when undamped


def x(t: float, zeta: float) -> float:
    """Free response of x'' + 2ζω0 x' + ω0² x = 0 with x(0) = 1, x'(0) = 0, under- or critically damped."""
    if zeta < 1:
        wd = W0 * np.sqrt(1 - zeta ** 2)
        return np.exp(-zeta * W0 * t) * (np.cos(wd * t) + zeta * W0 / wd * np.sin(wd * t))
    return np.exp(-W0 * t) * (1 + W0 * t)


class LivePlot(MnScene):
    def construct(self) -> None:
        zeta = ValueTracker(0.05)
        axes = Axes(x_range=[0, 3, 1], y_range=[-1, 1, 0.5], x_length=9, y_length=4.2,
                    axis_config={"color": MUTED, "include_tip": False}).shift(0.2 * UP)
        names = VGroup(tex("t", size=SMALL, color=MUTED).next_to(axes.x_axis, RIGHT, buff=0.15),
                       tex("x", size=SMALL, color=MUTED).next_to(axes.y_axis, UP, buff=0.15))
        curve = always_redraw(lambda: axes.plot(lambda t: x(t, zeta.get_value()), x_range=[0, 3, 0.01],
                                                color=ACCENT, stroke_width=4))
        envelope = always_redraw(lambda: axes.plot(lambda t: np.exp(-zeta.get_value() * W0 * t), x_range=[0, 3],
                                                   color=MUTED, stroke_width=2).set_stroke(opacity=0.6))
        readout = DecimalNumber(zeta.get_value(), num_decimal_places=2, font_size=BODY, color=ACCENT)
        label = VGroup(tex(r"\zeta=", color=ACCENT), readout).arrange(RIGHT, buff=0.1).to_corner(UR, buff=0.6)
        readout.add_updater(lambda d: d.set_value(zeta.get_value()))

        self.next_section("light damping")
        self.play(Create(axes), FadeIn(names), Create(curve), Create(envelope), FadeIn(label))
        self.note(r"$x''+2\zeta\omega_0x'+\omega_0^2x=0$, released from $x=1$.")

        self.next_section("more damping")
        self.play(zeta.animate.set_value(0.3), run_time=3)
        self.note(r"More damping: the envelope $e^{-\zeta\omega_0 t}$ closes faster.")

        self.next_section("critical")
        # The envelope bounds an oscillation; there's none past ζ = 1, so it goes first (at the start of the section).
        self.play(FadeOut(envelope))
        self.play(zeta.animate.set_value(1.0), run_time=3)
        self.note(r"At $\zeta=1$ it no longer oscillates: critical damping.")
        self.wait(1)

"""Fourier series as projection: a coefficient is how much of f lies along one sine.

Channel, course-paced (MATH 309, late November), and the first use of amanim.spectral, which the winter-break DFT
builds on.

What's computed (amanim.spectral, tested in amanim's tests/test_blocks.py: orthogonality, the square wave's 4/(πk),
Parseval, the Gibbs constant):
- Every coefficient is an inner product, ∫ f(x) sin(kx) dx, by the trapezoid rule over 8001 samples: the shaded
  area under f·sin kx is that integral, drawn. Nothing on screen is the closed form 4/(πk); it comes out.
- The partial sums are the coefficients added back up.
- The Gibbs overshoot is measured from the drawn partial sums (their maximum just past the jump), for each N.
- The orthogonality table is ⟨sin mx, sin nx⟩/π computed the same way.

Motion rule: the growing sum is an always_redraw read from a tracker; nothing live is FadeIn'd.
"""

import numpy as np
from amanim import halo, pulse_factor, settle
from amanim.plots import plot_axes
from amanim.spectral import basis, fourier_coefficients, inner, partial_sum, square_wave
from mn_style import *

CLIP = {
    "title": "Fourier series as projection",
    "courses": ["math309"],
    "topics": ["math::fourier", "math::linear-algebra::orthogonality", "principle::superposition"],
    "watch_for": "Each coefficient is an area: f times one sine, integrated. The even sines' areas cancel to zero. "
                 "Added back up, the sines rebuild the square wave, except for an overshoot at the jump that stays "
                 "at 9% however many terms you add.",
    "scene": "FourierProjection",
    "look": "ethereal",
}

TWO_PI = 2 * np.pi
A0, A, B = fourier_coefficients(square_wave, 99)          # each one an inner product, computed
XF = np.linspace(0, TWO_PI, 1601)
XG = np.linspace(1e-4, 0.5, 4000)                          # just past the jump at 0, where Gibbs lives


def overshoot(n: int) -> float:
    """How far the n-term partial sum overshoots, as a share of the jump (2)."""
    return float((partial_sum(A0, A, B, XG, terms=n).max() - 1) / 2)


# The numbers the scene says out loud, checked against the closed forms the code never used.
assert abs(B[0] - 4 / np.pi) < 2e-3 and abs(B[1]) < 2e-3 and abs(B[2] - 4 / (3 * np.pi)) < 2e-3
assert 0.085 < overshoot(99) < 0.095


class FourierProjection(MnScene):
    def construct(self) -> None:
        clock = self.clock()
        terms = ValueTracker(1)
        show_sum = ValueTracker(0.0)
        show_bars = ValueTracker(1.0)    # the bar chart, faded for the last shot

        ax = plot_axes((0, TWO_PI, np.pi / 2), (-1.6, 1.6, 1), x_length=7.0, y_length=2.6, color=MUTED,
                       labels=("x", "f"), label_size=SMALL).move_to((-2.7, 1.45, 0))
        px = plot_axes((0, TWO_PI, np.pi / 2), (-1.2, 1.2, 1), x_length=7.0, y_length=1.7, color=MUTED,
                       labels=("x", r"f\,\sin kx"), label_size=SMALL).move_to((-2.7, -1.45, 0))
        bx = plot_axes((0, 12, 1), (-0.2, 1.4, 0.5), x_length=3.6, y_length=2.6, color=MUTED,
                       labels=("k", "b_k"), label_size=SMALL).move_to((4.45, 1.45, 0))
        f_curve = ax.plot(square_wave, x_range=(0.001, TWO_PI - 0.001, 0.002), color=INK, stroke_width=3,
                          use_smoothing=False, discontinuities=[np.pi], dt=0.004)

        # ---- 1 · a function is a vector ----
        self.next_section("a function is a vector")
        xs = np.linspace(TWO_PI / 64, TWO_PI - TWO_PI / 64, 32)
        sticks = VGroup(*(Line(ax.c2p(x, 0), ax.c2p(x, square_wave(x)), color=ACCENT, stroke_width=3)
                          for x in xs))
        self.play(FadeIn(ax), Create(sticks, lag_ratio=0.03), run_time=1.6, rate_func=settle)
        self.note("Treat a function like a vector: one component for every x.")
        self.wait(1.5)
        self.play(ReplacementTransform(sticks, f_curve), run_time=1.4)
        self.wait(1.5)

        # ---- 2 · project it onto one sine ----
        self.next_section("projection")

        def projection(k: int) -> VGroup:
            """sin kx over f, f·sin kx below with its area shaded (green above, red below), and the numbers."""
            _, s = basis(k, XF)
            prod = square_wave(XF) * s
            area = inner(square_wave(XF), s, XF)
            sine = ax.plot(lambda x: np.sin(k * x), color=FLOW, stroke_width=3)
            prod_curve = VMobject(stroke_color=FLOW, stroke_width=2.5)
            prod_curve.set_points_as_corners([px.c2p(x, y) for x, y in zip(XF[::4], prod[::4], strict=True)])
            fills = VGroup()
            for sign, col in ((1, GOOD), (-1, BAD)):
                y = np.where(sign * prod > 0, prod, 0.0)
                pts = [px.c2p(x, v) for x, v in zip(XF[::4], y[::4], strict=True)]
                region = VMobject(stroke_width=0, fill_color=col, fill_opacity=0.35)
                region.set_points_as_corners([px.c2p(0, 0), *pts, px.c2p(TWO_PI, 0), px.c2p(0, 0)])
                fills.add(region)
            kx = "x" if k == 1 else f"{k}x"                     # sin x, not sin 1x
            nums = backed(VGroup(
                tex(rf"\langle f,\sin {kx}\rangle={area:.3f}", size=SMALL),
                tex(rf"b_{k}=\frac{{\langle f,\sin {kx}\rangle}}{{\langle \sin {kx},\sin {kx}\rangle}}={B[k - 1]:.3f}",
                    size=SMALL, color=ACCENT)).arrange(DOWN, aligned_edge=LEFT, buff=0.15), opacity=0.85)
            nums.next_to(bx, DOWN, buff=0.45)
            return VGroup(sine, fills, prod_curve, nums)

        def bar(k: int) -> Rectangle:
            h = B[k - 1]
            r = Rectangle(width=0.18, height=max(abs(bx.c2p(0, h)[1] - bx.c2p(0, 0)[1]), 0.01), stroke_width=0,
                          fill_color=ACCENT, fill_opacity=0.9)
            return r.move_to(bx.c2p(k, h / 2))

        p1 = projection(1)
        self.play(FadeIn(px), FadeIn(bx), Create(p1[0]), run_time=1.2)
        self.play(FadeIn(p1[1]), Create(p1[2]), run_time=1.2, rate_func=settle)
        self.play(FadeIn(p1[3]), GrowFromEdge(b1 := bar(1), DOWN), run_time=1.0)
        self.note(r"Multiply by $\sin x$ and integrate: the shaded area is the projection.")
        self.wait(3.0)
        p2 = projection(2)
        self.play(ReplacementTransform(p1, p2), run_time=1.4)
        b2 = bar(2)
        self.play(GrowFromEdge(b2, DOWN), run_time=0.6)
        self.note(r"Against $\sin 2x$ the green and red areas cancel: no $\sin 2x$ in a square wave.")
        self.wait(3.0)
        p3 = projection(3)
        self.play(ReplacementTransform(p2, p3), run_time=1.4)
        self.play(GrowFromEdge(b3 := bar(3), DOWN), run_time=0.6)
        bars = VGroup(b1, b2, b3)
        rest = VGroup(*(bar(k) for k in range(4, 12)))
        self.play(LaggedStart(*(GrowFromEdge(r, DOWN) for r in rest), lag_ratio=0.15), run_time=1.6)
        bars.add(*rest)
        self.note(r"Every odd sine gets $\tfrac{4}{\pi k}$; every even one, zero.")
        self.wait(2.5)

        # ---- 3 · add them back ----
        self.next_section("add them back")

        def the_sum() -> VGroup:
            n, op = round(terms.get_value()), show_sum.get_value()
            y = partial_sum(A0, A, B, XF, terms=n)
            curve = VMobject(stroke_color=ACCENT, stroke_width=3.5, stroke_opacity=op)
            curve.set_points_as_corners([ax.c2p(x, float(np.clip(v, -1.6, 1.6))) for x, v in zip(XF, y, strict=True)])
            k = pulse_factor(clock.get_value(), rate=0.4, depth=0.3)
            lit = VGroup(*(b.copy().set_fill(opacity=0.9 * op * show_bars.get_value()) for i, b in enumerate(bars) if i < n))
            lit.set_stroke(ACCENT, width=3, opacity=0.6 * op * k * show_bars.get_value())
            return VGroup(halo(curve, layers=3, width=8, max_op=0.22 * op), curve, lit)

        count = DecimalNumber(1, num_decimal_places=0, font_size=SMALL, color=ACCENT)
        n_lab = tex(r"N=", size=SMALL, color=ACCENT).next_to(ax, UP, buff=0.15).align_to(ax, LEFT).shift(0.5 * RIGHT)

        def read(m: DecimalNumber) -> None:
            m.set_value(round(terms.get_value())).next_to(n_lab, RIGHT, buff=0.1)
            m.set_opacity(show_sum.get_value())

        read(count)
        count.add_updater(read)
        n_lab.add_updater(lambda m: m.set_opacity(show_sum.get_value()))
        self.add(always_redraw(the_sum), n_lab, count)
        self.play(FadeOut(px), FadeOut(p3), f_curve.animate.set_stroke(opacity=0.35),
                  show_sum.animate.set_value(1.0), run_time=1.0)
        self.note("Add the projections back up, one sine at a time.")
        self.play(terms.animate.set_value(11), run_time=4.0, rate_func=linear)
        self.play(terms.animate.set_value(39), run_time=3.0, rate_func=smooth)
        self.wait(2.0)

        # ---- 4 · Gibbs: the overshoot that stays ----
        self.next_section("the overshoot that stays")
        pct = DecimalNumber(100 * overshoot(39), num_decimal_places=1, unit=r"\%", font_size=SMALL, color=WARM)
        pct_lab = words("overshoot at the jump:", size=SMALL, color=WARM)
        VGroup(pct_lab, pct).arrange(RIGHT, buff=0.15).next_to(ax, DOWN, buff=0.5).align_to(ax, LEFT)
        show_gibbs = ValueTracker(0.0)
        pct.add_updater(lambda m: m.set_value(100 * overshoot(round(terms.get_value()))).set_opacity(
            show_gibbs.get_value()))

        def peak() -> DashedLine:
            h = 1 + 2 * overshoot(round(terms.get_value()))
            return DashedLine(ax.c2p(0, h), ax.c2p(1.2, h), color=WARM, stroke_width=2, dash_length=0.06,
                              stroke_opacity=show_gibbs.get_value())

        self.add(always_redraw(peak), pct)
        self.play(FadeIn(pct_lab), show_gibbs.animate.set_value(1.0), run_time=0.8)
        self.note("Near a jump it overshoots by about 9%, however many terms you add.")
        self.play(terms.animate.set_value(99), run_time=4.0, rate_func=smooth)
        self.wait(3.0)

        # ---- 5 · why it works: the sines are orthogonal ----
        self.next_section("orthogonal")
        m = np.array([[inner(np.sin(i * XF), np.sin(j * XF), XF) / np.pi for j in range(1, 6)] for i in range(1, 6)])
        table = Matrix([[f"{abs(v):.2f}" if abs(v) > 5e-3 else "0" for v in row] for row in m],
                       element_to_mobject=lambda s: tex(s, size=SMALL, color=ACCENT if s != "0" else MUTED),
                       h_buff=0.95, v_buff=0.5)
        caption = tex(r"\tfrac{1}{\pi}\langle \sin mx,\sin nx\rangle", size=SMALL).next_to(table, UP, buff=0.25)
        block = backed(VGroup(caption, table), opacity=0.9, buff=0.2).move_to((4.0, 0.3, 0))   # where the bars were
        self.play(FadeOut(bx), FadeOut(bars), show_bars.animate.set_value(0.0), run_time=0.6)
        self.play(FadeIn(block, shift=0.15 * UP), run_time=1.0, rate_func=settle)
        self.note("The sines are orthogonal: each coefficient is its own projection.")
        self.wait(5.0)

"""Gauss's law by counting field lines: the net number leaving a closed surface depends only on the charge inside.

Flux is linear in E, so each charge's lines can be counted on their own (superposition) and the counts added. Sixteen
lines leave every +q. A charge inside sends all 16 out, through any shape of surface. A charge outside sends in as
many as it sends out: net 0. Move it inside and its count jumps to 16.

It's drawn for the card that keeps slipping: with charges outside, E on the surface is the total field from all of
them, but only the net flux is fixed by Q_enc. Young & Freedman's notation, as on his cards.
"""

import numpy as np
from mn_style import *

CLIP = {
    "title": "Gauss's law: counting lines through a closed surface",
    "courses": ["phys207"],
    "topics": ["physics::em::gauss", "physics::em::field-lines", "principle::flux-and-divergence"],
    "watch_for": "Count the green arrows (out) against the red ones (in). The surface changes shape and the outside "
                 "charge's lines go in and out, but the net count only changes when a charge crosses the surface.",
    "cards": [1790363822527, 1790363822525],  # charges outside the surface; Gauss's law (integral form)
    "scene": "GaussFlux",
}

C = np.array([-1.7, -0.35, 0.0])   # the inside charge, and the surface's center
R = 1.55                           # the surface's mean radius
LINES = 16                         # field lines per +q: the unit of flux in this picture
REACH = 10.0                       # a field line runs this far: off the frame from anywhere on it
START = 1.1                        # q2's x to begin with: close enough that several of its lines hit the surface


def radius(theta: np.ndarray, s: float, phase: float) -> np.ndarray:
    """The surface in polar form about C: a circle at s = 0, a lumpy closed curve at s = 1. It stays star-shaped about
    C (radius > 0 everywhere), so every line from the inside charge crosses it exactly once."""
    return R * (1 + s * (0.28 * np.sin(3 * theta + phase) + 0.12 * np.cos(2 * theta - phase)))


def outside(p: np.ndarray, s: float, phase: float) -> np.ndarray:
    """> 0 outside the surface, < 0 inside, for an array of points."""
    d = p - C
    return np.hypot(d[:, 0], d[:, 1]) - radius(np.arctan2(d[:, 1], d[:, 0]), s, phase)


# harvest: where field lines cross a closed curve, signed (Gauss counting), e.g. into amanim.fields
def crossings(q: np.ndarray, s: float, phase: float) -> list[tuple[np.ndarray, np.ndarray, bool]]:
    """Where each of q's field lines crosses the surface: (point, direction, leaving?). Found numerically: sample
    along the line and look for sign changes of `outside`. A line may cross three times (out, in, out): net +1."""
    found: list[tuple[np.ndarray, np.ndarray, bool]] = []
    t = np.linspace(0.05, REACH, 900)
    for k in range(LINES):
        a = 2 * np.pi * k / LINES + 0.13          # a small offset keeps lines off the surface's symmetry axes
        d = np.array([np.cos(a), np.sin(a), 0.0])
        pts = q + t[:, None] * d
        f = outside(pts, s, phase)
        for i in np.nonzero(np.sign(f[:-1]) != np.sign(f[1:]))[0]:
            w = f[i] / (f[i] - f[i + 1])           # linear interpolation between the two samples
            found.append((pts[i] + w * (pts[i + 1] - pts[i]), d, bool(f[i] < 0)))
    return found


def net(q: np.ndarray, s: float, phase: float) -> int:
    return sum(1 if out else -1 for _, _, out in crossings(q, s, phase))


def charge(color: ManimColor) -> VGroup:
    return VGroup(Circle(radius=0.2, color=color, fill_opacity=1, stroke_width=0),
                  tex("+", size=SMALL, color=GROUND))


def lines_and_marks(q: np.ndarray, color: ManimColor, s: float, phase: float) -> VGroup:
    """A charge's field lines, and a small arrow at every crossing: green leaving, red entering."""
    rays = VGroup(*[Line(q + 0.22 * d, q + REACH * d, color=color, stroke_width=2.5, stroke_opacity=0.75)
                    for d in (np.array([np.cos(a), np.sin(a), 0]) for a in
                              2 * np.pi * np.arange(LINES) / LINES + 0.13)])
    marks = VGroup(*[Arrow(p - 0.24 * d, p + 0.26 * d, buff=0, color=GOOD if out else BAD, stroke_width=7,
                           max_tip_length_to_length_ratio=0.5) for p, d, out in crossings(q, s, phase)])
    return VGroup(rays, marks)


class GaussFlux(MnScene):
    def construct(self) -> None:
        shape = ValueTracker(0.0)   # 0: circle, 1: lumpy
        phase = ValueTracker(0.0)   # turns the lumps, so the shape keeps changing
        qx = ValueTracker(START)    # the second charge's x; it starts outside, right of the surface
        q2_at = lambda: np.array([qx.get_value(), C[1] + 0.3, 0.0])

        def surface() -> VMobject:
            s_, ph = shape.get_value(), phase.get_value()
            return ParametricFunction(lambda t: C + radius(np.array(t), s_, ph) * np.array([np.cos(t), np.sin(t), 0]),
                                      t_range=(0, TAU, TAU / 240), color=INK, stroke_width=4,
                                      fill_color=INK, fill_opacity=0.06)

        def lines1() -> VGroup:
            return lines_and_marks(C, ACCENT, shape.get_value(), phase.get_value())

        def lines2() -> VGroup:
            return lines_and_marks(q2_at(), WARN, shape.get_value(), phase.get_value())

        law = backed(tex(r"\oint\vec E\cdot d\vec A", "=", r"\dfrac{Q_{enc}}{\epsilon_0}", size=TITLE), buff=0.2)
        law.to_corner(UL, buff=0.45)

        q1 = charge(ACCENT).move_to(C)
        # q2 keeps the same parts as it moves, so an updater is enough; its lines need redrawing (see bring_in).
        q2 = charge(WARN).add_updater(lambda m: m.move_to(q2_at()))

        # The tally: net lines out, per charge. Each number is recounted from the picture every frame.
        n1 = Integer(0, font_size=BODY, color=ACCENT)
        n2 = Integer(0, font_size=BODY, color=WARN)
        rows = VGroup(
            words("net lines out", size=SMALL, color=MUTED),
            VGroup(words(r"from $q_1$:", color=ACCENT), n1).arrange(RIGHT, buff=0.2),
            VGroup(words(r"from $q_2$:", color=WARN), n2).arrange(RIGHT, buff=0.2),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.25)
        plate = backed(rows, buff=0.25)[0]   # sized for all three rows now, so it doesn't jump when q2 arrives
        VGroup(plate, rows).to_corner(UR, buff=0.45)

        # ---- one charge inside: every line crosses once, outward ----
        self.next_section("inside")
        self.play(FadeIn(law))
        self.bring_in(surface, anim=Create, extra=[FadeIn(q1, scale=0.5)])
        self.bring_in(lines1, run_time=1.2)
        self.bring_to_front(q1)   # charges sit on top of their own lines
        # Count before fading in: a number that goes 0 -> 12 mid-fade changes its glyph count, which breaks the fade.
        n1.add_updater(lambda m: m.set_value(net(C, shape.get_value(), phase.get_value()))).update()
        self.play(FadeIn(plate), FadeIn(rows[0]), FadeIn(rows[1]))
        self.note(rf"{LINES} lines leave $q_1$. Each crosses the closed surface once, outward.")
        self.wait(2)

        # ---- the shape doesn't matter ----
        self.next_section("any shape")
        self.play(shape.animate.set_value(1.0), run_time=2.5)
        self.play(phase.animate.set_value(2.2), run_time=3, rate_func=there_and_back_with_pause)
        self.note(rf"Any closed surface around it: still {LINES} out. The shape doesn't matter.")
        self.wait(1.5)

        # ---- a second charge, outside: everything it sends in comes back out ----
        self.next_section("outside")
        n2.add_updater(lambda m: m.set_value(net(q2_at(), shape.get_value(), phase.get_value()))).update()
        self.play(FadeIn(q2, scale=0.5))
        self.bring_in(lines2, extra=[FadeIn(rows[2])], run_time=1.2)
        self.bring_to_front(q1, q2)
        self.note(r"$q_2$ is outside. Each of its lines that enters (red) leaves again (green): net 0.")
        self.wait(2.5)

        # ---- bring it inside: the count jumps only as it crosses ----
        self.next_section("crossing in")
        self.play(qx.animate.set_value(C[0] + 0.75), run_time=4, rate_func=smooth)
        self.note(rf"Inside, all {LINES} of $q_2$'s lines leave. The count changed only as it crossed.")
        self.wait(2)

        # ---- the card this is for ----
        self.next_section("only Q_enc")
        self.play(qx.animate.set_value(START), run_time=3)
        box = SurroundingRectangle(law[1][2], color=GOOD, buff=0.08)
        self.play(Create(box))
        self.note(r"$\vec E$ on the surface comes from both charges. Only the net flux is fixed by $Q_{enc}$.")
        self.wait(3)

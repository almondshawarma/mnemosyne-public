"""A 2×2 matrix moves the whole plane: its columns are where e1 and e2 land, and |det| is how areas scale.

Three matrices, one idea each: an ordinary one (area ×3), one with a negative determinant (same area, flipped), and a
singular one (dependent columns: the plane collapses onto a line). Leon's notation: e1, e2, det A.
"""

from fractions import Fraction

import numpy as np
from mn_style import *

CLIP = {
    "title": "A 2×2 matrix moves the plane",
    "courses": ["math309"],
    "topics": ["math::linear-algebra::matrices", "math::linear-algebra::determinants"],
    "watch_for": "Where e1 and e2 land (the columns), and what happens to the unit square: area ×|det A|, a flip when "
                 "det A < 0, and a collapse when the columns are dependent.",
    "cards": [1790364340214, 1790363865777],  # det = signed area; n vectors dependent iff det X = 0
    "scene": "LinearMap",
}

# The three examples. Every number on screen is computed from these, never typed.
A = np.array([[2, -1], [1, 1]])
F = np.array([[1, 2], [2, 1]])
S = np.array([[2, 1], [1, 0.5]])   # second column is half the first

O = np.array([1.6, -1.3, 0.0])     # the plane's origin, right of center: the left third holds the algebra
U = 1.25                           # one plane unit, in frame units: bigger than 1 so the unit square reads on a phone


def num(v: float) -> str:
    f = Fraction(float(v)).limit_denominator(8)
    if f.denominator == 1:
        return str(f.numerator)
    return ("-" if f < 0 else "") + rf"\tfrac{{{abs(f.numerator)}}}{{{f.denominator}}}"


def matrix(m: np.ndarray, name: str) -> VGroup:
    """'A =' and the matrix, with column 1 green and column 2 amber, matching the arrows they describe."""
    body = Matrix([[num(v) for v in row] for row in m], left_bracket="(", right_bracket=")",
                  element_to_mobject=lambda s: tex(s), h_buff=1.0)
    cols = body.get_columns()
    cols[0].set_color(GOOD)
    cols[1].set_color(WARN)
    return VGroup(tex(name, "="), body).arrange(RIGHT, buff=0.2)


def column(v: np.ndarray, color: ManimColor) -> MathTex:
    return tex(rf"\begin{{pmatrix}}{num(v[0])}\\{num(v[1])}\end{{pmatrix}}", size=SMALL, color=color)


def det(m: np.ndarray) -> float:
    return float(round(np.linalg.det(m), 6))


class LinearMap(MnScene):
    def construct(self) -> None:
        # The grid that moves, over a faint copy that doesn't: the fixed one is what "moved" is measured against.
        grid = {"x_range": (-8, 8, 1), "y_range": (-6, 6, 1), "x_length": 16 * U, "y_length": 12 * U}
        still = NumberPlane(**grid, background_line_style={"stroke_color": GRID, "stroke_width": 1.5},
                            axis_config={"stroke_color": GRID}).shift(O)
        plane = NumberPlane(**grid, background_line_style={"stroke_color": ACCENT, "stroke_width": 1.5,
                                                           "stroke_opacity": 0.45},
                            axis_config={"stroke_color": MUTED}).shift(O)
        square = Polygon(O, O + U * RIGHT, O + U * (RIGHT + UP), O + U * UP,
                         stroke_width=0, fill_color=ACCENT, fill_opacity=0.35)
        # Invisible dots ride the transformation; the arrows are redrawn from them every frame. Transforming an
        # Arrow directly would shear its tip along with the plane.
        t1, t2 = Dot(O + U * RIGHT).set_opacity(0), Dot(O + U * UP).set_opacity(0)
        world = VGroup(plane, square, t1, t2)
        e1 = always_redraw(lambda: Arrow(O, t1.get_center(), buff=0, color=GOOD, stroke_width=7,
                                         max_tip_length_to_length_ratio=0.2))
        e2 = always_redraw(lambda: Arrow(O, t2.get_center(), buff=0, color=WARN, stroke_width=7,
                                         max_tip_length_to_length_ratio=0.2))

        self.next_section("basis")
        self.add(still)
        self.play(Create(plane, lag_ratio=0.05), run_time=1.5)
        self.play(FadeIn(square), GrowArrow(e1), GrowArrow(e2))
        self.add(e1, e2)
        l1 = backed(tex(r"\vec e_1", color=GOOD)).next_to(t1, DOWN, buff=0.15)
        l2 = backed(tex(r"\vec e_2", color=WARN)).next_to(t2, LEFT, buff=0.15)
        self.play(FadeIn(l1), FadeIn(l2))
        self.note(r"Follow $\vec e_1$ and $\vec e_2$: the rest of the plane goes where they go.")
        self.wait(1.5)

        # ---- an ordinary matrix: columns, then area ----
        self.next_section("columns")
        panel = backed(matrix(A, "A"), buff=0.2).to_corner(UL, buff=0.5)
        self.play(FadeIn(panel), FadeOut(l1), FadeOut(l2))
        world.save_state()
        self.play(world.animate.apply_matrix(A, about_point=O), run_time=2.5)
        # Label each tip with the column itself, so the eye can match it against the matrix in the corner.
        c1 = backed(column(A[:, 0], GOOD)).next_to(t1, RIGHT, buff=0.15)
        c2 = backed(column(A[:, 1], WARN)).next_to(t2, LEFT, buff=0.15)
        self.play(FadeIn(c1), FadeIn(c2))
        self.note(r"The columns of $A$ are where $\vec e_1$ and $\vec e_2$ land.")
        self.wait(2)

        self.next_section("area")
        area = backed(tex(r"\text{area}=|\det A|=", num(abs(det(A))))).next_to(panel, DOWN, aligned_edge=LEFT,
                                                                                buff=0.4)
        self.play(FadeOut(c1), FadeOut(c2), FadeIn(area), Indicate(square, color=ACCENT, scale_factor=1.05))
        self.note(r"Every area, the unit square's included, scales by $|\det A|$.")
        self.wait(2.5)

        # ---- a negative determinant: same kind of move, but the plane turns over ----
        self.next_section("flip")
        self.play(Restore(world), FadeOut(panel), FadeOut(area), run_time=1.5)
        # The turn from e1 to e2, redrawn every frame from the arrows: counterclockwise now, clockwise after F.
        turn = always_redraw(lambda: self.turn_arrow(t1.get_center(), t2.get_center()))
        self.play(Create(turn))
        panel = backed(matrix(F, "F"), buff=0.2).to_corner(UL, buff=0.5)
        self.play(FadeIn(panel))
        self.play(world.animate.apply_matrix(F, about_point=O), run_time=2.5)
        sign = backed(tex(r"\det F=", num(det(F)))).next_to(panel, DOWN, aligned_edge=LEFT, buff=0.4)
        self.play(FadeIn(sign))
        self.note(r"$\det F<0$: the plane turned over. $\vec e_1\to\vec e_2$ now runs clockwise.")
        self.wait(2.5)

        # ---- a singular matrix: dependent columns, nothing left of the plane but a line ----
        self.next_section("collapse")
        self.play(Restore(world), FadeOut(turn), FadeOut(panel), FadeOut(sign), run_time=1.5)
        panel = backed(matrix(S, "S"), buff=0.2).to_corner(UL, buff=0.5)
        self.play(FadeIn(panel))
        self.play(world.animate.apply_matrix(S, about_point=O), run_time=2.5)
        zero = backed(tex(r"\det S=", num(det(S)))).next_to(panel, DOWN, aligned_edge=LEFT, buff=0.4)
        self.play(FadeIn(zero))
        self.note(r"Dependent columns: $\det S=0$, and the plane collapses onto a line.")
        self.wait(3)

    @staticmethod
    def turn_arrow(p1: np.ndarray, p2: np.ndarray) -> VMobject:
        """A small arc near the origin from e1's direction to e2's, the short way round: its direction is the sign of
        the determinant."""
        a1 = np.arctan2(*(p1 - O)[1::-1])
        a2 = np.arctan2(*(p2 - O)[1::-1])
        sweep = (a2 - a1 + np.pi) % (2 * np.pi) - np.pi
        if abs(sweep) < 0.05:
            return VMobject()
        arc = Arc(radius=0.85, start_angle=a1, angle=sweep, arc_center=O, color=INK, stroke_width=5)
        arc.add_tip(tip_length=0.24, tip_width=0.24)
        return arc

"""A two-mesh circuit solved as a linear system: Kirchhoff's loop rule around each mesh gives A i = b, row reduction
gives the currents, and a negative answer turns an arrow around.

PHYS 207 meets MATH 309: the loop rule (his cards on mesh analysis and the sign of b_i) produces the system, and the
rref of the augmented matrix [A | b] holds the unknowns in its last column (his card on reading it). Every number is
computed from the component values below, and the row operations are found by the elimination, not typed.
"""

from fractions import Fraction

import numpy as np
from amanim import cell, resistor
from mn_style import *

CLIP = {
    "title": "A circuit as a linear system: mesh currents by row reduction",
    "courses": ["phys207", "math309"],
    "topics": ["physics::em::circuits", "math::linear-algebra::row-reduction", "math::linear-algebra::systems",
               "principle::linearity"],
    "watch_for": "How each loop walk becomes one row of [A | b] (the sign of each emf), and how a negative mesh current "
                 "means the arrow you guessed was backwards.",
    "cards": [1791778983768, 1791779003188, 1791575303539],  # b_i's sign; reading the rref; how many equations
    "scene": "MeshMatrix",
}

R1, R2, R3 = 2, 4, 2           # ohms: left top, right top, the shared middle branch
E1, E2 = 10, 10                # volts: left battery (+ up), right battery (+ up)

# The loop rule, walking each mesh clockwise. Mesh 1 crosses E1 from - to + (+E1); mesh 2 crosses E2 from + to - (-E2).
A = [[Fraction(R1 + R3), Fraction(-R3)], [Fraction(-R3), Fraction(R2 + R3)]]
b = [Fraction(E1), Fraction(-E2)]

# Circuit corners, in frame units: the left half of the frame. The right half holds the algebra.
TL, TM, TR = np.array([-5.5, 2.1, 0]), np.array([-3.0, 2.1, 0]), np.array([-0.5, 2.1, 0])
BL, BM, BR = np.array([-5.5, -2.3, 0]), np.array([-3.0, -2.3, 0]), np.array([-0.5, -2.3, 0])


def frac(x: Fraction) -> str:
    if x.denominator == 1:
        return str(x.numerator)
    return ("-" if x < 0 else "") + rf"\tfrac{{{abs(x.numerator)}}}{{{x.denominator}}}"


def lin(row: list[Fraction]) -> str:
    """'4I_1 - 2I_2' from the coefficients."""
    out = ""
    for i, c in enumerate(row):
        if c == 0:
            continue
        mag = "" if abs(c) == 1 else frac(abs(c))
        sign = ("-" if c < 0 else "") if not out else (" - " if c < 0 else " + ")
        out += f"{sign}{mag}I_{i + 1}"
    return out


def gauss_jordan(m: list[list[Fraction]]) -> list[tuple[str, int, list[list[Fraction]]]]:
    """Row reduction of an augmented matrix to rref, step by step: (the operation in LaTeX, the row it changed, the
    matrix after it). Forward elimination first, then scaling and clearing upward: the order Leon teaches."""
    m = [row[:] for row in m]
    steps: list[tuple[str, int, list[list[Fraction]]]] = []
    n = len(m)
    for j in range(n):
        for i in range(j + 1, n):
            k = -m[i][j] / m[j][j]
            if k:
                m[i] = [a + k * p for a, p in zip(m[i], m[j], strict=True)]
                steps.append((rf"R_{i + 1}\to R_{i + 1}{'+' if k > 0 else '-'}{frac(abs(k)) if abs(k) != 1 else ''}R_{j + 1}",
                              i, [r[:] for r in m]))
    for j in reversed(range(n)):
        if m[j][j] != 1:
            k = 1 / m[j][j]
            m[j] = [k * a for a in m[j]]
            steps.append((rf"R_{j + 1}\to {frac(k)}R_{j + 1}", j, [r[:] for r in m]))
        for i in range(j):
            k = -m[i][j]
            if k:
                m[i] = [a + k * p for a, p in zip(m[i], m[j], strict=True)]
                steps.append((rf"R_{i + 1}\to R_{i + 1}{'+' if k > 0 else '-'}{frac(abs(k)) if abs(k) != 1 else ''}R_{j + 1}",
                              i, [r[:] for r in m]))
    return steps


# ---------- circuit parts ----------

def mesh_arrow(center: np.ndarray, label: str, clockwise: bool = True, color: ManimColor = ACCENT) -> VGroup:
    sweep = -1.55 * PI if clockwise else 1.55 * PI
    start = 0.75 * PI if clockwise else 0.25 * PI
    arc = Arc(radius=0.6, start_angle=start, angle=sweep, arc_center=center, color=color, stroke_width=5)
    arc.add_tip(tip_length=0.22, tip_width=0.22)
    return VGroup(arc, tex(label, size=BODY, color=color).move_to(center))


def augmented(m: list[list[Fraction]], hot: int | None = None) -> VGroup:
    """[A | b] as a Matrix with a bar before the last column; row `hot` in the accent color."""
    mat = Matrix([[frac(x) for x in row] for row in m], left_bracket="[", right_bracket="]",
                 element_to_mobject=lambda s: tex(s), h_buff=1.15, v_buff=0.9)
    cols = mat.get_columns()
    x = (cols[-2].get_right()[0] + cols[-1].get_left()[0]) / 2
    bar = Line([x, mat.get_top()[1] - 0.15, 0], [x, mat.get_bottom()[1] + 0.15, 0], color=MUTED, stroke_width=3)
    if hot is not None:
        mat.get_rows()[hot].set_color(ACCENT)
    return VGroup(mat, bar)


class MeshMatrix(MnScene):
    def construct(self) -> None:
        # ---- the circuit ----
        # The symbols are amanim.circuits (harvested from this scene): drawn between node coordinates.
        r1, r2, r3 = (resistor(a, b, color=INK) for a, b in ((TL, TM), (TM, TR), (TM, BM)))
        e1 = cell(BL, TL, plus_toward_b=True, color=INK, plus_color=MUTED)
        e2 = cell(TR, BR, plus_toward_b=False, color=INK, plus_color=MUTED)
        bottom = Line(BL, BR, color=INK, stroke_width=4)
        circuit = VGroup(r1, r2, r3, e1, e2, bottom)
        ohm = lambda v: tex(rf"{v}\,\Omega", size=SMALL, color=MUTED)
        volt = lambda v: tex(rf"{v}\,\mathrm{{V}}", size=SMALL, color=MUTED)
        # The middle resistor's label sits high, beside its top teeth: the mesh arrows sit low, so the two never meet.
        labels = VGroup(ohm(R1).next_to(r1, UP, buff=0.15), ohm(R2).next_to(r2, UP, buff=0.15),
                        ohm(R3).next_to(r3.get_center() + 0.35 * UP, RIGHT, buff=0.3),
                        volt(E1).next_to(e1, LEFT, buff=0.12), volt(E2).next_to(e2, RIGHT, buff=0.12))
        c1, c2 = (TL + BM) / 2 + 0.5 * DOWN, (TM + BR) / 2 + 0.5 * DOWN
        i1, i2 = mesh_arrow(c1, "I_1"), mesh_arrow(c2, "I_2")
        self.keep_clear(circuit, i1, i2)

        self.next_section("circuit")
        self.play(Create(circuit, lag_ratio=0.15), run_time=2.5)
        self.play(FadeIn(labels))
        self.play(Create(i1), Create(i2))
        self.note("Two unknowns: a current around each mesh, both guessed clockwise.")
        self.wait(1.5)

        # ---- walk mesh 1: each element adds a term ----
        self.next_section("mesh 1")
        eq1_walk = tex(f"{E1}", f"-{R1}I_1", f"-{R3}(I_1-I_2)", "=0", size=BODY)
        eq1 = tex(lin(A[0]), "=", frac(b[0]))
        eq2_walk = tex(f"-{R2}I_2", f"-{E2}", f"-{R3}(I_2-I_1)", "=0", size=BODY)
        eq2 = tex(lin(A[1]), "=", frac(b[1]))
        top = np.array([3.5, 2.75, 0])
        eq1_walk.move_to(top)
        self.walk([BL, TL, TM, BM, BL], [e1, r1, r3, None], eq1_walk)
        eq1.move_to(top)
        self.play(TransformMatchingShapes(eq1_walk, eq1))
        self.note(r"Walking up through the battery crosses $-\to+$: $+10$ on the right side.")
        self.wait(1.5)

        self.next_section("mesh 2")
        eq2_walk.next_to(eq1, DOWN, buff=0.35)
        self.walk([TM, TR, BR, BM, TM], [r2, e2, None, r3], eq2_walk)
        eq2.next_to(eq1, DOWN, buff=0.35)
        eq2.shift((eq1[1].get_center()[0] - eq2[1].get_center()[0]) * RIGHT)  # line up the = signs
        self.play(TransformMatchingShapes(eq2_walk, eq2))
        self.note(r"Walking down through the right battery crosses $+\to-$: $b_2=-10$.")
        self.wait(1.5)

        # ---- the system as an augmented matrix ----
        self.next_section("[A | b]")
        rows = [A[0] + [b[0]], A[1] + [b[1]]]
        mat = augmented(rows).move_to([3.4, 0.1, 0])
        self.play(TransformFromCopy(VGroup(eq1, eq2), mat), run_time=1.5)
        self.note(r"Coefficients on the left of the bar, the emfs ($\vec b$) on the right.")
        self.wait(1.5)

        # ---- row reduce, one operation per section so each step can be checked ----
        steps = gauss_jordan(rows)
        op: Mobject | None = None
        for k, (label, row, after) in enumerate(steps):
            self.next_section(f"step {k + 1}")
            new = augmented(after, hot=row).move_to(mat)
            new_op = backed(tex(label, size=SMALL + 4, color=ACCENT)).next_to(mat, DOWN, buff=0.4)
            self.play(FadeTransform(op, new_op) if op else FadeIn(new_op))
            self.play(ReplacementTransform(mat, new), run_time=1.2)
            mat, op = new, new_op
            self.wait(0.8)
        self.play(mat[0].animate.set_color(INK), FadeOut(op))

        # ---- read off the answer: the last column ----
        self.next_section("read off")
        last = mat[0].get_columns()[-1]
        box = SurroundingRectangle(last, color=GOOD, buff=0.12)
        I1, I2 = steps[-1][2][0][-1], steps[-1][2][1][-1]
        answer = tex(rf"I_1={frac(I1)}\,\mathrm{{A}}", r",\quad", rf"I_2={frac(I2)}\,\mathrm{{A}}", color=GOOD)
        answer.next_to(mat, DOWN, buff=0.5)
        self.play(Create(box))
        self.play(FadeIn(answer, shift=0.2 * DOWN))
        self.note("In rref, the last column holds the unknowns, top to bottom.")
        self.wait(2)

        # ---- back to the circuit: a negative current turns its arrow around ----
        self.next_section("back to the circuit")
        flipped = mesh_arrow(c2, "I_2", clockwise=I2 > 0, color=WARN if I2 < 0 else ACCENT)
        self.play(Indicate(answer[2], color=WARN))
        self.play(ReplacementTransform(i2, flipped))
        mag = tex(rf"{frac(abs(I2))}\,\mathrm{{A}}", size=SMALL, color=WARN).next_to(flipped, DOWN, buff=0.1)
        middle = I1 - I2
        # Low on the shared branch, under the mesh arrows, so its label has room.
        down = Arrow(BM + 0.85 * UP + 0.35 * LEFT, BM + 0.2 * UP + 0.35 * LEFT, buff=0, color=GOOD, stroke_width=5)
        shared = tex(rf"{frac(middle)}\,\mathrm{{A}}", size=SMALL, color=GOOD).next_to(down, LEFT, buff=0.1)
        self.play(FadeIn(mag), GrowArrow(down), FadeIn(shared))
        self.note(rf"$I_2<0$: mesh 2 runs counterclockwise. The shared branch carries $I_1-I_2={frac(middle)}$ A.")
        self.wait(3)

    def walk(self, corners: list[np.ndarray], legs: list[Mobject | None], equation: MathTex) -> None:
        """A dot walks the mesh clockwise, leg by leg; each element it passes lights up and writes its term (the
        equation's parts are in walking order, '=0' last)."""
        dot = Dot(corners[0], radius=0.11, color=WARN)
        self.play(FadeIn(dot, scale=0.5), run_time=0.4)
        term = 0
        for b, hit in zip(corners[1:], legs, strict=True):
            self.play(dot.animate.move_to(b), run_time=0.9, rate_func=linear)
            if hit is not None and term < len(equation) - 1:
                self.play(Indicate(hit, color=WARN, scale_factor=1.0), Write(equation[term]), run_time=0.8)
                term += 1
        self.play(Write(equation[term:]), FadeOut(dot), run_time=0.6)

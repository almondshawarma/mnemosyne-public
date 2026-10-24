"""Thomson's e/m, derived: the electric field alone bends the beam by θ, crossed fields find the speed, and the
two together give e/m from things you can measure.

For NUEN 201 L10 (2026-10-13), slides 5-6, where Aman wrote "derivation?" beside e/m_e = Vθ/(B²ℓd) and
"E = −v×B, goes straight?". The notation is the slide's: e, m_e, V, d, ℓ, v_x, θ, B.

Every path is integrated from F = q(E + v×B) by amanim.lorentz_path, in scene units (only the shape is physics; the
units are the screen's). The final number is CODATA's, from scipy.constants, never typed in.
"""

import numpy as np
from amanim import lorentz_path
from mn_style import *
from scipy import constants

CLIP = {
    "title": "Thomson's e/m: deflect with E, select with B, divide",
    "courses": ["nuen201"],
    "topics": ["physics::quantum::atomic-structure", "physics::em::motion-in-field"],
    "watch_for": "Why θ alone can't give e/m (it hides v_x), how crossed fields pick out one speed, and how "
                 "v_x = E/B turns θ into e/m_e = Vθ/(B²ℓd).",
    "scene": "ThomsonEM",
}

# ---- the apparatus, in scene units: the beam runs along y = Y0 ----
Y0 = 0.35                     # the beam's height on screen (the algebra gets the right third)
X_IN, ELL = -5.1, 3.6         # plates start at X_IN and are ℓ long
D = 2.2                       # plate gap d
X_SCREEN = 1.5                # the phosphor screen
V0 = 3.0                      # beam speed v_x in the E-only shot
E0 = 0.8                      # field between the plates, pointing down (top plate +)
B0 = E0 / V0                  # the crossed B that balances it at v_x = V0: this is the selector condition
Q_M = -1.0                    # an electron: negative charge (only the sign and q/m matter for the shape)


def inside(r: np.ndarray) -> bool:
    return X_IN <= r[0] <= X_IN + ELL and abs(r[1] - Y0) < D / 2


def e_field(r: np.ndarray) -> np.ndarray:
    return np.array([0.0, -E0, 0.0]) if inside(r) else np.zeros(3)   # + plate on top: E points down


def b_field(r: np.ndarray) -> np.ndarray:
    return np.array([0.0, 0.0, -B0]) if inside(r) else np.zeros(3)   # into the page (⊗), as on the slide


def beam(speed: float, with_b: bool) -> np.ndarray:
    """The electron's real path from the cathode side to the screen."""
    rs, _, _ = lorentz_path(Q_M, E=e_field, B=b_field if with_b else None, r0=(X_IN - 0.9, Y0, 0),
                            v0=(speed, 0, 0), dt=0.004, steps=6000, stop=lambda r, v, t: r[0] >= X_SCREEN)
    return rs


def path_mob(points: np.ndarray, color: ManimColor, width: float = 5) -> VMobject:
    return VMobject(stroke_color=color, stroke_width=width).set_points_as_corners(points)


class ThomsonEM(MnScene):
    def construct(self) -> None:
        # ---- the tube: plates (structure color), the screen, and the labels the slide uses ----
        top = Rectangle(width=ELL, height=0.14, fill_color=STRUCT, fill_opacity=1, stroke_color=MUTED,
                        stroke_width=2).move_to([X_IN + ELL / 2, Y0 + D / 2 + 0.07, 0])
        bottom = top.copy().move_to([X_IN + ELL / 2, Y0 - D / 2 - 0.07, 0])
        plus = tex("+", size=SMALL, color=INK).next_to(top, UP, buff=0.1)
        minus = tex("-", size=SMALL, color=INK).next_to(bottom, DOWN, buff=0.1)
        screen = Line([X_SCREEN, Y0 - 2.6, 0], [X_SCREEN, Y0 + 2.6, 0], color=MUTED, stroke_width=6)
        gun = VGroup(Circle(radius=0.16, color=MUTED, stroke_width=3).move_to([X_IN - 1.05, Y0, 0]),
                     tex("C", size=SMALL, color=MUTED).move_to([X_IN - 1.05, Y0 + 0.45, 0]))
        ell = VGroup(DoubleArrow([X_IN, Y0 - D / 2 - 0.55, 0], [X_IN + ELL, Y0 - D / 2 - 0.55, 0], buff=0,
                                 color=MUTED, stroke_width=2, tip_length=0.15),
                     tex(r"\ell", size=SMALL, color=MUTED).move_to([X_IN + ELL / 2, Y0 - D / 2 - 0.85, 0]))
        gap = VGroup(DoubleArrow([X_IN - 0.35, Y0 - D / 2, 0], [X_IN - 0.35, Y0 + D / 2, 0], buff=0,
                                 color=MUTED, stroke_width=2, tip_length=0.15),
                     tex("d", size=SMALL, color=MUTED).move_to([X_IN - 0.6, Y0 + 0.35, 0]))
        straight = DashedLine([X_IN - 0.9, Y0, 0], [X_SCREEN, Y0, 0], color=MUTED, stroke_width=2, dash_length=0.12)
        apparatus = VGroup(top, bottom, plus, minus, screen, gun, ell, gap)
        self.keep_clear(top, bottom, screen)

        self.next_section("the tube")
        self.play(FadeIn(apparatus, lag_ratio=0.1), run_time=1.5)
        self.play(Create(straight))
        self.note(r"Thomson, 1897: a beam of cathode rays between two plates, then a phosphor screen.")
        self.wait(1.5)

        # ---- E alone: the field points down (+ plate on top), the negative beam bends UP ----
        self.next_section("E alone")
        e_arrows = VGroup(*[Arrow([x, Y0 + D / 2 - 0.1, 0], [x, Y0 - D / 2 + 0.1, 0], buff=0, color=FLOW,
                                  stroke_width=2, max_tip_length_to_length_ratio=0.12).set_opacity(0.45)
                            for x in np.linspace(X_IN + 0.3, X_IN + ELL - 0.3, 6)])
        e_label = tex(r"\vec E", size=SMALL, color=FLOW).next_to(top, UP, buff=0.12).shift(1.0 * RIGHT)
        pts = beam(V0, with_b=False)
        path = path_mob(pts, ACCENT)
        dot = Dot(pts[0], radius=0.08, color=ACCENT)
        self.play(FadeIn(e_arrows), FadeIn(e_label))
        self.play(Create(path), MoveAlongPath(dot, path), run_time=2.5, rate_func=linear)

        # θ at the plates' exit, from the computed velocity there
        k = int(np.argmin(np.abs(pts[:, 0] - (X_IN + ELL))))
        exit_pt, slope = pts[k], (pts[k + 1, 1] - pts[k, 1]) / (pts[k + 1, 0] - pts[k, 0])
        # the velocity triangle at the exit, to scale: v_y / v_x is the computed slope
        span = 1.8
        vx = Arrow(exit_pt, exit_pt + span * RIGHT, buff=0, color=INK, stroke_width=4, max_tip_length_to_length_ratio=0.15)
        vy = Arrow(exit_pt + span * RIGHT, exit_pt + span * (RIGHT + slope * UP), buff=0, color=INK,
                   stroke_width=4, max_tip_length_to_length_ratio=0.35)
        vy_lbl = tex("v_y", size=SMALL, color=INK).next_to(vy, RIGHT, buff=0.08)
        theta = Angle(Line(exit_pt, exit_pt + RIGHT), Line(exit_pt, exit_pt + RIGHT + slope * UP), radius=0.8,
                      color=WARM, stroke_width=4)
        theta_lbl = tex(r"\theta", size=SMALL, color=WARM).next_to(theta, RIGHT, buff=0.08).shift(0.05 * UP)
        vx_lbl = tex("v_x", size=SMALL, color=INK).next_to(vx, DOWN, buff=0.08)
        self.play(GrowArrow(vx), FadeIn(vx_lbl))
        self.play(GrowArrow(vy), FadeIn(vy_lbl), Create(theta), FadeIn(theta_lbl))
        self.note(r"Only $\vec E$: the beam bends toward the $+$ plate, so the rays carry negative charge.")

        # the algebra, in the slide's notation, one step at a time (right third of the frame)
        steps = [
            tex(r"a_y = \frac{eE}{m_e} = \frac{eV}{m_e d}", size=SMALL + 2),
            tex(r"t = \frac{\ell}{v_x}", size=SMALL + 2),
            tex(r"v_y = a_y t = \frac{eV\ell}{m_e d\, v_x}", size=SMALL + 2),
            tex(r"\theta \approx \frac{v_y}{v_x} = \frac{V\ell}{", r"v_x^2", r"d}\left(\frac{e}{m_e}\right)",
                size=SMALL + 2),
        ]
        arrange_rows(steps, buff=0.32).to_edge(RIGHT, buff=0.45).shift(1.2 * UP)   # placed now, written one by one
        for s in steps:
            self.play(Write(s), run_time=1.0)
            self.wait(0.5)
        self.wait(1)

        # ---- the catch: θ hides v_x ----
        self.next_section("the unknown")
        catch = SurroundingRectangle(steps[3][1], color=BAD, buff=0.06)
        self.play(Create(catch), Indicate(steps[3][1], color=BAD))
        self.note(r"But $\theta$ also depends on $v_x$, and nothing in the tube measures $v_x$ directly.")
        self.wait(2)

        # ---- add B: crossed fields select one speed ----
        self.next_section("add B")
        crosses = VGroup(*[tex(r"\otimes", size=SMALL - 4, color=MUTED).move_to([x, y, 0])
                           for x in np.linspace(X_IN + 0.35, X_IN + ELL - 0.35, 5)
                           for y in (Y0 + 0.45, Y0 - 0.45)])
        b_label = tex(r"\vec B", size=SMALL, color=MUTED).next_to(bottom, RIGHT, buff=0.2)   # clear of the ell line
        self.play(FadeOut(path), FadeOut(dot), FadeOut(vx), FadeOut(vx_lbl), FadeOut(vy), FadeOut(vy_lbl),
                  FadeOut(theta), FadeOut(theta_lbl),
                  FadeOut(VGroup(*steps[:3])), FadeOut(catch),
                  steps[3].animate.to_edge(RIGHT, buff=0.45).to_edge(UP, buff=0.9))
        self.play(FadeIn(crosses, lag_ratio=0.05), FadeIn(b_label))

        speeds = [(0.6 * V0, r"v_x < E/B", WARM), (V0, r"v_x = E/B", GOOD), (1.7 * V0, r"v_x > E/B", FLOW)]
        paths = [path_mob(beam(v, with_b=True), c, 4) for v, _, c in speeds]
        tags = VGroup(*[tex(t, size=SMALL - 2, color=c).next_to(p.get_end(), RIGHT, buff=0.15)
                        for p, (_, t, c) in zip(paths, speeds, strict=True)])
        self.play(*[Create(p) for p in paths], run_time=2.5, rate_func=linear)
        self.play(FadeIn(tags))
        self.note(r"$\vec B$ pushes back, harder for faster electrons: one speed goes straight.")
        self.wait(2)

        # the balance on the electron that goes straight: eE up, e v_x B down (Aman's "E = -v x B")
        self.next_section("the balance")
        probe = Dot([X_IN + ELL / 2, Y0, 0], radius=0.09, color=GOOD)
        f_e = Arrow(probe.get_center(), probe.get_center() + 0.75 * UP, buff=0.05, color=FLOW, stroke_width=5)
        f_b = Arrow(probe.get_center(), probe.get_center() + 0.75 * DOWN, buff=0.05, color=WARM, stroke_width=5)
        fe_lbl = backed(tex(r"eE", size=SMALL, color=FLOW)).next_to(f_e, LEFT, buff=0.08)
        fb_lbl = backed(tex(r"ev_xB", size=SMALL, color=WARM)).next_to(f_b, LEFT, buff=0.08)
        balance = tex(r"eE = e v_x B", r"\;\Rightarrow\;", r"v_x = \frac{E}{B} = \frac{V}{Bd}", size=SMALL + 2)
        balance.next_to(steps[3], DOWN, buff=0.6).to_edge(RIGHT, buff=0.45)
        # the field glyphs step aside so the two forces can be read
        self.play(FadeOut(tags), FadeOut(crosses), FadeOut(e_arrows),
                  *[p.animate.set_stroke(opacity=0.35) for p in (paths[0], paths[2])])
        self.play(FadeIn(probe), GrowArrow(f_e), GrowArrow(f_b), FadeIn(fe_lbl), FadeIn(fb_lbl))
        self.play(Write(balance))
        self.note(r"Straight means the forces cancel: $\vec E = -\vec v\times\vec B$. That fixes $v_x = E/B$.")
        self.wait(2)

        # ---- substitute: θ and v_x together give e/m from measured things ----
        self.next_section("e over m")
        result = tex(r"\frac{e}{m_e} = \frac{V\theta}{B^2 \ell d}", size=TITLE)
        result.next_to(balance, DOWN, buff=0.7).to_edge(RIGHT, buff=0.9)
        box = SurroundingRectangle(result, color=GOOD, buff=0.15)
        self.play(TransformFromCopy(VGroup(steps[3], balance[2]), result), run_time=1.5)
        self.play(Create(box))
        self.note(r"Put $v_x = V/(Bd)$ into $\theta$: everything on the right is measured.")
        self.wait(2)

        # the value, from CODATA (scipy.constants), not typed
        self.next_section("the number")
        ratio = constants.e / constants.m_e
        mant, exp = f"{ratio:.3e}".split("e")
        value = tex(rf"\approx {mant}\times 10^{{{int(exp)}}}\ \mathrm{{C/kg}}", size=SMALL + 4, color=GOOD)
        value.next_to(box, DOWN, buff=0.35)
        self.play(FadeIn(value, shift=0.2 * UP))
        times = constants.m_p / constants.m_e    # e/m_e over the hydrogen ion's e/m_p, from CODATA
        self.note(rf"{times:.0f} times the hydrogen ion's $q/m$: far lighter than any atom.")
        self.wait(3)

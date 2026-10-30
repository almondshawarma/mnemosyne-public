"""The four subspaces of a triangle circuit's incidence matrix, drawn in the two ℝ³'s they live in.

A companion to Strang L12 (the strang-l12 clip). A is 3×3 here (3 edges, 3 nodes), so both spaces are ℝ³ and every
subspace is a line or a plane: node space holds N(A) ⟂ R(Aᵀ), edge space holds R(A) ⟂ N(Aᵀ). Each picture is shown
from a view computed from its plane's normal (amanim.threed), and the right angle from the one view where it is
drawn as 90°. Then the 2D part: Strang's chain AᵀCA x = f, and the dimension count that is Euler's formula.

Ported from the claude.ai chat's two-part clip (the-four-subspaces-in-3d-*): same content, one clip, on mn_style's
MnScene3D and amanim's views and shapes. Every number on screen is computed from A, x, and y below.
"""

from amanim import (
    edge_on_view,
    fader,
    flow_dots,
    line_through,
    nudge,
    oblique_view,
    plane_patch,
    right_angle_3d,
    split_parallelogram,
    vec3,
)
from mn_style import *

CLIP = {
    "title": "The four subspaces in 3D: a triangle circuit",
    "courses": ["math309", "phys207"],
    "topics": ["math::linear-algebra::four-subspaces", "math::linear-algebra::orthogonality",
               "physics::em::circuits"],
    "watch_for": "N(A) is the line (1,1,1), the voltage you ground; the rows' plane R(Aᵀ) is perpendicular to it. In "
                 "edge space, R(A) is the KVL plane and N(Aᵀ) the loop current, and their right angle is Tellegen. The "
                 "right angles only look right in the edge-on views.",
    "scene": "FourSubspaces3D",
}

# ---- the inputs: change them and every label follows ----
# Triangle graph: e1 = 1->2, e2 = 2->3, e3 = 1->3. Row = edge: -1 where it leaves, +1 where it enters.
A = np.array([[-1, 1, 0], [0, -1, 1], [-1, 0, 1]], dtype=float)
X = np.array([2.0, 0.0, 1.0])            # node potentials followed through node space
Y = np.array([2.0, 1.0, 0.0])            # an edge current to split in edge space

m, n = A.shape
r = int(np.linalg.matrix_rank(A))


def null_basis(M: np.ndarray) -> np.ndarray:
    """The first basis vector of N(M), from the SVD, scaled so its largest entry is +1: (1, 1, 1), not (0.58, ...)."""
    v = np.linalg.svd(M)[2][np.linalg.matrix_rank(M)]
    return v / v[np.argmax(np.abs(v))]


NA = null_basis(A)                       # (1, 1, 1): equal potentials
NAT = null_basis(A.T)                    # the loop current
if NAT[0] < 0:
    NAT = -NAT                           # orient the loop 1->2->3->1: forward on e1 and e2, backward on e3
XN = (X @ NA) / (NA @ NA) * NA           # x's part in N(A)
XR = X - XN                              # and the rest, in R(Aᵀ)
E = A @ X                                # edge voltages; the same as A @ XR
YL = (Y @ NAT) / (NAT @ NAT) * NAT       # y's loop part, in N(Aᵀ)
YC = Y - YL                              # and the rest, in R(A) (an orthogonal split only with equal resistors)
K = A.T @ A                              # the graph Laplacian with C = I

# One color per subspace for the whole clip, from roles that read as different hues (gallery-palette). R(A) is GOOD
# because every solvable b lives there; N(A^T) is FLOW because it's a current; N(A) is WARN, the other null space's
# second actor (both were blue in the chat's original, when WARN was FLOW).
C_ROW, C_NUL, C_COL, C_LNUL = ACCENT, WARN, GOOD, FLOW
ZOOM = 0.85

NODE_POS = {1: np.array([-1.25, -0.8, 0]), 2: np.array([1.25, -0.8, 0]), 3: np.array([0, 1.2, 0])}
EDGES = [(1, 2), (2, 3), (1, 3)]


def fmt(v: np.ndarray) -> str:
    return "(" + ",".join(f"{round(c)}" for c in v) + ")"


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def triangle_graph() -> VGroup:
    """The circuit as a picture, built at its final size so no label drops under SMALL. .nodes maps 1..3 to circles."""
    g = VGroup()
    centroid = sum(NODE_POS.values()) / 3
    for k, (i, j) in enumerate(EDGES):
        a, b = NODE_POS[i], NODE_POS[j]
        d = unit(b - a)
        g.add(Arrow(a + 0.33 * d, b - 0.33 * d, buff=0, color=MUTED, stroke_width=5,
                    max_tip_length_to_length_ratio=0.15))
        out = np.array([-d[1], d[0], 0])
        out = out if out @ ((a + b) / 2 - centroid) > 0 else -out      # each edge label pushed away from the middle
        g.add(tex(f"e_{k + 1}", size=SMALL, color=MUTED).move_to((a + b) / 2 + 0.4 * out))
    g.nodes = {}
    for k, p in NODE_POS.items():
        node = VGroup(Circle(radius=0.3, color=INK, stroke_width=3, fill_color=GROUND, fill_opacity=1),
                      tex(str(k), size=SMALL)).move_to(p)
        g.nodes[k] = node
        g.add(node)
    return g


class FourSubspaces3D(MnScene3D):
    def construct(self) -> None:
        self.next_section("the setup")
        self.setup_2d()
        self.next_section("null space")
        self.node_space()           # runs its own sections: null space, row space, edge-on, split x
        self.next_section("column space is KVL")
        self.edge_space()           # column space, left null space, Tellegen, split y
        self.next_section("the circuit equation")
        self.circuit()
        self.next_section("past 3D")
        self.beyond()

    # ---- shared pieces ----
    def say(self, text: str, wait: float = 2.4) -> None:
        self.note(text)
        self.wait(wait)

    def space(self, normal: np.ndarray, show: list[np.ndarray], names: tuple[str, str, str], heading: str
              ) -> tuple[VGroup, VGroup]:
        """One ℝ³: the camera on the view computed from the plane's normal (one that also draws the vectors split
        later at a readable length), quiet axes, the circuit inset, and a heading that says which ℝ³ this is: they
        look identical, and they are not the same space."""
        self.view = oblique_view(normal, show=show)
        self.set_camera_orientation(*self.view, zoom=ZOOM)
        ax, labels = self.axes(names)
        inset = triangle_graph().scale(0.8)
        for t in [m for m in inset.get_family() if isinstance(m, MathTex)]:
            t.scale(1 / 0.8)                           # the drawing shrinks, its labels stay at SMALL
        self.inset = self.pin(inset.to_corner(UL, buff=SAFE + 0.15))
        self.heading = self.pin(words(heading, size=SMALL, color=MUTED).to_corner(UR, buff=SAFE + 0.05))
        self.play(Create(ax), FadeIn(labels), FadeIn(self.inset), FadeIn(self.heading))
        return ax, labels

    def subspace_pair(self, line_dir: np.ndarray, plane_normal: np.ndarray, line_color: ManimColor,
                      plane_color: ManimColor) -> tuple[Line, VGroup, VMobject]:
        """A line and the plane perpendicular to it, with the right-angle mark between the line and the plane's level
        direction b1 (the one edge_on_view(normal, b1) shows square)."""
        line = line_through(line_dir, 6.2, color=line_color)
        plane = plane_patch(plane_normal, size=4.0, color=plane_color)
        corner = right_angle_3d(line_dir, plane.b1, 0.35, color=INK)
        self.keep_clear(line)
        return line, plane, corner

    def edge_on(self, normal: np.ndarray, b1: np.ndarray, stacked: list[Mobject], added: Sequence[Animation] = ()
                ) -> None:
        """Go edge-on: the plane becomes a line and the right angle is drawn as 90°. Labels pile up there (two axes
        project onto one screen line, the third onto the normal), so the caller passes the ones to fade first."""
        self.play(*(FadeOut(s) for s in stacked))
        # A little further out: edge-on, the normal runs the full height of the frame, and its label must clear the
        # caption.
        self.move_camera(*edge_on_view(normal, b1), zoom=0.72, run_time=2.5, added_anims=list(added))

    # ---- 2D: the matrix ----
    def setup_2d(self) -> None:
        title = words("The four subspaces, drawn in 3D", size=TITLE)
        sub = words("a triangle circuit $\\cdot$ a companion to Strang L12", size=SMALL, color=MUTED)
        VGroup(title, sub).arrange(DOWN, buff=0.3)
        self.play(Write(title), FadeIn(sub, shift=0.2 * UP))
        self.wait(1)
        self.play(FadeOut(title), FadeOut(sub))
        # Picture left, algebra right (house rule: don't stack them).
        g = triangle_graph().shift(3.4 * LEFT + 0.5 * UP)
        M = IntegerMatrix(A.astype(int), h_buff=1.0, v_buff=0.8)
        mat = VGroup(tex("A="), M).arrange(RIGHT, buff=0.2).shift(2.8 * RIGHT + 0.5 * UP)
        rows = VGroup(*(tex(f"e_{i + 1}", size=SMALL, color=MUTED).next_to(M, RIGHT, buff=0.3)
                        .match_y(M.get_rows()[i]) for i in range(m)))
        cols = VGroup(*(tex(f"x_{j + 1}", size=SMALL, color=MUTED).next_to(M, UP, buff=0.2)
                        .match_x(M.get_columns()[j]) for j in range(n)))
        self.play(FadeIn(g), Write(mat), FadeIn(rows), FadeIn(cols))
        self.say(rf"{m} edges, {n} nodes: $A$ is ${m}\times{n}$ with rank {r}.")
        self.say(r"Both of its spaces are $\mathbb{R}^3$: each subspace is a line or a plane.")

    # ---- node space: N(A) and R(A^T) ----
    def node_space(self) -> None:
        self.clean()
        _, labels = self.space(NA, [X, XR, XN], ("x_1", "x_2", "x_3"), r"node space: potentials $x$")
        line, plane, corner = self.subspace_pair(NA, NA, C_NUL, C_ROW)
        # At the near end, lifted off the line on screen (the far end runs into x_2's label).
        lab_n = self.label("N(A)", -3.2 * unit(NA) + nudge(self.view, up=0.4), color=C_NUL)
        self.play(Create(line), FadeIn(lab_n))
        self.say(rf"$N(A)$ is the line through ${fmt(NA)}$: every node at one voltage.")
        self.say(r"Lift every node by 5 V and no edge notices. Grounding a node fixes it.")

        self.next_section("row space")
        rows = [vec3(row, color=C_ROW) for row in A]
        row_labels = [self.label(f"r_{i + 1}", 1.3 * row, color=C_ROW) for i, row in enumerate(A)]
        # From above, x_3's label sits between r_1's and r_3's: it steps out while the rows are up.
        self.play(*(Create(v) for v in rows), *(FadeIn(lab) for lab in row_labels), FadeOut(labels[2]))
        self.say(r"The rows of $A$ lie in one plane, since $r_3=r_1+r_2$.")
        lab_r = self.label(r"R(A^T)", 1.6 * plane.b2 - 2.0 * plane.b1, color=C_ROW, plate=True)
        self.play(FadeIn(plane), Create(corner), FadeIn(lab_r))
        self.say(rf"Each row sums to 0, so $R(A^T)\perp{fmt(NA)}$.")

        self.next_section("edge-on")
        # A label is pinned to a 3D point, and N(A)'s point (beside the line's end) lands on the line edge-on: it
        # gets its own label for this view, beside the line's top end.
        edge = edge_on_view(NA, plane.b1)
        lab_n_edge = self.label("N(A)", 3.1 * unit(NA) + nudge(edge, right=0.75), color=C_NUL)
        self.edge_on(NA, plane.b1, [*row_labels, lab_r, labels[0], labels[1], lab_n])   # x_3 is out already
        self.play(FadeIn(lab_n_edge))
        self.say(r"Edge-on the plane is a line, and the corner is a true $90^\circ$.")
        self.say(r"Only from here does the right angle look like one.", wait=1.6)

        self.next_section("split x")
        self.move_camera(*self.view, zoom=ZOOM, run_time=2,
                         added_anims=[*(FadeOut(v) for v in rows), FadeOut(lab_n_edge), FadeIn(lab_n)])
        split = split_parallelogram(X, XR, XN, color_v=INK, color_a=C_ROW, color_b=C_NUL)
        # Each label past its tip; x_n's lies on N(A) itself, so it is nudged off the line on screen.
        # Beside the tip, to the right and a little down: the two dashed sides arrive from the left and from above.
        names = [self.label(rf"x={fmt(X)}", X + nudge(self.view, right=1.0, up=-0.2), color=INK),
                 self.label("x_r", 1.4 * XR, color=C_ROW, plate=True),
                 self.label("x_n", 0.5 * XN + nudge(self.view, up=0.4), color=C_NUL, plate=True)]
        self.keep_clear(split)
        self.play(FadeIn(lab_r), *(FadeIn(lab) for lab in labels), Create(split.whole), FadeIn(names[0]))
        self.play(Create(split.a), Create(split.b), Create(split.sides), FadeIn(names[1]), FadeIn(names[2]))
        self.say(rf"$x=x_r+x_n={fmt(XR)}+{fmt(XN)}$.")
        self.say(rf"$A$ kills $x_n$; only $x_r$ drives current: $Ax=Ax_r={fmt(E)}$.")

    # ---- edge space: R(A) and N(A^T) ----
    def edge_space(self) -> None:
        self.clean()
        _, labels = self.space(NAT, [Y, YC, YL, E], ("e_1", "e_2", "e_3"), r"edge space: voltages $e$, currents $y$")
        self.say(r"A different $\mathbb{R}^3$: one axis per edge now, not per node.", wait=1.8)
        line, plane, corner = self.subspace_pair(NAT, NAT, C_LNUL, C_COL)
        cols = [vec3(c, color=C_COL) for c in A.T]
        col_labels = [self.label(f"a_{j + 1}", 1.3 * c, color=C_COL) for j, c in enumerate(A.T)]
        self.play(*(Create(v) for v in cols), *(FadeIn(lab) for lab in col_labels))
        self.say(r"The columns of $A$ span $R(A)$: every voltage pattern that potentials make.")
        kvl = self.pin(tex(r"e_1+e_2-e_3=0", size=SMALL, color=C_COL).next_to(self.heading, DOWN, buff=0.3)
                       .align_to(self.heading, RIGHT))
        lab_c = self.label("R(A)", -1.6 * plane.b2 - 1.4 * plane.b1, color=C_COL, plate=True)
        self.play(FadeIn(plane), FadeIn(lab_c), FadeIn(kvl), *(FadeOut(lab) for lab in col_labels),
                  *(FadeOut(v) for v in cols))
        self.say(r"Every $Ax$ obeys $e_1+e_2-e_3=0$: KVL around the loop.")
        ax_vec = vec3(E, color=INK)
        lab_e = self.label(rf"Ax={fmt(E)}", 1.2 * E + nudge(self.view, up=-0.4), color=INK)
        self.play(Create(ax_vec), FadeIn(lab_e))
        self.say(r"$A$ took $x$ from node space to this point. The map itself would need 6D.")

        self.next_section("left null space is KCL")
        lab_l = self.label(r"N(A^T)", -3.55 * unit(NAT), color=C_LNUL)
        self.play(Create(line), Create(corner), FadeIn(lab_l))
        # The loop current as dots on the inset: one ValueTracker drives them, so they keep moving through every
        # later play and camera move. The path is the inset's own triangle, in screen space like the inset.
        loop = VMobject().set_points_as_corners([self.inset.nodes[k].get_center() for k in (1, 2, 3, 1)])
        clock = ValueTracker(0)
        dots = self.pin(flow_dots(loop, clock, current=1.0, n=6, speed=1.2, color=C_LNUL))
        y_lab = self.pin(tex(rf"y={fmt(NAT)}", size=SMALL, color=C_LNUL).next_to(self.inset, DOWN, buff=0.2))
        show_dots = ValueTracker(0.0)                  # they flow while they fade in: FadeIn would freeze them
        dots.add_updater(fader(dots, show_dots.get_value))
        self.add(dots)
        self.play(show_dots.animate.set_value(1.0), FadeIn(y_lab), clock.animate(rate_func=linear).set_value(1),
                  run_time=1)
        self.note(r"$N(A^T)$: currents with $A^Ty=0$. Nothing piles up at a node: KCL.")
        self.play(clock.animate.set_value(3.5), rate_func=linear, run_time=2.5)

        self.next_section("Tellegen")
        self.note(r"$y^T(Ax)=(A^Ty)^Tx=0$: KCL currents $\perp$ KVL voltages.")
        self.edge_on(NAT, plane.b1, [*labels, lab_e, lab_c],
                     added=[clock.animate(rate_func=linear).set_value(6.5)])
        self.note("That is Tellegen's theorem: the total power is 0.")
        self.play(clock.animate.set_value(8.5), rate_func=linear, run_time=2)

        self.next_section("split y")
        self.move_camera(*self.view, zoom=ZOOM, run_time=2,
                         added_anims=[clock.animate(rate_func=linear).set_value(10.5), FadeOut(ax_vec)])
        dots.clear_updaters()
        split = split_parallelogram(Y, YC, YL, color_v=INK, color_a=C_COL, color_b=C_LNUL)
        # y's label starts past its tip; y_loop lies on N(A^T), so its label sits off the middle of its arrow.
        names = [self.label(rf"y={fmt(Y)}", Y + nudge(self.view, right=1.05, up=0.3), color=INK),
                 self.label("y_c", 1.35 * YC, color=C_COL, plate=True),
                 self.label(r"y_{\text{loop}}", 0.5 * YL + nudge(self.view, right=-0.45, up=-0.5), color=C_LNUL,
                            plate=True)]
        self.keep_clear(split)
        # e_2's label would sit on y_c's arrow in this view: it stays out.
        self.play(FadeOut(dots), FadeOut(y_lab), FadeIn(labels[2]), FadeIn(lab_c),
                  Create(split.whole), FadeIn(names[0]))
        self.play(Create(split.a), Create(split.b), Create(split.sides), FadeIn(names[1]), FadeIn(names[2]))
        self.say(rf"Equal resistors: $y=y_c+y_{{\text{{loop}}}}={fmt(YC)}+{fmt(YL)}$.")
        self.say(rf"Nodes feel only $y_c$: $A^Ty=A^Ty_c={fmt(A.T @ Y)}$.")
        self.say(r"Unequal resistors tilt this split. Tellegen holds either way.")

    # ---- 2D again: the equation and the count ----
    def circuit(self) -> None:
        self.clean()
        self.set_camera_orientation(phi=0, theta=-90 * DEGREES, zoom=1)   # flat: plain mobjects draw as in 2D
        specs = [("x", C_ROW, "potentials"), ("e=Ax", C_COL, "voltages"), ("y=-Ce", C_LNUL, "currents"),
                 ("A^Ty=-f", C_ROW, "KCL")]
        boxes = [VGroup(tex(t, color=c), words(lab, size=SMALL, color=MUTED)).arrange(DOWN, buff=0.15)
                 for t, c, lab in specs]
        VGroup(*boxes).arrange(RIGHT, buff=1.1).shift(1.6 * UP)        # placed together, shown one by one
        arrows = [Arrow(boxes[i].get_right(), boxes[i + 1].get_left(), buff=0.12, color=MUTED) for i in range(3)]
        over = [tex(s, size=SMALL).next_to(a, UP, buff=0.05) for s, a in zip(("A", "C", "A^T"), arrows, strict=True)]
        for i, b in enumerate(boxes):
            self.play(FadeIn(b, shift=0.2 * RIGHT), *((GrowArrow(arrows[i - 1]), FadeIn(over[i - 1])) if i else ()),
                      run_time=0.7)
        self.say("Strang's chain: potentials, voltages, currents, balance.")
        self.say(r"$C$ holds the conductances $1/R$; the minus: current runs downhill.")
        Kx = tex(r"A^TCA\,x=f").shift(0.2 * DOWN)
        Km = tex(r"A^TA=", r"\begin{bmatrix}" + r"\\".join("&".join(str(int(v)) for v in row) for row in K)
                 + r"\end{bmatrix}", size=SMALL + 4).next_to(Kx, DOWN, buff=0.4)
        self.play(Write(Kx), FadeIn(Km))
        self.say(r"With every $R=1$, $A^TCA$ is the graph Laplacian $A^TA$.")
        self.say(rf"It is singular: $A^TA\,{fmt(NA)}=0$, the same $N(A)$ as before.")
        cond = tex(r"A^TCA\,x=f\ \text{solvable}\iff f\perp" + fmt(NA) + r"\iff f_1+f_2+f_3=0",
                   size=SMALL + 4).next_to(Kx, DOWN, buff=0.5)
        self.play(FadeOut(Km), Write(cond))
        self.say("Current pushed in must equal current pulled out. Then ground a node.")

        self.next_section("dimensions count the circuit")
        self.clean()
        counts = arrange_rows([
            tex(rf"\dim N(A)=n-r={n - r}", r"\ \to\ \text{connected pieces}", color=C_NUL),
            tex(rf"\dim R(A^T)=\dim R(A)=r={r}", color=C_ROW),
            tex(rf"\dim N(A^T)=m-r={m - r}", r"\ \to\ \text{independent loops}", color=C_LNUL),
            tex(r"\text{nodes}-\text{edges}+\text{loops}=" + str(n - m + (m - r)), color=INK),
        ], buff=0.45).move_to(0.3 * UP)
        self.play(LaggedStart(*(FadeIn(row, shift=0.2 * RIGHT) for row in counts), lag_ratio=0.4), run_time=2.4)
        self.say("The rank theorem is counting the circuit: Euler's formula.")

    # ---- past 3D ----
    def beyond(self) -> None:
        self.clean()
        P = {1: np.array([-1.3, -1.2, 0]), 2: np.array([1.3, -1.2, 0]), 3: np.array([1.3, 1.2, 0]),
             4: np.array([-1.3, 1.2, 0])}
        E2 = [(1, 2), (2, 3), (3, 4), (1, 4), (1, 3)]
        A2 = np.zeros((len(E2), len(P)))
        for k, (i, j) in enumerate(E2):
            A2[k, i - 1], A2[k, j - 1] = -1, 1
        m2, n2, r2 = A2.shape[0], A2.shape[1], int(np.linalg.matrix_rank(A2))
        g = VGroup()
        for i, j in E2:
            d = unit(P[j] - P[i])
            g.add(Arrow(P[i] + 0.32 * d, P[j] - 0.32 * d, buff=0, color=MUTED, stroke_width=5,
                        max_tip_length_to_length_ratio=0.1))
        for k, p in P.items():
            g.add(VGroup(Circle(radius=0.3, color=INK, stroke_width=3, fill_color=GROUND, fill_opacity=1),
                         tex(str(k), size=SMALL)).move_to(p))
        shift = 3.6 * LEFT + 0.2 * DOWN
        g.shift(shift)
        loops = VGroup(Polygon(P[1], P[2], P[3], color=C_LNUL, fill_opacity=0.25, stroke_width=0),
                       Polygon(P[1], P[3], P[4], color=C_LNUL, fill_opacity=0.08, stroke_width=0)).shift(shift)
        info = arrange_rows([
            tex(rf"A\ \text{{is}}\ {m2}\times{n2},\quad r={r2}", size=SMALL + 4),
            tex(rf"N(A):\ \text{{line in }}\mathbb{{R}}^{n2}", size=SMALL + 4, color=C_NUL),
            tex(rf"R(A^T):\ {r2}\text{{D in }}\mathbb{{R}}^{n2}", size=SMALL + 4, color=C_ROW),
            tex(rf"R(A):\ {r2}\text{{D in }}\mathbb{{R}}^{m2}", size=SMALL + 4, color=C_COL),
            tex(rf"N(A^T):\ {m2 - r2}\text{{D in }}\mathbb{{R}}^{m2}\ ({m2 - r2}\ \text{{loops}})", size=SMALL + 4,
                color=C_LNUL),
        ], buff=0.3).shift(2.7 * RIGHT + 0.3 * UP)
        self.keep_clear(g)
        self.play(FadeIn(loops), FadeIn(g))
        self.play(LaggedStart(*(FadeIn(i, shift=0.2 * LEFT) for i in info), lag_ratio=0.25), run_time=2.2)
        self.say(rf"{n2} nodes, {m2} edges: the spaces live in $\mathbb{{R}}^{n2}$ and $\mathbb{{R}}^{m2}$.")
        self.say("Same right angles, but the picture can't follow anymore.")

        self.next_section("the two right angles")
        self.clean()
        s1 = tex(r"N(A)", r"\perp", r"R(A^T)", size=TITLE)
        s1[0].set_color(C_NUL)
        s1[2].set_color(C_ROW)
        s2 = tex(r"N(A^T)", r"\perp", r"R(A)", size=TITLE)
        s2[0].set_color(C_LNUL)
        s2[2].set_color(C_COL)
        t1 = words(r"ground voltage $\perp$ what the edges can feel", size=SMALL, color=MUTED)
        t2 = words(r"loop currents (KCL) $\perp$ voltages (KVL)", size=SMALL, color=MUTED)
        self.play(FadeIn(VGroup(s1, t1, s2, t2).arrange(DOWN, buff=0.3), shift=0.2 * UP))
        self.wait(3)

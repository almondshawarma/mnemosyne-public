"""Gallery: a 3×3 matrix acting on space, the 3D version of the linear-map clip.

The columns of A are where e1, e2, e3 land, and |det A| is how volumes scale: the unit cube becomes the parallelepiped
spanned by the columns. As in linear_map.py, invisible dots ride the transformation and the arrows are redrawn from
them every frame, so a tip is never sheared. The cube is 12 edges (Lines) and 3 faces, not a solid: cheap to orbit.

The view is computed (`clear_view`) from the basis and A's columns together. The first try, a nice-looking
phi 65°, theta −40°, drew A e1 and A e2 along almost one screen line, and the parallelepiped looked flat.
"""

from fractions import Fraction

from amanim import clear_view, vec3
from mn_style import *

CLIP = {
    "title": "Gallery: a 3×3 matrix moves space",
    "technique": "matrices: apply_matrix on a 3D lattice and unit cube; arrows redrawn from riding dots; pinned matrix",
    "watch_for": "The columns of A are where e1, e2, e3 land, and the cube's volume becomes |det A|.",
    "scene": "Matrix3D",
}

A = np.array([[1.0, 0.8, 0.0], [0.0, 1.4, 0.6], [0.5, 0.0, 1.0]])   # det 1.64: a change you can see
COLORS = (GOOD, FLOW, ACCENT)        # e1, e2, e3: the columns keep their color in the matrix


def num(v: float) -> str:
    f = Fraction(float(v)).limit_denominator(10)
    return str(f.numerator) if f.denominator == 1 else f"{float(v):g}"


def cube_edges() -> VGroup:
    corners = [np.array(c, dtype=float) for c in np.ndindex(2, 2, 2)]
    # Quieter than the arrows, which run along three of these edges and must stay on top of them.
    return VGroup(*(Line(a, b, color=INK, stroke_width=2, stroke_opacity=0.55) for i, a in enumerate(corners)
                    for b in corners[i + 1:] if np.isclose(np.abs(a - b).sum(), 1)))


def lattice(extent: int = 2) -> VGroup:
    """Lines of the integer grid through the cube's neighborhood, in the three axis directions."""
    r = range(-extent, extent + 1)
    lines = VGroup()
    for d in np.eye(3):
        others = [e for e in np.eye(3) if not np.allclose(e, d)]
        for i in r:
            for j in r:
                c = i * others[0] + j * others[1]
                lines.add(Line(c - extent * d, c + extent * d, color=GRID, stroke_width=1))
    return lines


class Matrix3D(MnScene3D):
    def construct(self) -> None:
        # One view where neither the cube nor its image has two edges stacked on one screen line.
        self.set_camera_orientation(*clear_view(*np.eye(3), *A.T), zoom=0.8)
        grid = lattice()
        cube = cube_edges()
        faces = VGroup(*(Polygon(ORIGIN, a, a + b, b, stroke_width=0, fill_color=MUTED, fill_opacity=0.25)
                         for a, b in ((RIGHT, UP), (UP, OUT), (OUT, RIGHT))))
        riders = VGroup(*(Dot(e, radius=0.01).set_opacity(0) for e in np.eye(3)))
        world = VGroup(grid, cube, faces, riders)
        arrows = [always_redraw(lambda k=k: vec3(riders[k].get_center(), color=COLORS[k], stroke_width=7)
                                .set_z_index(3)) for k in range(3)]

        self.next_section("basis")
        self.play(Create(grid, lag_ratio=0.01), run_time=1.5)
        self.play(Create(cube), FadeIn(faces), *(Create(a) for a in arrows))
        names = [self.label(rf"\vec e_{k + 1}", 1.35 * np.eye(3)[k], color=COLORS[k]) for k in range(3)]
        self.play(*(FadeIn(n) for n in names))
        self.note(r"The unit cube, spanned by $\vec e_1,\vec e_2,\vec e_3$.")

        self.next_section("apply A")
        body = Matrix([[num(v) for v in row] for row in A], left_bracket="(", right_bracket=")",
                      element_to_mobject=lambda s: tex(s, size=SMALL), h_buff=0.95, v_buff=0.6)
        for k, col in enumerate(body.get_columns()):
            col.set_color(COLORS[k])
        panel = self.pin(backed(VGroup(tex("A="), body).arrange(RIGHT, buff=0.2)).to_corner(UL, buff=SAFE + 0.1))
        self.play(FadeIn(panel), *(FadeOut(n) for n in names))
        self.play(world.animate.apply_matrix(A), run_time=3)
        vol = self.pin(backed(tex(r"\text{volume}=|\det A|=", f"{abs(np.linalg.det(A)):.2f}"))
                       .next_to(panel, DOWN, aligned_edge=LEFT, buff=0.3))
        self.play(FadeIn(vol))
        self.note(r"Columns of $A$: where the basis lands. Volumes scale by $|\det A|$.")
        self.wait(1)

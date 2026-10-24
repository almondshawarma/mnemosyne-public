"""Gallery: a parametric surface as a wireframe of its u- and v-lines.

A Surface is hundreds of shaded faces and costs 4–13 s of rendering per second of camera motion; the wireframe of the
same surface costs about 0.35 (3d.md's table). Its lines bunch where the surface turns away, which is the shading the
eye needs. Coloring the u-lines and v-lines differently shows the parametrization itself: the coordinate curves.
"""

from amanim import wireframe
from mn_style import *

CLIP = {
    "title": "Gallery: a parametric surface as a wireframe",
    "technique": "3D: wireframe(f, u_range, v_range) instead of Surface; u-lines and v-lines in two roles",
    "watch_for": "Each colour is one family of coordinate curves: u fixed (around the tube) and v fixed (around the "
                 "hole).",
    "scene": "ParametricSurface",
}

R, r = 2.0, 0.8          # a torus: R from the axis to the tube's center, r the tube's radius


def torus(u: float, v: float) -> np.ndarray:
    return np.array([(R + r * np.cos(v)) * np.cos(u), (R + r * np.cos(v)) * np.sin(u), r * np.sin(v)])


class ParametricSurface(MnScene3D):
    def construct(self) -> None:
        self.next_section("the surface")
        self.set_camera_orientation(phi=62 * DEGREES, theta=-50 * DEGREES, zoom=0.9)
        mesh = wireframe(torus, (0, TAU), (0, TAU), nu=24, nv=12, color=FLOW)
        mesh.u_lines.set_color(ACCENT)
        self.play(Create(mesh.v_lines, lag_ratio=0.05), run_time=1.5)
        self.play(Create(mesh.u_lines, lag_ratio=0.05), run_time=1.5)
        formula = self.pin(tex(r"\vec r(u,v)=\big((R+r\cos v)\cos u,\ (R+r\cos v)\sin u,\ r\sin v\big)",
                               size=SMALL).to_edge(UP, buff=SAFE + 0.1))
        self.play(FadeIn(formula))
        self.note(r"Orange: $u$ fixed, around the tube. Blue: $v$ fixed, around the hole.")

        self.next_section("turn it")
        self.move_camera(phi=35 * DEGREES, theta=20 * DEGREES, run_time=3)
        self.wait(0.5)

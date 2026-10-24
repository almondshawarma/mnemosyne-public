"""Gallery: a potential as a surface, with its contour map underneath.

The potential is computed (four rods, the Paul trap's quadrupole, from amanim's coulomb_potential), drawn as a height
wireframe, and its equipotentials are drawn twice: flat on the floor (the map) and lifted onto the surface (the map
is the surface's skeleton). The same `levels` make both, so each floor contour sits right under its ring.
"""

from amanim import coulomb_potential, equipotentials, height_wireframe
from mn_style import *

CLIP = {
    "title": "Gallery: a potential surface over its contour map",
    "technique": "3D: height_wireframe of a computed potential; equipotentials flat on the floor and lifted onto it",
    "watch_for": "A saddle: the potential rises along x and falls along y, so a charge is held in one direction and "
                 "pushed out in the other.",
    "scene": "PotentialSurface",
}

R0 = 1.5                                            # the rods sit at (±R0, 0) and (0, ±R0)
V = coulomb_potential([((R0, 0), 1), ((-R0, 0), 1), ((0, R0), -1), ((0, -R0), -1)])
SPAN = 0.95                                         # stay well inside the rods, where V is smooth
SCALE = 1.6 / abs(V((SPAN, 0)) - V((0, 0)))         # height per volt: the surface rises 1.6 units at x = SPAN
FLOOR = -2.9
LEVELS = np.linspace(-1.4, 1.4, 9) / SCALE + V((0, 0))   # evenly spaced in height, skipping the flat center


def height(x: float, y: float) -> float:
    return SCALE * (V((x, y)) - V((0, 0)))


class PotentialSurface(MnScene3D):
    def construct(self) -> None:
        # The map is drawn in the plot's own units, three frame units to one: a bigger picture than ±0.95 would be.
        k = 3.0 / SPAN

        def surf(x: float, y: float) -> float:
            return height(x / k, y / k)

        self.next_section("the map")
        self.set_camera_orientation(phi=0, theta=-90 * DEGREES, zoom=0.65)
        floor = equipotentials(lambda p: V((p[0], p[1])), LEVELS, color=MUTED, x_span=SPAN, n=160, opacity=0.9,
                               c2p=lambda x, y: np.array([k * x, k * y, FLOOR]))
        self.play(Create(floor, lag_ratio=0.1), run_time=1.5)
        self.note("Equipotentials of four rods: $+$ on the $x$ axis, $-$ on the $y$ axis.")

        self.next_section("the surface")
        # Low enough (phi 76°) that the floor map and the surface separate on screen instead of overprinting.
        self.move_camera(phi=76 * DEGREES, theta=-60 * DEGREES, run_time=2)
        surface = height_wireframe(surf, (-3, 3), (-3, 3), nu=15, nv=15, color=FLOW, opacity=0.7)
        rings = equipotentials(lambda p: V((p[0], p[1])), LEVELS, color=ACCENT, x_span=SPAN, n=160, opacity=1,
                               stroke_width=3, height=lambda x, y: height(x, y),
                               c2p=lambda x, y, z: np.array([k * x, k * y, z]))
        self.play(Create(surface, lag_ratio=0.02), run_time=2)
        self.play(Create(rings, lag_ratio=0.1), run_time=1.5)
        self.note("Each orange ring is a floor contour lifted to its own height: a saddle.")
        self.wait(1)

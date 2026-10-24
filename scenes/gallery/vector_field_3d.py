"""Gallery: a vector field in space, computed: the magnetic field around a long straight wire.

B = (μ0 I / 2π) φ̂ / s circles the wire and falls off as 1/s. vector_field_3d draws an arrow at each grid point with
length ∝ |B| (capped, so the arrows near the wire don't swamp the frame) and fades the weak ones. The grid skips the
points on the wire itself, where the field isn't defined. Arrows are vec3, so 50-odd of them still orbit cheaply.
"""

from amanim import line_through, vec3, vector_field_3d
from mn_style import *

CLIP = {
    "title": "Gallery: a 3D vector field (B around a wire)",
    "technique": "3D: vector_field_3d over a grid, arrow length ∝ |F| with a cap, a short orbit to show the circulation",
    "watch_for": "The field circles the wire the way your right hand's fingers curl around the current, and weakens "
                 "with distance but not along the wire.",
    "scene": "WireField",
}


def B(p: np.ndarray) -> np.ndarray:
    """Units with μ0 I / 2π = 1: B = φ̂ / s."""
    s2 = p[0] ** 2 + p[1] ** 2
    return np.array([-p[1], p[0], 0.0]) / s2


POINTS = [np.array([x, y, z]) for x in (-2, -1, 0, 1, 2) for y in (-2, -1, 0, 1, 2) for z in (-1.5, 0, 1.5)
          if (x, y) != (0, 0)]


class WireField(MnScene3D):
    def construct(self) -> None:
        self.next_section("the wire")
        self.set_camera_orientation(phi=65 * DEGREES, theta=-50 * DEGREES, zoom=0.85)
        wire = line_through(OUT, 5, color=INK, stroke_width=6)
        current = vec3(1.2 * OUT, 1.3 * OUT, color=ACCENT, stroke_width=7)
        self.play(Create(wire), Create(current), FadeIn(self.label("I", 2.9 * OUT + 0.4 * RIGHT, color=ACCENT)))
        self.note("A long straight wire, current $I$ upward.")

        self.next_section("the field")
        field = vector_field_3d(B, POINTS, scale=0.9, max_length=0.8, color=FLOW)
        self.play(LaggedStart(*(GrowFromCenter(a) for a in field), lag_ratio=0.02), run_time=2)
        self.note(r"$\vec B$ circles the wire and falls off as $1/s$.")
        # A short move to a computed-free view from above: the circulation reads best looking down the wire.
        self.move_camera(phi=20 * DEGREES, theta=-80 * DEGREES, run_time=2.5)
        self.wait(0.5)

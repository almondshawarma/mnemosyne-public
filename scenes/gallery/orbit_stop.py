"""Gallery: a camera orbit that stops exactly on a computed view.

`move_camera` goes straight to the number it's given, so an orbit is a theta unwrapped the long way round:
`orbit_to(theta_now, goal, turns=1)`. The goal is computed (here, the oblique view of a plane), so the orbit shows the
object from every side and then lands where the picture reads best. Use this instead of an ambient rotation: it costs
the same per second, and it ends where you choose (an ambient rotation leaves the still at a random angle).
"""

from amanim import oblique_view, orbit_to, plane_patch, vec3
from mn_style import *

CLIP = {
    "title": "Gallery: an orbit that stops on a computed view",
    "technique": "camera: orbit_to unwraps theta so move_camera orbits a full turn and lands on oblique_view(normal)",
    "watch_for": "The camera goes all the way round once and stops on the view where the plane reads as a plane.",
    "scene": "OrbitStop",
}

N = np.array([1.0, -2.0, 1.5])        # a plane with no friendly orientation: no guessed angle shows it well
U, V = np.array([2.0, 1.0, 0.0]), np.array([0.0, 0.75, 1.0])   # two vectors in it (both are ⟂ N)


class OrbitStop(MnScene3D):
    def construct(self) -> None:
        goal = oblique_view(N)

        self.next_section("start anywhere")
        # Start edge-on-ish on purpose (phi 75, theta matching the plane's level line): the plane is a sliver.
        level = np.arctan2(*np.cross([0, 0, 1], N)[1::-1])
        self.set_camera_orientation(phi=75 * DEGREES, theta=level, zoom=0.85)
        ax, names = self.axes()
        plane = plane_patch(N, size=4.2, color=ACCENT)
        u, v = vec3(U, color=GOOD), vec3(V, color=FLOW)
        lu, lv = self.label(r"\vec u", 1.2 * U, color=GOOD), self.label(r"\vec v", 1.3 * V, color=FLOW)
        self.play(Create(ax), FadeIn(names), FadeIn(plane), Create(u), Create(v), FadeIn(lu), FadeIn(lv))
        self.note("From a guessed angle the plane is a sliver.")

        self.next_section("orbit and land")
        # One full turn plus the way to the goal, in the same direction: the theta is unwrapped, not wrapped.
        theta = orbit_to(self.renderer.camera.get_theta(), goal.theta, turns=1)
        self.move_camera(phi=goal.phi, theta=theta, run_time=5, rate_func=smooth)
        self.note(r"One turn, then the view computed from the normal: $54^\circ$ off it.")
        self.wait(1)

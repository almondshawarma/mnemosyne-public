"""Gallery: a plane and the line perpendicular to it, from a computed view.

The pattern for any subspace-and-complement picture (row space and null space, a surface and its normal): the view
is computed from the plane's normal (`oblique_view`), the plane is a grid patch (the grid is what makes it read as
tilted), the line is a plain Line, and the labels face the camera. Then the edge-on view, computed too, is the one
place the right angle looks like 90°.
"""

from amanim import apparent_angle, edge_on_view, line_through, oblique_view, plane_patch, right_angle_3d
from mn_style import *

CLIP = {
    "title": "Gallery: a plane, its normal line, and the edge-on view",
    "technique": "3D: oblique_view and edge_on_view computed from the normal; plane_patch with grid; facing labels; "
                 "a live readout of the right angle's drawn size",
    "watch_for": "The right-angle mark is drawn at some other angle until the camera reaches the edge-on view.",
    "scene": "PlaneAndLine",
}

N = np.array([1.0, 1.0, 1.0])     # the plane is everything perpendicular to this


class PlaneAndLine(MnScene3D):
    def construct(self) -> None:
        home = oblique_view(N)                 # 54° off the normal: the plane shows as a plane
        b1, b2 = plane_patch(N).b1, plane_patch(N).b2
        edge = edge_on_view(N, b1)             # perpendicular to N and b1: the right angle N-b1 drawn true

        self.next_section("a plane and its line")
        self.set_camera_orientation(*home, zoom=0.85)
        ax, names = self.axes(("x", "y", "z"))
        plane = plane_patch(N, size=4.4, color=ACCENT)
        line = line_through(N, 6.4, color=FLOW)
        corner = right_angle_3d(N, b1, 0.4, color=INK)
        lp = self.label(r"N^\perp", 2.0 * b2 - 1.7 * b1, color=ACCENT)
        # Off the line's lower end: its upper end runs past the y axis's label in this view.
        ln = self.label(r"\mathrm{span}(1,1,1)", -3.3 * N / np.linalg.norm(N) + 0.35 * IN, color=FLOW)
        self.keep_clear(line)
        self.play(Create(ax), FadeIn(names))
        self.play(FadeIn(plane), Create(line), Create(corner), FadeIn(lp), FadeIn(ln))
        self.note(r"View computed from the normal: $54^\circ$ off $(1,1,1)$.")

        # A number pinned to the frame, recomputed every frame from the camera: what the right angle LOOKS like.
        drawn = DecimalNumber(0, num_decimal_places=0, unit=r"^\circ", font_size=SMALL + 4, color=INK)
        caption = words("the corner is drawn at", size=SMALL, color=MUTED)

        def update(d: DecimalNumber) -> None:
            cam = self.renderer.camera
            d.set_value(np.degrees(apparent_angle(N, b1, (cam.get_phi(), cam.get_theta()))))

        # Set the value before placing it: a DecimalNumber grows to the right as digits appear, so placed while it
        # read "0" it would run off the corner later (and set before the fade: the house rule on changing counts).
        update(drawn)
        readout = self.pin(VGroup(caption, drawn).arrange(RIGHT, buff=0.2).to_corner(UR, buff=SAFE + 0.1))
        drawn.add_updater(update)
        self.play(FadeIn(readout))
        self.wait(1)

        self.next_section("edge-on")
        # Edge-on, x and y project onto one screen line and z onto the normal, so their labels would stack on the
        # picture: fade them for this beat. Zoom out a little so the normal's label clears the caption.
        self.play(FadeOut(names), FadeOut(lp))
        self.move_camera(*edge, zoom=0.75, run_time=2.5)
        self.note(r"Edge-on: the plane is a line, and only here is the corner $90^\circ$.")
        self.wait(1)
        drawn.clear_updaters()

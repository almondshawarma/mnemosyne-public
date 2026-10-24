"""Gallery: a moving camera zooms into a detail, and the caption stays put.

MnZoomScene is MnScene on a MovingCameraScene. `self.camera.frame` is a rectangle in the scene: animate it smaller and
onto the detail, and everything zooms. Its `note()` and `pin()` keep the caption and overlays the same size on
screen. The example: e^x and its tangent line at 0 look the same up close, which is what a derivative is.
"""

from mn_style import *

CLIP = {
    "title": "Gallery: zoom into a detail with a moving camera",
    "technique": "camera: MnZoomScene, camera.frame.animate.scale().move_to() into a detail; caption pinned to the frame",
    "watch_for": "Up close, the curve and its tangent line become the same line: that's what the derivative says.",
    "scene": "Zoom",
}


class Zoom(MnZoomScene):
    def construct(self) -> None:
        self.next_section("the curve")
        axes = Axes(x_range=[-2, 2, 1], y_range=[0, 5, 1], x_length=8, y_length=5.4,
                    axis_config={"color": MUTED, "include_tip": False}).shift(0.45 * UP)   # clear of the caption
        curve = axes.plot(np.exp, x_range=[-2, 1.6], color=ACCENT, stroke_width=4)
        tangent = axes.plot(lambda x: 1 + x, x_range=[-2, 1.6], color=FLOW, stroke_width=3)
        point = Dot(axes.c2p(0, 1), color=INK, radius=0.06)
        labels = VGroup(tex("e^x", color=ACCENT).next_to(axes.c2p(1.45, np.exp(1.45)), LEFT, buff=0.2),
                        tex("1+x", color=FLOW).next_to(axes.c2p(1.6, 2.6), RIGHT, buff=0.15))
        self.play(Create(axes), Create(curve), Create(tangent), FadeIn(point), FadeIn(labels))
        self.note("$e^x$ and its tangent line at $x=0$.")

        self.next_section("zoom in")
        # Zoom 60x onto the point: at 25x the curve still visibly bends away (e^x - (1+x) is about x^2/2). Stroke
        # widths zoom too, so thin the lines first by the same factor, or they'd end up 60x as wide.
        z = 60
        self.play(curve.animate.set_stroke(width=4 / z), tangent.animate.set_stroke(width=3 / z),
                  point.animate.scale(1.5 / z), FadeOut(labels), FadeOut(axes))
        self.play(self.camera.frame.animate.scale(1 / z).move_to(point), run_time=3)
        self.note("Up close they are the same line. That is what $\\frac{d}{dx}e^x\\big|_0=1$ says.")
        self.wait(1)

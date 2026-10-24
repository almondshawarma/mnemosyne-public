"""Gallery: labels placed automatically in a crowded 3D picture, and placed again when the view changes.

`self.place(text, at, toward=..., avoid=[...])` tries spots around the point and keeps the first one the layout check
would call clear: in the frame, off other text (including labels placed but not shown yet), and off kept-clear lines.
A label belongs to one view, so the edge-on section places a fresh set instead of moving the old ones. Compare the
four-subspaces clip, whose labels were nudged by hand.
"""

from amanim import edge_on_view, line_through, oblique_view, plane_patch, split_parallelogram, vec3
from mn_style import *

CLIP = {
    "title": "Gallery: automatic label placement",
    "technique": "labels: self.place(text, at, toward=, avoid=) picks a clear spot; place again for a new view",
    "watch_for": "No label sits on a line or on another label, in either view, and none was positioned by hand.",
    "scene": "LabelPlacement",
}

N = np.array([1.0, 1.0, 1.0])
A = np.array([[-1, 1, 0], [0, -1, 1], [-1, 0, 1]], dtype=float)
X = np.array([2.0, 0.0, 1.0])
XN = (X @ N) / (N @ N) * N
XR = X - XN


class LabelPlacement(MnScene3D):
    def construct(self) -> None:
        self.next_section("crowded")
        self.set_camera_orientation(*oblique_view(N, show=[X, XR, XN]), zoom=0.85)
        ax, names = self.axes(("x_1", "x_2", "x_3"))
        plane = plane_patch(N, size=4.0, color=ACCENT)
        line = line_through(N, 6.2, color=WARN)
        rows = [vec3(r, color=ACCENT) for r in A]
        split = split_parallelogram(X, XR, XN, color_v=INK, color_a=ACCENT, color_b=WARN)
        self.keep_clear(line, split, *rows)
        self.add(ax, plane, line, *rows, split)
        # Every label: a point, and the direction it would like to sit in. place() finds where it can.
        labels = [self.place("N(A)", 3.1 * N / np.linalg.norm(N), color=WARN, toward=N),
                  *(self.place(f"r_{i + 1}", r, color=ACCENT, toward=r) for i, r in enumerate(A)),
                  self.place(r"x=(2,0,1)", X, toward=X),
                  self.place("x_r", XR, color=ACCENT, toward=XR, plate=True),
                  self.place("x_n", 0.5 * XN, color=WARN, toward=XN, plate=True),
                  self.place(r"R(A^T)", -1.4 * plane.b2, color=ACCENT, plate=True)]
        self.play(FadeIn(names), *(FadeIn(lab) for lab in labels))
        self.note("Nine labels, every one placed by the layout check.")

        self.next_section("edge-on, placed again")
        self.play(*(FadeOut(lab) for lab in labels), FadeOut(names))
        self.move_camera(*edge_on_view(N, plane.b1), zoom=0.72, run_time=2)
        again = [self.place("N(A)", 3.1 * N / np.linalg.norm(N), color=WARN, toward=N),
                 self.place(r"R(A^T)", 1.8 * plane.b1, color=ACCENT),
                 self.place(r"x", X, toward=X)]
        self.play(*(FadeIn(lab) for lab in again))
        self.note("A new view, new spots: labels belong to one view.")
        self.wait(1)

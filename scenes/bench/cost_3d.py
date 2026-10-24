"""What costs render time in a 3D clip: each block below holds one kind of mobject on screen while the camera moves
for 2 s (the case where a ThreeDScene redraws everything on every frame), and times it.

    uv run scripts/render.py scenes/bench/cost_3d.py --hq --amanim ../amanim

writes bench.json beside the draft on the Obelisk (media/render/.drafts/cost-3d/). The numbers are seconds of
rendering per second of video at the quality rendered, and they go into the clips skill's 3d.md cost table. Not a clip:
there's nothing to watch.
"""

import json
import time
from pathlib import Path

from amanim import grid_points, height_wireframe, plane_patch, vec3, vector_field_3d, wireframe
from mn_style import *

CLIP = {"title": "3D render cost benchmark", "scene": "Cost3D"}

VECS = [np.array(v, dtype=float) for v in
        [(2, 0, 1), (1, -1, 0), (1, 1, 1), (-1, 2, 0.5), (0, -2, 1.5), (-2, -1, -1), (1.5, 1.5, -1), (0, 1, 2),
         (-1, -1, 2)]]
SECONDS = 2.0


def sphere(u: float, v: float) -> np.ndarray:
    return 1.8 * np.array([np.cos(u) * np.sin(v), np.sin(u) * np.sin(v), np.cos(v)])


class Cost3D(MnScene3D):
    def construct(self) -> None:
        self.results: dict[str, float] = {}
        self.set_camera_orientation(phi=65 * DEGREES, theta=-40 * DEGREES, zoom=0.9)

        def blank() -> list[Mobject]:
            return []

        def axes() -> list[Mobject]:
            ax, names = self.axes()
            return [ax, names]

        def labels() -> list[Mobject]:
            return [self.label(f"v_{i}", 1.15 * v) for i, v in enumerate(VECS)]

        def solid(**kw: object) -> Callable[[], list[Mobject]]:
            return lambda: [Arrow3D(ORIGIN, v, color=ACCENT, **kw) for v in VECS]  # pyright: ignore[reportArgumentType]

        def surface(res: int) -> Callable[[], list[Mobject]]:
            return lambda: [Surface(lambda u, v: sphere(u, v), u_range=[0, TAU], v_range=[0, PI], resolution=(res, res),
                                    fill_color=FLOW, fill_opacity=0.5, checkerboard_colors=False)]

        blocks: list[tuple[str, Callable[[], list[Mobject]]]] = [
            ("nothing (encoding only)", blank),
            ("ThreeDAxes, no ticks", axes),
            ("9 labels (fixed orientation)", labels),
            ("9 vec3 line (Line + crossed tip)", lambda: [vec3(v, color=ACCENT) for v in VECS]),
            ("9 vec3 flat (Arrow)", lambda: [vec3(v, color=ACCENT, style="flat") for v in VECS]),
            ("9 Arrow3D, default resolution", solid()),
            ("9 Arrow3D, resolution 6", solid(resolution=6, thickness=0.025, height=0.22, base_radius=0.07)),
            ("9 Line3D", lambda: [Line3D(ORIGIN, v, color=ACCENT) for v in VECS]),
            ("9 Line", lambda: [Line(ORIGIN, v, color=ACCENT) for v in VECS]),
            ("1 plane_patch with grid", lambda: [plane_patch((1, 1, 1), color=ACCENT)]),
            ("Surface 16x16", surface(16)),
            ("Surface 32x32", surface(32)),
            ("wireframe 12x12 (same sphere)", lambda: [wireframe(sphere, (0, TAU), (0, PI), color=FLOW)]),
            ("height_wireframe 15x15", lambda: [height_wireframe(lambda x, y: np.exp(-(x * x + y * y) / 2),
                                                                 (-2.5, 2.5), (-2.5, 2.5), nu=15, nv=15, color=FLOW)]),
            ("vector_field_3d 4x4x4", lambda: [vector_field_3d(lambda p: np.array([-p[1], p[0], 0.3]),
                                                               grid_points(2, 4), color=FLOW)]),
        ]
        for name, build in blocks:
            self.timed_orbit(name, build())

        # The same busy picture under each kind of motion: is it the camera, the updater, or the moving thing?
        busy = [*axes(), plane_patch((1, 1, 1), color=ACCENT), *(vec3(v, color=ACCENT) for v in VECS), *labels()]
        heavy = [Arrow3D(ORIGIN, v, color=ACCENT, resolution=6) for v in VECS]
        self.timed("busy: static wait", busy, lambda: self.wait(SECONDS))
        self.timed("busy: ambient rotation", busy, self.ambient)
        dot = Dot(color=INK)
        clock = ValueTracker(0)
        dot.add_updater(lambda d: d.move_to(np.array([np.cos(clock.get_value()), np.sin(clock.get_value()), 0])))
        self.timed("busy: wait with one updater", [*busy, dot],
                   lambda: self.play(clock.animate.set_value(3), run_time=SECONDS, rate_func=linear))
        dot.clear_updaters()
        self.timed("busy: one arrow drawn in, camera still", busy,
                   lambda: self.play(Create(vec3((0, 0, 2.5), color=INK)), run_time=SECONDS))
        self.timed("9 Arrow3D: one arrow drawn in, camera still", heavy,
                   lambda: self.play(Create(vec3((0, 0, 2.5), color=INK)), run_time=SECONDS))

        # LaTeX: every distinct string compiles once per render (the cache starts empty in the sandbox).
        t0 = time.perf_counter()
        for i in range(10):
            tex(rf"x_{{{i}}} = {i}\cdot\vec v_{{{i}}}")
        self.results["10 distinct MathTex compiled (s each)"] = round((time.perf_counter() - t0) / 10, 3)
        self.write()

    def ambient(self) -> None:
        self.begin_ambient_camera_rotation(rate=0.4)
        self.wait(SECONDS)
        self.stop_ambient_camera_rotation()

    def timed_orbit(self, name: str, mobs: list[Mobject]) -> None:
        self.timed(name, mobs, lambda: self.move_camera(theta=self.camera.get_theta() + 60 * DEGREES,
                                                        run_time=SECONDS))

    def timed(self, name: str, mobs: list[Mobject], motion: Callable[[], None]) -> None:
        self.clear()
        self.add(*mobs)
        t0 = time.perf_counter()
        motion()
        self.results[name] = round((time.perf_counter() - t0) / SECONDS, 3)
        print(f"MN-BENCH {name}: {self.results[name]} s per video second", flush=True)

    def write(self) -> None:
        meta = {"pixels": f"{config.pixel_width}x{config.pixel_height}", "fps": config.frame_rate,
                "seconds_per_video_second": self.results}
        out = Path("/out")
        if out.is_dir():
            (out / "bench.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(json.dumps(meta, indent=2), flush=True)

"""Study mode's measurements (services/watch/study.py) on synthetic frames whose answers are known."""
import math
from itertools import pairwise

import numpy as np
import pytest
import study

FPS = 30.0
H, W = 90, 160


def blank(value: int = 0) -> np.ndarray:
    return np.full((H, W, 3), value, dtype=np.uint8)


def square(x: float, y: float, size: int = 12, color: tuple[int, int, int] = (230, 230, 230),
           base: np.ndarray | None = None) -> np.ndarray:
    f = blank() if base is None else base.copy()
    cx, cy = round(x * W), round(y * H)
    f[max(0, cy - size // 2):cy + size // 2, max(0, cx - size // 2):cx + size // 2] = color
    return f


def curve(frames: list[np.ndarray]) -> dict[str, list[float]]:
    """The raw curves watch_job.sample_span builds: what changed into each frame (nothing into the first)."""
    gray = [study.gray(f) for f in frames]
    c: dict[str, list[float]] = {"energy": [0.0], "moved": [0.0], "score": [0.0]}
    for a, b in pairwise(gray):
        e, m, s = study.pair(a, b)
        c["energy"].append(e)
        c["moved"].append(m)
        c["score"].append(s)
    c["t"] = [i / FPS for i in range(len(frames))]
    return c


def segs_of(frames: list[np.ndarray]) -> tuple[list[int], list[dict[str, object]]]:
    """Cuts and segments, the way watch_job.study_main finds them."""
    c = curve(frames)
    cuts = study.find_cuts(c["energy"], c["score"], FPS)
    return cuts, study.segments(study.speed(c["energy"], cuts, FPS), study.speed(c["moved"], cuts, FPS), c["t"], cuts)


def smooth(u: float) -> float:
    """Manim's default rate function's shape: an ease in-out."""
    return 3 * u * u - 2 * u ** 3


def test_a_cut_at_a_known_frame():
    # shot A: a square drifting slowly on the left; at frame 45 the picture is replaced by shot B (a bar, lower right)
    frames = [square(0.2 + 0.001 * i, 0.4, 14) for i in range(45)]
    bar = blank()
    bar[60:75, 90:150] = (60, 140, 230)
    frames += [bar] * 30
    assert segs_of(frames)[0] == [45]


def test_moves_are_not_cuts_even_fast_ones():
    # a big square crossing the frame quickly: every frame changes a lot, but so do its neighbors
    frames = [square(0.1 + 0.8 * smooth(i / 20), 0.5, 30) for i in range(21)]
    assert segs_of([frames[0]] * 15 + frames + [frames[-1]] * 15)[0] == []


def test_something_appearing_over_what_stays_is_not_a_cut():
    base = square(0.3, 0.5, 20)
    assert segs_of([base] * 20 + [square(0.7, 0.5, 20, base=base)] * 20)[0] == []


def test_an_eased_move_peaks_mid_move_between_holds():
    hold, steps = 15, 30
    path = [square(0.15 + 0.7 * smooth(i / steps), 0.5, 16) for i in range(steps + 1)]
    _, segs = segs_of([path[0]] * hold + path + [path[-1]] * hold)
    assert [s["kind"] for s in segs] == ["hold", "move", "hold"]
    move = segs[1]
    assert move["ease"] == "ease in-out"
    assert 0.35 <= move["peak_at"] <= 0.65   # type: ignore[operator]
    assert move["shape"] < 0.85              # type: ignore[operator]
    assert math.isclose(move["duration"], steps / FPS, abs_tol=4 / FPS)   # type: ignore[arg-type]
    assert math.isclose(segs[0]["duration"], (hold + 1) / FPS, abs_tol=3 / FPS)   # type: ignore[arg-type]


def test_a_thin_line_sweeping_slowly_is_a_move_not_a_hold():
    # one pixel wide, crawling a pixel every other frame: almost no energy, but visibly moving
    def line(x: int) -> np.ndarray:
        f = blank()
        f[35:55, x] = (240, 220, 60)
        return f
    frames = [line(40)] * 20 + [line(40 + i // 2) for i in range(60)] + [line(70)] * 20
    assert max(study.speed(curve(frames)["energy"], [], FPS)) < 0.0015   # a tenth of a slow full-frame move
    _, segs = segs_of(frames)
    assert [s["kind"] for s in segs] == ["hold", "move", "hold"]
    assert math.isclose(segs[1]["duration"], 2.0, abs_tol=0.15)   # type: ignore[arg-type]


def test_an_animation_drawn_at_half_the_frame_rate_is_one_smooth_move():
    # each pose shown twice (15 drawings a second in a 30 fps video): the raw curve is a comb of changes and zeros
    steps = 20
    poses = [square(0.15 + 0.7 * smooth(i / steps), 0.5, 16) for i in range(steps + 1)]
    frames = [poses[0]] * 15 + [p for p in poses for _ in (0, 1)] + [poses[-1]] * 15
    c = curve(frames)
    assert sum(e == 0 for e in c["energy"][16:16 + 2 * steps]) >= steps - 2    # the comb
    cuts, segs = segs_of(frames)
    assert cuts == []
    assert [s["kind"] for s in segs] == ["hold", "move", "hold"]
    assert segs[1]["ease"] == "ease in-out"
    assert sum(study.speed(c["energy"], [], FPS)) == pytest.approx(sum(c["energy"]))   # spread, not added


def test_film_rate_animation_in_a_30fps_video_reads_as_a_steady_speed():
    # 24 drawings in 30 frames (3:2 pulldown): every 5 frames, 4 changes and a repeat, irregularly placed
    raw = [0.0] + [0.0 if i % 5 == 2 else 0.03 for i in range(1, 61)]
    v = study.speed(raw, [], 30.0)
    middle = v[6:55]
    assert max(middle) - min(middle) < 0.003     # the ripple's gone: 0.024 a frame, steady
    assert sum(v) == pytest.approx(sum(raw), rel=0.05)


def test_a_cut_is_not_spread_over_the_frames_before_it():
    energy = [0.0, 0.01, 0.0, 0.01, 0.0, 0.2, 0.0, 0.0]
    assert study.spread_repeats(energy, [5]) == [0.0, 0.01, 0.005, 0.005, 0.0, 0.2, 0.0, 0.0]


def test_codec_flicker_is_not_motion():
    still = square(0.4, 0.5, 30)
    pulse = np.clip(still.astype(np.int16) + 3, 0, 255).astype(np.uint8)   # a keyframe's faint whole-frame shift
    frames = [still] * 20 + [pulse] + [still] * 20
    assert max(curve(frames)["energy"]) == 0.0
    assert [s["kind"] for s in segs_of(frames)[1]] == ["hold"]


def test_a_linear_move_is_steady():
    steps = 30
    path = [square(0.15 + 0.7 * i / steps, 0.5, 16) for i in range(steps + 1)]
    move = next(s for s in segs_of([path[0]] * 10 + path + [path[-1]] * 10)[1] if s["kind"] == "move")
    assert move["ease"] == "steady"


def test_a_snap_and_the_holds_around_it():
    frames = [square(0.3, 0.5, 16)] * 20 + [square(0.34, 0.5, 16)] * 20
    _, segs = segs_of(frames)
    assert [s["kind"] for s in segs] == ["hold", "move", "hold"]
    assert segs[1]["ease"] == "snap"


def test_a_cut_splits_the_segments():
    frames = [square(0.3, 0.5, 16)] * 20 + [square(0.7, 0.3, 30, (200, 60, 60))] * 20
    cuts, segs = segs_of(frames)
    assert cuts == [20]
    assert [s["kind"] for s in segs] == ["hold", "hold"]
    assert math.isclose(segs[1]["start"], 20 / FPS, abs_tol=0.001)   # type: ignore[arg-type]


def test_two_color_palette_shares():
    f = np.zeros((100, 100, 3), dtype=np.uint8)
    f[:] = (20, 24, 32)
    f[:, 70:] = (226, 167, 67)
    p = study.palette(f)
    assert [c["hex"] for c in p] == ["#141820", "#e2a743"]
    assert [c["share"] for c in p] == [0.7, 0.3]


def test_antialiased_edges_join_their_nearest_color():
    f = np.zeros((100, 100, 3), dtype=np.uint8)
    f[:, 60:] = 255
    f[:, 59] = (20, 20, 20)        # a column of nearly-black edge pixels
    f[:, 60] = (240, 240, 240)     # and of nearly-white ones
    p = study.palette(f, k=5)
    assert len(p) == 2
    assert p[0]["share"] == pytest.approx(0.6)


def test_tone_of_black_and_white():
    f = np.zeros((10, 10, 3), dtype=np.uint8)
    f[:, 5:] = 255
    t = study.tone([f])
    assert t["lightness"] == pytest.approx(50.0, abs=0.1)
    assert t["contrast"] == pytest.approx(50.0, abs=0.1)
    assert (t["dark"], t["light"]) == (0.0, 100.0)
    assert study.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)


def test_composition_finds_the_thirds_point():
    f = square(1 / 3, 1 / 3, 12)
    c = study.composition([f], (0, 0, 0))
    assert c["nearest"] == "upper-left third"
    assert c["off_by"] < 0.02
    assert c["coverage"] == pytest.approx(144 / (H * W), rel=0.05)
    assert c["grid"][0][0] == pytest.approx(0.25, abs=0.05)   # it sits on the corner of four cells


def test_composition_grid_and_box():
    c = study.composition([square(5 / 6, 5 / 6, 12)], (0, 0, 0))
    assert c["grid"][2][2] == 1.0
    assert c["nearest"] == "lower-right third"
    assert c["box"]["x"][0] > 2 / 3 and c["box"]["y"][1] < 1.0


def test_composition_of_an_empty_frame():
    assert study.composition([blank()], (0, 0, 0)) == {"coverage": 0.0}

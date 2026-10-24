"""Study mode's measurements (watch_job.py --study): timing, cuts, color, and composition from a span's frames.

Small pure functions on numpy arrays, so they're tested on synthetic frames (tests/test_study.py) and the container
only feeds them. What they can see is frame differences and pixel colors: speed is inferred from how much of the
picture changes between frames, not from tracking objects, and there's no sound.

- `pair`: the motion energy between two consecutive frames (mean absolute difference, 0-1) and a cut score.
- `spread_repeats`, `speed`: an animation drawn at fewer frames than the video carries, as a smooth speed curve.
- `find_cuts`: where a new shot starts, from isolated spikes in the energy that replaced what was on screen.
- `segments`: holds (nothing moving) and moves, each move with where its speed peaks and how peaked it is (easing).
- `palette`, `tone`: the dominant colors and their shares, mean lightness and contrast (CIE L*).
- `composition`: where the ink (anything not background) sits: centroid, spread, box, a 3x3 grid, the nearest
  thirds point, and how much of the frame it covers.
"""

from itertools import pairwise
from typing import Any

import numpy as np
from numpy.typing import NDArray

Gray = NDArray[np.float32]   # H x W, 0-1
RGB = NDArray[np.uint8]      # H x W x 3, or N x 3 pixels

INK = 0.08          # a gray pixel this far (0-1) from the frame's median is ink, for the cut score
CHANGED = 0.06      # a gray pixel that changes this much between frames has changed
NOISE = 0.02        # changes smaller than this (5 of 255) are compression flicker, not motion
CUT_SCORE = 0.5     # at least this share of the ink before and after the change must have changed
CUT_SPIKE = 3.0     # and the change must be this many times the motion around it (half a second each side)
CUT_FLOOR = 0.008   # and not tiny: mean change of the whole frame at least this
MIN_SHOT = 0.2      # seconds: two cuts closer than this are one (the bigger spike wins)
MOVED = 0.0003      # a hold: under this share of the pixels changing visibly (17 pixels of a 320 x 180 frame)
DUPLICATE = 0.0003  # energy (or moved share) below this is the same picture repeated
MIN_HOLD = 0.25     # seconds: stillness shorter than this is a pause inside a move, not a hold
SNAP = 0.2          # seconds: a move shorter than this is a snap
BG_DIST = 40.0      # RGB distance from the background color that counts as ink, for composition
SEED_DIST = 40.0    # palette colors closer than this (RGB distance) are one color


def gray(rgb: RGB) -> Gray:
    """Luma (Rec. 709 weights on the sRGB values), 0-1."""
    x = rgb.astype(np.float32) / 255.0
    return (0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]).astype(np.float32)


def _share(part: NDArray[np.bool_], whole: NDArray[np.bool_]) -> float:
    n = int(whole.sum())
    if n < max(4, whole.size // 1000):   # (almost) nothing there: all of it changed, vacuously
        return 1.0
    return float((part & whole).sum()) / n


def pair(a: Gray, b: Gray) -> tuple[float, float, float]:
    """What changed from frame a to frame b:
    - energy: the mean |b - a| (0-1), ignoring changes under NOISE (the codec's own flicker, which pulses at every
      keyframe). How much is moving: a full-screen pan is big, a thin arrow sweeping is small.
    - moved: the share of pixels that changed visibly (more than CHANGED). Whether anything is moving at all, however
      small: holds are where this is near zero.
    - cut score: the smaller of the share of a's ink that changed and the share of b's ink that changed. Near 1 when the
      whole picture was replaced (a cut, or the screen cleared at once); low when things move or appear over what
      stays."""
    d = np.abs(b - a)
    changed = d > CHANGED
    ink_a = np.abs(a - np.median(a)) > INK
    ink_b = np.abs(b - np.median(b)) > INK
    return (float(np.where(d >= NOISE, d, 0).mean()), float(changed.mean()),
            min(_share(changed, ink_a), _share(changed, ink_b)))


def spread_repeats(energy: list[float], cuts: list[int], longest: int = 3, same: float = DUPLICATE) -> list[float]:
    """Animations are often drawn at fewer frames a second than the video carries (3b1b's older videos: about 18 in
    30), so a move reads as change, nothing, change, nothing. Each change that follows a short run of repeated frames
    (at most `longest`) is spread evenly over them, so the curve is the change per frame of video: a speed. A longer
    run of stillness is a hold and stays still, and a cut stays one frame."""
    out = list(energy)
    run = 0
    for i, e in enumerate(energy):
        if e < same:
            run += 1
            continue
        if 0 < run <= longest and i - run > 0 and i not in cuts:
            share = e / (run + 1)
            for k in range(i - run, i + 1):
                out[k] = share
        run = 0
    return out


def speed(energy: list[float], cuts: list[int], fps: float) -> list[float]:
    """The curve to read timing from: repeats spread (`spread_repeats`), then a centered mean over 1/6 s inside each
    shot. Film-rate animation in a 30 fps video (24 drawings in 30 frames) puts 4 drawings in every 5 frames, so a
    1/6 s window holds a whole cycle and the ripple cancels. Cut frames keep their spike and aren't averaged in. Timing
    blurs by about 1/12 s, well under any hold worth naming."""
    spread = spread_repeats(energy, cuts)
    half = max(1, round(fps / 6)) // 2
    edges = sorted({0, len(energy), *cuts})
    out = list(spread)
    for lo, hi in pairwise(edges):
        for i in range(lo, hi):
            if i in cuts:
                continue
            near = [spread[j] for j in range(max(lo, i - half), min(hi, i + half + 1)) if j not in cuts]
            out[i] = float(np.mean(near))
    return out


def find_cuts(energy: list[float], score: list[float], fps: float) -> list[int]:
    """Frame indexes where a new shot starts. energy[i] and score[i] describe the change from frame i-1 into frame i
    (index 0 is the span's first frame, so it's never a cut). A cut replaced the picture (score >= CUT_SCORE), is a
    spike (CUT_SPIKE times the larger of the median energies of the half second before and after it), and isn't tiny
    (CUT_FLOOR). Moves are sustained, so their neighbors are as busy as they are; a cut is one frame."""
    e = np.asarray(energy, dtype=np.float64)
    w = max(2, round(fps / 2))
    found: list[int] = []
    for i in range(1, len(e)):
        if score[i] < CUT_SCORE or e[i] < CUT_FLOOR:
            continue
        before, after = e[max(1, i - w):i], e[i + 1:i + 1 + w]
        before, after = before[before >= DUPLICATE], after[after >= DUPLICATE]   # repeats say nothing about motion
        around = max(float(np.median(before)) if before.size else 0.0, float(np.median(after)) if after.size else 0.0)
        if e[i] < CUT_SPIKE * around:
            continue
        if found and (i - found[-1]) / fps < MIN_SHOT:
            if e[i] > e[found[-1]]:
                found[-1] = i
            continue
        found.append(i)
    return found


def segments(energy: list[float], moved: list[float], times: list[float], cuts: list[int]) -> list[dict[str, Any]]:
    """The span as alternating holds (moved under MOVED: nothing visibly changing) and moves, split at cuts. Pass the
    `speed` of both curves. Each: kind, start, end, duration (s). A move also has peak (its highest energy), peak_at
    (0 = speed peaks at its start, 1 = at its end, 0.5 = mid-move: an ease in-out), shape (mean / peak: about 1 for
    constant speed, 0.6 or so for a smooth ease, low for a single burst), and a word for it (snap, ease in-out, fast
    start, fast finish, steady)."""
    n = len(energy)
    if n == 0:
        return []
    dt = float(np.median(np.diff(times))) if n > 1 else 0.0
    still = [moved[i] < MOVED for i in range(n)]
    still[0] = still[1] if n > 1 else True   # the first frame has no change into it; it's like the next one
    for c in cuts:
        still[c] = still[c + 1] if c + 1 < n else still[c - 1]   # a cut isn't motion inside either shot
    # stillness shorter than MIN_HOLD is a pause inside a move
    i = 0
    while i < n:
        j = i
        while j < n and still[j] == still[i]:
            j += 1
        if still[i] and (j - i) * dt < MIN_HOLD and i > 0 and j < n:
            for k in range(i, j):
                still[k] = False
        i = j
    bounds = sorted({0, n, *cuts})
    out: list[dict[str, Any]] = []
    for lo, hi in pairwise(bounds):
        i = lo
        while i < hi:
            j = i
            while j < hi and still[j] == still[i]:
                j += 1
            end = times[j] if j < n else times[-1] + dt
            seg: dict[str, Any] = {"kind": "hold" if still[i] else "move", "start": round(times[i], 3),
                                   "end": round(end, 3), "duration": round(end - times[i], 3)}
            if not still[i]:
                seg |= _move_shape([energy[k] for k in range(i, j) if k not in cuts], end - times[i])
            out.append(seg)
            i = j
    return out


def _move_shape(e: list[float], duration: float) -> dict[str, Any]:
    if not e:
        return {"peak": 0.0, "peak_at": 0.5, "shape": 1.0, "ease": "snap"}
    peak = max(e)
    at = e.index(peak) / (len(e) - 1) if len(e) > 1 else 0.5
    shape = float(np.mean(e)) / peak if peak else 1.0
    if duration < SNAP:
        word = "snap"
    elif shape >= 0.85:
        word = "steady"
    elif at < 0.3:
        word = "fast start"
    elif at > 0.7:
        word = "fast finish"
    else:
        word = "ease in-out"
    return {"peak": round(peak, 4), "peak_at": round(at, 2), "shape": round(shape, 2), "ease": word}


# ---------- color ----------

def lightness(rgb: tuple[int, int, int] | NDArray[np.float64]) -> NDArray[np.float64]:
    """CIE L* (0-100) of sRGB colors (the last axis is R, G, B, 0-255)."""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    y = lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722
    return np.where(y > 216 / 24389, 116 * np.cbrt(y) - 16, y * 24389 / 27)


def luminance(rgb: tuple[int, int, int]) -> float:
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(lin @ np.array([0.2126, 0.7152, 0.0722]))


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """WCAG contrast ratio, 1 (same) to 21 (black on white). Text wants 4.5 or more."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def hexcolor(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def palette(pixels: RGB, k: int = 5) -> list[dict[str, Any]]:
    """The k (or fewer) dominant colors of some pixels (any shape ending in 3), each with its share of them, its
    hex, and its L*. Colors are binned (5 bits a channel, each bin keeping the mean of its real pixels), seeded from the
    biggest bins at least SEED_DIST apart, then refined by a few rounds of weighted k-means. Anti-aliased edges fall
    into the nearest color rather than becoming colors of their own."""
    px = pixels.reshape(-1, 3).astype(np.int64)
    if px.shape[0] == 0:
        return []
    key = (px[:, 0] >> 3) * 1024 + (px[:, 1] >> 3) * 32 + (px[:, 2] >> 3)
    bins, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
    sums = np.zeros((bins.size, 3), dtype=np.float64)
    np.add.at(sums, inverse, px.astype(np.float64))
    colors = sums / counts[:, None]
    weights = counts.astype(np.float64)
    seeds: list[NDArray[np.float64]] = []
    for b in np.argsort(-weights, kind="stable"):
        if all(np.linalg.norm(colors[b] - s) >= SEED_DIST for s in seeds):
            seeds.append(colors[b])
            if len(seeds) == k:
                break
    centers = np.array(seeds)
    nearest = np.zeros(bins.size, dtype=np.int64)
    for _ in range(8):
        dist = np.linalg.norm(colors[:, None, :] - centers[None, :, :], axis=2)
        nearest = dist.argmin(axis=1)
        for c in range(len(centers)):
            m = nearest == c
            if m.any():
                centers[c] = (colors[m] * weights[m, None]).sum(axis=0) / weights[m].sum()
    share = np.bincount(nearest, weights=weights, minlength=len(centers)) / weights.sum()
    out: list[dict[str, Any]] = []
    for c in np.argsort(-share, kind="stable"):
        if share[c] <= 0:
            continue
        rgb = (round(float(centers[c][0])), round(float(centers[c][1])), round(float(centers[c][2])))
        out.append({"hex": hexcolor(rgb), "rgb": list(rgb), "share": round(float(share[c]), 3),
                    "L": round(float(lightness(rgb)), 1)})
    return out


def tone(frames: list[RGB]) -> dict[str, float]:
    """Mean lightness (L*, 0-100), contrast (the standard deviation of L* over the pixels), and the dark and light
    ends (5th and 95th percentile L*)."""
    lstar = np.concatenate([lightness(f.reshape(-1, 3).astype(np.float64)) for f in frames])
    return {"lightness": round(float(lstar.mean()), 1), "contrast": round(float(lstar.std()), 1),
            "dark": round(float(np.percentile(lstar, 5)), 1), "light": round(float(np.percentile(lstar, 95)), 1)}


# ---------- composition ----------

ANCHORS = {"upper-left third": (1 / 3, 1 / 3), "upper-right third": (2 / 3, 1 / 3), "center": (0.5, 0.5),
           "lower-left third": (1 / 3, 2 / 3), "lower-right third": (2 / 3, 2 / 3)}


def composition(frames: list[RGB], bg: tuple[int, int, int]) -> dict[str, Any]:
    """Where the ink sits over some frames of a shot. Ink is anything at least BG_DIST from the background color,
    averaged over the frames into a weight map. Coordinates are 0-1 from the top left. Returns coverage (the share of
    the frame that's ink: the rest is negative space), centroid, spread (weighted standard deviation), box (where the
    middle 90% of the ink sits), grid (3x3 shares, rows top to bottom), and the nearest of the four thirds points and
    the center, with the distance to it."""
    if not frames:
        return {"coverage": 0.0}
    ref = np.asarray(bg, dtype=np.float64)
    w = np.mean([np.linalg.norm(f.astype(np.float64) - ref, axis=2) >= BG_DIST for f in frames], axis=0)
    total = float(w.sum())
    if total < 1e-6:
        return {"coverage": 0.0}
    h, wd = w.shape
    ys: NDArray[np.float64] = (np.arange(h, dtype=np.float64) + 0.5) / h
    xs: NDArray[np.float64] = (np.arange(wd, dtype=np.float64) + 0.5) / wd
    col, row = w.sum(axis=0) / total, w.sum(axis=1) / total
    cx, cy = float(col @ xs), float(row @ ys)
    sx, sy = float(np.sqrt(col @ (xs - cx) ** 2)), float(np.sqrt(row @ (ys - cy) ** 2))

    def bounds(marginal: NDArray[np.float64], at: NDArray[np.float64]) -> list[float]:
        cum = np.cumsum(marginal)
        return [round(float(at[np.searchsorted(cum, 0.05)]), 3), round(float(at[min(np.searchsorted(cum, 0.95),
                                                                                     at.size - 1)]), 3)]

    grid = [[round(float(w[r * h // 3:(r + 1) * h // 3, c * wd // 3:(c + 1) * wd // 3].sum()) / total, 3)
             for c in range(3)] for r in range(3)]
    name, (ax, ay) = min(ANCHORS.items(), key=lambda kv: (kv[1][0] - cx) ** 2 + (kv[1][1] - cy) ** 2)
    return {"coverage": round(total / w.size, 4), "centroid": [round(cx, 3), round(cy, 3)],
            "spread": [round(sx, 3), round(sy, 3)], "box": {"x": bounds(col, xs), "y": bounds(row, ys)},
            "grid": grid, "nearest": name, "off_by": round(float(np.hypot(cx - ax, cy - ay)), 3)}

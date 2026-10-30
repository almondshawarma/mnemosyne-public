"""An export's sound kit for DaVinci Resolve: the scene's cues as timeline markers, and its computed tones as WAVs.

`scripts/render.py --publish` calls this with the render's render.json. It writes, next to the video in exports/:

- `<slug>-markers.edl`: one marker per cue. In Resolve, Timeline > Import > Timeline Markers from EDL, onto the
  timeline holding the export at its start (01:00:00:00 by default, Resolve's own). Colours say what kind of moment it
  is (see KIND_COLORS), and names carry the strength, so a sound can be sized to its hit.
- `<slug>-tone-<name>.wav`: one mono stem per `self.tone(...)` track, 48 kHz 24-bit, as long as the video, so it lines
  up dropped at the timeline's start. The pitch is the scene's computed ratio times its base, glided frame to frame
  with a continuous phase (no clicks), and the loudness follows what's on screen.

A track made with `voice="buzz"` is an ambient bed instead (`synth_buzz`): a low, harmonic-rich hum with a beating
twin and a breath of high air, about −15 dBFS, swelling with the scene's level (in compton-light, the energy the
electron holds).

A tone's timbre is a starting point for Aman's sound: a warm fundamental with a quiet octave below, the octave and the
twelfth above (the fifth's partial, which reads as regal), a second voice a few cents sharp for shimmer, and a slight
tremolo for the buzz of held energy. It sits at −6 dBFS peak so there's headroom for reverb in Fairlight.

Pure functions; numpy and the standard library only.
"""

from __future__ import annotations

import math
import re
import wave
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_RATE = 48000
KIND_COLORS = {                      # Resolve's marker colours, by the kind of moment
    "hit": "ResolveColorRed", "absorb": "ResolveColorRed", "emit": "ResolveColorYellow",
    "move": "ResolveColorGreen", "reveal": "ResolveColorCyan", "section": "ResolveColorBlue",
    "caption": "ResolveColorCream", "title": "ResolveColorPurple", "credits": "ResolveColorPurple",
}
PARTIALS = ((0.5, 0.35), (1.0, 1.0), (2.0, 0.42), (3.0, 0.22), (4.0, 0.08))   # (multiple, weight)
DETUNE_CENTS, VOICE2 = 4.0, 0.25     # the shimmer voice: it beats with the first about 0.7 times a second at D4
TREMOLO_HZ, TREMOLO_DEPTH = 5.5, 0.06
PEAK = 0.5                           # −6 dBFS
SMOOTH_S = 0.02                      # loudness smoothing, so a step in loudness can't click


def timecode(seconds: float, fps: int, start_hours: int = 1) -> str:
    """HH:MM:SS:FF, non-drop, from `start_hours` (Resolve's timelines start at 01:00:00:00)."""
    frames = round(seconds * fps) + start_hours * 3600 * fps
    f = frames % fps
    s = frames // fps
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


GREEK = ("alpha", "beta", "gamma", "delta", "Delta", "epsilon", "theta", "lambda", "mu", "nu", "pi", "rho", "sigma",
         "tau", "phi", "varphi", "chi", "psi", "omega", "Omega", "hbar")


def plain(text: str) -> str:
    """A caption's TeX as plain ASCII for a marker: "$180^\\circ$, $\\Delta\\lambda$" -> "180 deg, Delta lambda".
    Greek letters keep their names, other commands go, and anything left that isn't ASCII is dropped (EDLs are
    ASCII), with '|' and line breaks too: EDL fields are split on '|'."""
    s = re.sub(r"\^\s*\{?\s*\\circ\s*\}?|°", " deg", text)
    s = re.sub(r"\\(" + "|".join(GREEK) + r")(?![A-Za-z])", lambda m: " " + m.group(1).replace("var", "") + " ", s)
    s = re.sub(r"\\[A-Za-z]+|[$\\{}^_]", "", s)
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[|\s]+", " ", s)
    return re.sub(r"\s+([,.;:)])", r"\1", re.sub(r"\(\s+", "(", s)).strip()


def marker_name(cue: dict[str, Any]) -> str:
    """What the marker says: the label (plain ASCII), and the strength when it's a hit worth sizing."""
    label = plain(str(cue.get("label", "")))
    kind = str(cue.get("kind", "hit"))
    if kind in ("hit", "absorb", "emit"):
        label = f"{kind} {float(cue.get('strength', 1.0)):.2f}" + (f" {label}" if label and label != kind else "")
    return label[:80] or kind


def marker_edl(title: str, cues: list[dict[str, Any]], fps: int = 60, start_hours: int = 1) -> str:
    """A CMX3600 EDL whose events are one-frame markers, the form Resolve exports and imports timeline markers in."""
    clean = plain(title)[:60]
    lines = [f"TITLE: {clean}", "FCM: NON-DROP FRAME", ""]
    for i, cue in enumerate(sorted(cues, key=lambda c: c["t"]), start=1):
        a = timecode(cue["t"], fps, start_hours)
        b = timecode(cue["t"] + 1 / fps, fps, start_hours)
        color = KIND_COLORS.get(str(cue.get("kind")), "ResolveColorBlue")
        lines.append(f"{i:03d}  001      V     C        {a} {b} {a} {b}  ")
        lines.append(f" |C:{color} |M:{marker_name(cue)} |D:1")
        lines.append("")
    return "\r\n".join(lines)


def _curves(samples: list[list[Any]], n: int, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """The ratio and loudness at every audio sample, from per-frame samples [t, ratio or None, amp]. Repeated times
    keep the last (a play's first frame samples twice). In silence the pitch holds its last value, so a fade-out
    doesn't glide to nowhere; before the first pitch it takes the first."""
    by_t: dict[float, tuple[float | None, float]] = {}
    for t, ratio, amp in samples:
        by_t[float(t)] = (ratio, float(amp) if ratio is not None else 0.0)
    times = sorted(by_t)
    if not times:
        return np.ones(n), np.zeros(n)
    ratios: list[float] = []
    known: float = next((r for r, _ in (by_t[t] for t in times) if r is not None), 1.0)
    for t in times:
        r = by_t[t][0]
        if r is not None:
            known = float(r)
        ratios.append(known)
    amps = [by_t[t][1] for t in times]
    grid = np.arange(n) / sr
    return np.interp(grid, times, ratios), np.interp(grid, times, amps, left=0.0, right=0.0)


def _smooth(x: np.ndarray, k: int) -> np.ndarray:
    """A centred moving average over k samples, from a running sum: O(n) where np.convolve is O(n·k), which made a
    quarter-second window over a 40 s stem take minutes. The ends average over what's there."""
    if k <= 1:
        return x
    c = np.concatenate([[0.0], np.cumsum(x)])
    i = np.arange(len(x))
    lo, hi = np.clip(i - k // 2, 0, len(x)), np.clip(i - k // 2 + k, 0, len(x))
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


def synth_tone(samples: list[list[Any]], base_hz: float, duration: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    """A mono float stem `duration` long: base_hz × the scene's ratio, phase-continuous, in the house timbre."""
    n = max(1, math.ceil(duration * sr))
    ratio, amp = _curves(samples, n, sr)
    k = max(1, int(SMOOTH_S * sr))
    amp = _smooth(amp, k)
    phase = 2 * np.pi * np.cumsum(base_hz * ratio) / sr
    sharp = 2 ** (DETUNE_CENTS / 1200)
    out = np.zeros(n)
    nyquist = sr / 2
    for mult, weight in PARTIALS:
        if base_hz * mult * float(ratio.max()) * sharp >= nyquist:
            continue
        out += weight * (np.sin(mult * phase) + VOICE2 * np.sin(mult * sharp * phase + 0.7))
    grid = np.arange(n) / sr
    out *= amp * (1 - TREMOLO_DEPTH * 0.5 * (1 - np.cos(2 * np.pi * TREMOLO_HZ * grid)))
    # One fixed scale, not each stem's own peak: loudness 1 is the same level in every stem and every render, so the
    # stems keep the scene's balance (the sweep's faint incoming ray stays half as loud as the scattered one).
    return _tail(out * (PEAK / (sum(w for _, w in PARTIALS) * (1 + VOICE2))), sr)


BUZZ_PEAK = 0.45                     # the bound; at full level it really peaks near −15 dBFS: a bed, never in front
BUZZ_HARMONICS, BUZZ_TOP_HZ = 16, 2500.0
TAIL_S = 0.5                         # every stem fades out over its last half second instead of stopping dead


def synth_buzz(samples: list[list[Any]], base_hz: float, duration: float, sr: int = SAMPLE_RATE,
               seed: int = 0) -> np.ndarray:
    """A faint bed of particle energy: a low hum rich in harmonics (base_hz × ratio, its overtones falling as
    1/k^1.1 up to BUZZ_TOP_HZ), a second hum 7 cents sharp that beats against it, and a breath of high air (noise
    band-passed to 1.5–7 kHz in the frequency domain) drifting on a slow swell. Loudness follows the scene's
    computed level, smoothed over a quarter second, since a bed should breathe, not twitch. `seed` keeps the noise
    the same on every render."""
    n = max(1, math.ceil(duration * sr))
    ratio, amp = _curves(samples, n, sr)
    k = max(1, int(0.25 * sr))
    amp = _smooth(amp, k)
    phase = 2 * np.pi * np.cumsum(base_hz * ratio) / sr
    sharp = 2 ** (7 / 1200)
    rng = np.random.default_rng(seed)
    hum = np.zeros(n)
    weights = 0.0
    for h in range(1, BUZZ_HARMONICS + 1):
        if base_hz * h * float(ratio.max()) * sharp >= BUZZ_TOP_HZ:
            break
        w = h ** -1.1
        offsets = rng.uniform(0, 2 * np.pi, 2)
        hum += w * (np.sin(h * phase + offsets[0]) + 0.4 * np.sin(h * sharp * phase + offsets[1]))
        weights += w * 1.4
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    band = np.clip((freqs - 1500) / 500, 0, 1) * np.clip((7000 - freqs) / 2000, 0, 1)
    air = np.fft.irfft(spectrum * band, n)
    air /= max(float(np.std(air)), 1e-12)
    grid = np.arange(n) / sr
    swell = 1 - 0.3 * 0.5 * (1 - np.cos(2 * np.pi * 0.07 * grid))
    drift = 1 - 0.5 * 0.5 * (1 - np.cos(2 * np.pi * 0.17 * grid + 1.3))
    out = amp * swell * (hum / max(weights, 1e-12) + 0.06 * drift * air)
    return _tail(out * (BUZZ_PEAK / 1.25), sr)


def _tail(x: np.ndarray, sr: int) -> np.ndarray:
    """Fade the last TAIL_S out, so a stem never ends on a click."""
    m = min(len(x), int(TAIL_S * sr))
    if m > 1:
        x = x.copy()
        x[-m:] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, m)))
    return x


def write_wav(path: Path, x: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    """Mono 24-bit PCM."""
    pcm = (np.clip(x, -1.0, 1.0) * 8388607).astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def write_kit(meta: dict[str, Any], folder: Path, slug: str, fps: int = 60) -> list[Path]:
    """The markers and the stems for one render, into `folder`; returns what it wrote."""
    written: list[Path] = []
    cues = meta.get("cues") or []
    if cues:
        edl = folder / f"{slug}-markers.edl"
        edl.write_text(marker_edl(str(meta.get("title") or slug), cues, fps), encoding="utf-8", newline="")
        written.append(edl)
    duration = float(meta.get("duration") or 0)
    for name, track in (meta.get("tones") or {}).items():
        if duration <= 0 or not track.get("samples"):
            continue
        wav = folder / f"{slug}-tone-{name}.wav"
        if track.get("voice") == "buzz":
            seed = sum(name.encode())
            write_wav(wav, synth_buzz(track["samples"], float(track["base_hz"]), duration, seed=seed))
        else:
            write_wav(wav, synth_tone(track["samples"], float(track["base_hz"]), duration))
        written.append(wav)
    return written

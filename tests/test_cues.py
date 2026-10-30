"""The sound kit: job.py keeps only well-formed, capped cues and tones from a scene's output, the EDL is one marker
per cue on the export's frames, and a tone stem is the computed pitch, glided without clicks."""

import importlib.util
import json
import wave
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cues = load("cues", ROOT / "scripts" / "cues.py")
job = load("manim_job", ROOT / "services" / "manim" / "job.py")


def tone_lines(name: str, rows: list[tuple[float, float | None, float]], base: float = 220.0) -> str:
    lines = [f"MN-TONE-TRACK {json.dumps({'name': name, 'base_hz': base})}"]
    lines += [f"MN-TONE {json.dumps([name, t, r, a])}" for t, r, a in rows]
    return "\n".join(lines) + "\n"


# ---------- job.py: what survives from the scene's output ----------

def test_job_keeps_cues_sorted_and_ignores_everything_else():
    lines = [
        "Manim Community v0.21.0",
        'MN-CUE {"t": 2.5, "kind": "emit", "label": "emit", "strength": 0.35}',
        'MN-CUE {"t": 1.0, "kind": "absorb", "label": "absorb", "strength": 0.6}',
        "MN-LAYOUT [x] text overlaps text: a x b",
        "MN-CUE not json",
        'MN-CUE {"t": NaN, "kind": "hit"}',
        'MN-CUE {"t": -3, "kind": "hit"}',
        'MN-CUE {"t": true, "kind": "hit"}',
    ]
    log = "\n".join(lines)
    got = job.sound(log)
    assert [c["t"] for c in got["cues"]] == [1.0, 2.5]
    assert got["cues"][0] == {"t": 1.0, "kind": "absorb", "label": "absorb", "strength": 0.6}
    assert got["tones"] == {}


def test_job_caps_and_trims_what_a_chat_scene_could_flood():
    log = "\n".join(f'MN-CUE {json.dumps({"t": i, "label": "x" * 500})}' for i in range(job.MAX_CUES + 50))
    got = job.sound(log)["cues"]
    assert len(got) == job.MAX_CUES
    assert all(len(c["label"]) == 160 for c in got)


def test_job_keeps_tone_samples_only_for_declared_tracks_with_sane_values():
    log = tone_lines("photon", [(0.0, 1.0, 1.0), (0.1, None, 0.0), (0.2, -1.0, 1.0), (0.3, 0.5, 2.0)])
    log += 'MN-TONE ["ghost", 0.0, 1.0, 1.0]\n'
    log += 'MN-TONE-TRACK {"name": "../evil", "base_hz": 220}\n'
    got = job.sound(log)["tones"]
    assert list(got) == ["photon"]
    assert got["photon"]["base_hz"] == 220.0
    assert got["photon"]["samples"] == [[0.0, 1.0, 1.0], [0.1, None, 0.0]]


# ---------- the marker EDL ----------

def test_timecode_counts_frames_from_resolves_hour_one():
    assert cues.timecode(0, 60) == "01:00:00:00"
    assert cues.timecode(1.5, 60) == "01:00:01:30"
    assert cues.timecode(61.25, 30) == "01:01:01:08"
    assert cues.timecode(2.0, 60, start_hours=0) == "00:00:02:00"


def test_the_edl_is_one_coloured_marker_per_cue_in_time_order():
    edl = cues.marker_edl("compton|light", [
        {"t": 4.55, "kind": "emit", "label": "emit", "strength": 0.35},
        {"t": 3.25, "kind": "absorb", "label": "absorb", "strength": 0.6},
        {"t": 0.0, "kind": "section", "label": "a photon arrives", "strength": 1.0},
        {"t": 2.0, "kind": "caption", "label": "A photon | meets\na free electron", "strength": 1.0},
    ], fps=60)
    lines = edl.split("\r\n")
    assert lines[0] == "TITLE: compton light" and lines[1] == "FCM: NON-DROP FRAME"
    events = [l for l in lines if l[:3].isdigit()]
    markers = [l for l in lines if l.startswith(" |C:")]
    assert len(events) == len(markers) == 4
    assert "01:00:00:00 01:00:00:01 01:00:00:00 01:00:00:01" in events[0]
    assert "01:00:03:15" in events[2]                          # 3.25 s at 60 fps
    assert markers[0] == " |C:ResolveColorBlue |M:a photon arrives |D:1"
    assert markers[1] == " |C:ResolveColorCream |M:A photon meets a free electron |D:1"
    assert markers[2] == " |C:ResolveColorRed |M:absorb 0.60 |D:1"
    assert markers[3] == " |C:ResolveColorYellow |M:emit 0.35 |D:1"


def test_caption_tex_becomes_a_plain_ascii_marker_name():
    caption = r"At $180^\circ$, $\Delta\lambda=2h/m_ec=4.85$ pm: backscatter loses the most."
    assert cues.plain(caption) == "At 180 deg, Delta lambda =2h/mec=4.85 pm: backscatter loses the most."
    caption = r"A photon ($\lambda_0$) meets a free electron at rest."
    assert cues.plain(caption) == "A photon (lambda 0) meets a free electron at rest."
    assert cues.plain("sweep to 180°") == "sweep to 180 deg"
    assert cues.plain(r"recoils at $\varphi$, at $\beta=0.66$ | x") == "recoils at phi, at beta =0.66 x"
    assert cues.plain(r"$\frac{h}{m_e c}$ ok").isascii()


# ---------- the tone stems ----------

def fundamental(x: np.ndarray, sr: int, lo: float, hi: float) -> float:
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    band = (freqs > lo) & (freqs < hi)
    return float(freqs[band][np.argmax(spectrum[band])])


def test_a_tone_sounds_at_base_times_the_computed_ratio():
    sr = 48000
    # the Compton fifth: at 60 degrees with lambda0 = lambda_C, lambda' = 1.5 lambda0, so E'/E0 = 2/3
    ratio = 1 / (1 + (1 - np.cos(np.radians(60))))
    assert ratio == pytest.approx(2 / 3)
    rows = [[t, ratio, 1.0] for t in np.arange(0, 2.01, 1 / 60)]
    x = cues.synth_tone(rows, 293.66, 2.0, sr)
    assert len(x) == 2 * sr
    f = fundamental(x[sr // 2: 3 * sr // 2], sr, 150, 240)
    assert f == pytest.approx(293.66 * 2 / 3, abs=1.5)        # G3: a perfect fifth below D4


def test_silence_is_silent_and_a_held_pitch_does_not_glide_while_fading():
    sr = 48000
    rows = [[0.0, None, 0.0], [0.5, None, 0.0], [0.52, 1.0, 1.0], [1.0, 1.0, 1.0], [1.02, None, 0.0], [2.0, None, 0.0]]
    x = cues.synth_tone(rows, 220.0, 2.0, sr)
    assert np.abs(x[: int(0.45 * sr)]).max() == 0
    assert np.abs(x[int(1.2 * sr):]).max() < 1e-6
    assert np.abs(x[int(0.6 * sr): int(0.95 * sr)]).max() > 0.15


def test_loudness_means_the_same_level_in_every_stem():
    sr = 48000
    full = cues.synth_tone([[0.0, 1.0, 1.0], [1.0, 1.0, 1.0]], 220.0, 1.0, sr)
    half = cues.synth_tone([[0.0, 1.0, 0.5], [1.0, 1.0, 0.5]], 220.0, 1.0, sr)
    mid = slice(sr // 4, 3 * sr // 4)
    assert np.sqrt(np.mean(half[mid] ** 2)) == pytest.approx(0.5 * np.sqrt(np.mean(full[mid] ** 2)), rel=0.02)
    assert np.abs(full).max() <= cues.PEAK


def test_a_glide_has_no_clicks():
    sr = 48000
    rows = [[t, 1.0 - 0.6 * t / 3, 1.0] for t in np.arange(0, 3.01, 1 / 30)]      # a sweep down, like theta to 180
    x = cues.synth_tone(rows, 293.66, 3.0, sr)
    steps = np.abs(np.diff(x))
    # the largest step is what the highest partial's own slope allows, never a phase jump
    top = 293.66 * 4.0 * 2 ** (cues.DETUNE_CENTS / 1200)
    assert steps.max() < 2 * np.pi * top / sr * 1.5


def test_the_kit_writes_markers_and_24_bit_stems(tmp_path: Path):
    meta = {"title": "Compton", "duration": 1.0,
            "cues": [{"t": 0.25, "kind": "hit", "label": "hit", "strength": 1.0}],
            "tones": {"photon": {"base_hz": 220.0, "samples": [[0.0, 1.0, 1.0], [1.0, 1.0, 1.0]]}}}
    written = cues.write_kit(meta, tmp_path, "compton-light", fps=60)
    assert [p.name for p in written] == ["compton-light-markers.edl", "compton-light-tone-photon.wav"]
    with wave.open(str(written[1])) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 3, 48000, 48000)


# ---------- the ambient buzz ----------

def test_job_keeps_a_tracks_voice_and_refuses_unknown_ones():
    log = 'MN-TONE-TRACK {"name": "ambience", "base_hz": 73.42, "voice": "buzz"}\n'
    log += 'MN-TONE-TRACK {"name": "odd", "base_hz": 73.42, "voice": "shell"}\n'
    log += 'MN-TONE-TRACK {"name": "photon", "base_hz": 293.66}\n'
    got = job.sound(log)["tones"]
    assert got["ambience"]["voice"] == "buzz" and got["photon"]["voice"] == "tone" and "odd" not in got


def test_the_buzz_is_a_faint_low_hum_that_follows_its_level():
    sr = 48000
    rows = [[t, 1.0, 0.35 if t < 2 else 1.0] for t in np.arange(0, 4.01, 1 / 30)]
    x = cues.synth_buzz(rows, 73.42, 4.0, sr, seed=1)
    assert np.abs(x).max() <= cues.BUZZ_PEAK
    assert fundamental(x[sr // 2: 3 * sr // 2], sr, 50, 100) == pytest.approx(73.42, abs=1.5)   # D2
    quiet, loud = x[int(0.5 * sr): int(1.5 * sr)], x[int(2.5 * sr): int(3.4 * sr)]
    ratio = np.sqrt(np.mean(loud ** 2)) / np.sqrt(np.mean(quiet ** 2))
    assert ratio == pytest.approx(1 / 0.35, rel=0.25)       # it swells with the level (the slow swell blurs it a bit)
    again = cues.synth_buzz(rows, 73.42, 4.0, sr, seed=1)
    assert np.array_equal(x, again)                        # the same noise every render


def test_every_stem_fades_out_instead_of_stopping_dead():
    sr = 48000
    rows = [[0.0, 1.0, 1.0], [2.0, 1.0, 1.0]]
    for x in (cues.synth_tone(rows, 220.0, 2.0, sr), cues.synth_buzz(rows, 73.42, 2.0, sr)):
        assert np.abs(x[-50:]).max() < 1e-3
        assert np.abs(x[int(1.0 * sr): int(1.4 * sr)]).max() > 0.05


def test_the_kit_writes_a_buzz_track_with_the_buzz_voice(tmp_path: Path):
    meta = {"title": "Compton", "duration": 1.0, "cues": [],
            "tones": {"ambience": {"base_hz": 73.42, "voice": "buzz", "samples": [[0.0, 1.0, 1.0], [1.0, 1.0, 1.0]]}}}
    (wav,) = cues.write_kit(meta, tmp_path, "c", fps=60)
    with wave.open(str(wav)) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.uint8).reshape(-1, 3)
    peak = int(np.abs(raw[:, 2].astype(np.int8)).max())        # the top byte: a buzz stays well under full scale
    assert 0 < peak < 64

"""Scene tests need manim, which isn't one of mnemosyne's dependencies:

    uv run --with manim==0.21.0 pytest scenes/tests

Each test renders a tiny scene with dry_run (no files) at a small size, so the checks run on real cameras."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("manim")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # scenes/, where mn_style.py lives

from manim import tempconfig


@pytest.fixture(autouse=True)
def small_dry_run() -> Iterator[None]:
    with tempconfig({"dry_run": True, "pixel_width": 320, "pixel_height": 180, "frame_rate": 5, "verbosity": "ERROR",
                     "progress_bar": "none"}):
        yield

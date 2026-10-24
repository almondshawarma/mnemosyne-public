#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright"]
# ///
"""Check a built page renders: MathJax errors, raw $ or TeX leaking into the text, sideways overflow, console errors.
Optionally takes screenshots down the page.

    uv run shot.py PHYS221_Exam2_Cram_Sheet.html --widths 375,1300 --shots 0,0.3,0.6 --prefix shots/cram [--dark]

Exits 1 if any check fails at any width. Run it at phone width (360-400) on every page before delivering it.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright
from topdf import launch

PROBE = r"""() => ({
  merr: [...document.querySelectorAll('mjx-merror')].map(e => e.getAttribute('data-mjx-error') || e.textContent).slice(0, 20),
  wide: document.querySelectorAll('mjx-container.mjx-wide').length,
  sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
  raw: [...document.querySelectorAll('p,li,td,th')].filter(e => /\$|\\\(|\\frac/.test(e.innerText)).map(e => e.innerText.slice(0, 100)).slice(0, 10),
})"""


async def check(path: Path, width: int, dark: bool, shots: list[float], prefix: str | None, channel: str | None) -> bool:
    async with async_playwright() as p:
        b = await launch(p, channel)
        pg = await b.new_page(viewport={"width": width, "height": 1000}, color_scheme="dark" if dark else "light")
        console: list[str] = []
        pg.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        await pg.goto(path.resolve().as_uri())
        await pg.wait_for_function("window.MathJax && MathJax.startup && MathJax.startup.promise", timeout=60000)
        await pg.evaluate("MathJax.startup.promise")
        await pg.wait_for_timeout(500)
        info = await pg.evaluate(PROBE)
        ok = not info["merr"] and not info["raw"] and info["sw"] <= info["cw"] and not console
        print(f"{path.name} @ {width}px{' dark' if dark else ''}: {'ok' if ok else 'FAIL'}", info,
              "console errors:", console[:5])
        for i, y in enumerate(shots):
            await pg.evaluate(f"window.scrollTo(0, {y} * document.body.scrollHeight)")
            await pg.wait_for_timeout(150)
            await pg.screenshot(path=f"{prefix or path.stem}_{width}_{i}.png")
        await b.close()
        return ok


async def main(a: argparse.Namespace) -> int:
    widths = [int(w) for w in a.widths.split(",")]
    shots = [float(s) for s in a.shots.split(",")] if a.shots else []
    results = [await check(a.path, w, a.dark, shots, a.prefix, a.channel) for w in widths]
    return 0 if all(results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="the built .html")
    ap.add_argument("--widths", default="375,1300", help="viewport widths to check, comma-separated")
    ap.add_argument("--shots", default="", help="screenshot positions as fractions of the page, e.g. 0,0.5")
    ap.add_argument("--prefix", help="screenshot path prefix (default: the page's name)")
    ap.add_argument("--dark", action="store_true", help="dark color scheme")
    ap.add_argument("--channel", help="browser channel: msedge, chrome")
    sys.exit(asyncio.run(main(ap.parse_args())))

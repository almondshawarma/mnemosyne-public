#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright"]
# ///
"""Print a built page to a Letter PDF for the iPad: a 1.45in right margin to annotate in, each top-level section on
a new page, page numbers in the footer, and a PDF outline.

    uv run topdf.py PHYS221_Exam2_Review_Guide.html PHYS221_Exam2_Review_Guide.pdf --footer "PHYS 221 · Exam 2 Review Guide"

Uses Playwright's Chromium when it is installed, else the machine's Edge or Chrome (--channel to pick one).
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import Browser, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

CSS = """
@page { size: Letter; margin: 0.6in 1.45in 0.65in 0.6in; }
html, body { background: #fff !important; }
body { max-width: none !important; margin: 0 !important; padding: 0 !important; font-size: 11.2pt !important; line-height: 1.5 !important; }
body::after, body::before { display: none !important; }
nav#TOC { position: static !important; width: auto !important; height: auto !important; border: 1px solid #DDE2E8 !important; break-after: page; font-size: 10pt !important; }
h1:not(.title) { break-before: page; margin-top: 0 !important; }
nav#TOC + h1, header + h1 { break-before: avoid; }
h1, h2, h3 { break-after: avoid; }
figure, table, .box.key, .box.trap, .box.intuition, .box.asked { break-inside: avoid; }
.box.example, .box.derive { break-inside: auto; }
.box { box-shadow: none !important; }
.quizbar { display: none !important; }
p, li { orphans: 3; widows: 3; }
mjx-container[display="true"] { break-inside: avoid; }
"""

FOOTER = (
    '<div style="font:9px sans-serif;color:#5A6573;width:100%;padding:0 0.6in;display:flex;'
    'justify-content:space-between"><span>{text}</span>'
    '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
)


async def launch(p: Playwright, channel: str | None) -> Browser:
    """Playwright's own Chromium, else an installed Edge or Chrome."""
    if channel:
        return await p.chromium.launch(channel=channel)
    for ch in (None, "msedge", "chrome"):
        try:
            return await p.chromium.launch(channel=ch) if ch else await p.chromium.launch()
        except PlaywrightError:
            continue
    raise SystemExit("no Chromium: run `uv run --with playwright playwright install chromium`, or install Edge/Chrome")


async def main(src: Path, out: Path, footer: str | None, channel: str | None) -> None:
    async with async_playwright() as p:
        b = await launch(p, channel)
        pg = await b.new_page(viewport={"width": 620, "height": 1000}, color_scheme="light")
        await pg.goto(src.resolve().as_uri())
        await pg.wait_for_function("window.MathJax && MathJax.startup && MathJax.startup.promise", timeout=60000)
        await pg.evaluate("MathJax.startup.promise")
        await pg.emulate_media(media="print")
        await pg.add_style_tag(content=CSS)
        await pg.wait_for_timeout(300)
        await pg.evaluate("fitMath()")
        await pg.wait_for_timeout(300)
        errs = await pg.evaluate("document.querySelectorAll('mjx-merror').length")
        n = await pg.evaluate("document.querySelectorAll('mjx-container').length")
        print("mathjax errors:", errs, "containers:", n)
        text = footer or await pg.title()
        kw = {
            "path": str(out), "format": "Letter", "print_background": True, "prefer_css_page_size": True,
            "display_header_footer": True, "header_template": "<span></span>",
            "footer_template": FOOTER.format(text=text),
        }
        try:
            await pg.pdf(outline=True, tagged=True, **kw)
        except TypeError:  # Playwright older than 1.42 has no outline
            await pg.pdf(**kw)
        await b.close()
        print(out, out.stat().st_size)
        if errs:
            raise SystemExit(f"{errs} MathJax errors")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="the built .html")
    ap.add_argument("out", type=Path, help="the .pdf to write")
    ap.add_argument("--footer", help="footer text (default: the page title)")
    ap.add_argument("--channel", help="browser channel: msedge, chrome (default: Playwright's Chromium, then those)")
    a = ap.parse_args()
    asyncio.run(main(a.src, a.out, a.footer, a.channel))

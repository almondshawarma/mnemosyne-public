#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pypandoc_binary"]
# ///
"""Build an exam's HTML set from pandoc markdown: self-contained pages that open on his phone.

    uv run build.py <source-dir> <out-dir> --prefix PHYS221_Exam2 [guide solutions cram formula]

The source directory holds the markdown (guide.md, solutions.md, cram.md, formula.md: any that exist are built),
examples.md, any drill files, and figs/. Includes, expanded before pandoc runs:

- `{{EX:id}}`: a worked example, defined once in examples.md between `<!-- EX:id -->` and `<!-- END -->`, so the
  guide and the solutions print the same block;
- `{{FILE:x}}`: the whole of another file in the source directory (drills).

Each page is self-contained: pandoc's default CSS and every MathJax <script src> it adds (CDN, polyfill.io, or a
local /usr/share copy) are stripped, and the build fails if any remain. The fonts, style, and MathJax are inlined
and the figures base64-embedded. The cram sheet also gets an Artifact variant (no html/head/body tags).

MathJax 3 (tex-svg-full) is fetched from npm once, checked against the registry's sha512, and cached; pass
--mathjax FILE to use a local copy offline. pandoc comes from PATH, or from the pypandoc_binary wheel.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MATHJAX_VERSION = "3.2.2"
MATHJAX_TARBALL = f"https://registry.npmjs.org/mathjax/-/mathjax-{MATHJAX_VERSION}.tgz"
MATHJAX_SHA512 = "Bt+SSVU8eBG27zChVewOicYs7Xsdt40qm4+UpHyX7k0/O9NliPc+x77k1/FEsPsjKPZGJvtRZM1vO+geW0OhGw=="
MATHJAX_MEMBER = "package/es5/tex-svg-full.js"
HEAD_MARK = "<!-- html-set head -->"

# name: (source, output suffix, numbered sections, Artifact variant)
DOCS = {
    "guide": ("guide.md", "Review_Guide", True, False),
    "solutions": ("solutions.md", "Solutions", False, False),
    "cram": ("cram.md", "Cram_Sheet", False, True),
    "formula": ("formula.md", "Formula_Sheet_Explained", False, False),
}

# fitMath() shrinks display and inline math that would overflow its box, so nothing scrolls sideways at 360-400px.
MATHJAX_CONFIG = r"""window.MathJax={tex:{inlineMath:[['\\(','\\)']],displayMath:[['\\[','\\]']],processEscapes:true,macros:{dd:'\\mathrm{d}'}},svg:{fontCache:'global'},options:{enableMenu:false},startup:{pageReady(){return MathJax.startup.defaultPageReady().then(fitMath)}}};
function fitMath(){document.querySelectorAll('mjx-container[display="true"]').forEach(function(e){e.style.fontSize='';var r=e.clientWidth/e.scrollWidth;if(r<0.995){var f=Math.max(r*0.99,0.68);e.style.setProperty('font-size',(103*f).toFixed(1)+'%','important');}});
document.querySelectorAll('mjx-container:not([display="true"])').forEach(function(e){e.style.fontSize='';e.classList.remove('mjx-wide');var b=e.parentElement;while(b&&getComputedStyle(b).display.indexOf('inline')===0)b=b.parentElement;if(!b)return;var cs=getComputedStyle(b);var avail=b.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight);var w=e.getBoundingClientRect().width;if(w>avail){var f=Math.max(avail/w*0.98,0.72);e.style.setProperty('font-size',(103*f).toFixed(1)+'%','important');if(e.getBoundingClientRect().width>avail)e.classList.add('mjx-wide');}});}
var _ft;window.addEventListener('resize',function(){clearTimeout(_ft);_ft=setTimeout(fitMath,150)});"""

# Quiz mode: the checkbox #quiz blurs every .card's .kernel until the card is tapped (or Enter/Space on it).
QUIZ = r"""document.addEventListener('DOMContentLoaded',function(){var q=document.getElementById('quiz');if(!q)return;
q.addEventListener('change',function(){document.body.classList.toggle('quiz',q.checked);document.querySelectorAll('.card.open').forEach(function(c){c.classList.remove('open')})});
document.querySelectorAll('.card').forEach(function(c){c.setAttribute('tabindex','0');c.addEventListener('click',function(){if(document.body.classList.contains('quiz'))c.classList.toggle('open')});c.addEventListener('keydown',function(e){if((e.key==='Enter'||e.key===' ')&&document.body.classList.contains('quiz')){e.preventDefault();c.classList.toggle('open')}})});});"""

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".webp": "image/webp"}


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8", newline="\n")


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "mnemosyne-exam"


def mathjax(local: str | None) -> str:
    """The MathJax bundle's source: a local file, the cache, or npm (verified against the pinned sha512)."""
    if local:
        return read(Path(local))
    cached = cache_dir() / f"mathjax-{MATHJAX_VERSION}-tex-svg-full.js"
    if not cached.exists():
        print(f"fetching {MATHJAX_TARBALL}", file=sys.stderr)
        with urllib.request.urlopen(MATHJAX_TARBALL, timeout=120) as r:
            tgz = r.read()
        got = base64.b64encode(hashlib.sha512(tgz).digest()).decode()
        if got != MATHJAX_SHA512:
            raise SystemExit(f"mathjax tarball sha512 mismatch: {got}")
        with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as t:
            member = t.extractfile(MATHJAX_MEMBER)
            if member is None:
                raise SystemExit(f"{MATHJAX_MEMBER} missing from the tarball")
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(member.read())
    return read(cached)


def pandoc_bin(given: str | None) -> str:
    if given:
        return given
    if found := shutil.which("pandoc"):
        return found
    try:
        import pypandoc  # type: ignore[import-not-found]
    except ImportError:
        raise SystemExit("pandoc not found: put it on PATH, pass --pandoc, or run with `uv run build.py`") from None
    return pypandoc.get_pandoc_path()


def header(mj: str) -> str:
    fonts, css = read(HERE / "fonts.css"), read(HERE / "style.css")
    return (
        f'{HEAD_MARK}\n<meta name="claude:viewport-fit-sides" content="cover">\n<style>\n{fonts}\n{css}\n</style>\n'
        f"<script>{MATHJAX_CONFIG}</script>\n<script>{QUIZ}</script>\n<script>{mj}</script>\n"
    )


def examples(src: Path) -> dict[str, str]:
    f = src / "examples.md"
    if not f.exists():
        return {}
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"<!-- EX:(\S+) -->\n(.*?)<!-- END -->", read(f), re.DOTALL)}


def expand(md: Path, src: Path, ex: dict[str, str]) -> str:
    def one(m: re.Match[str]) -> str:
        if m.group(1) not in ex:
            raise SystemExit(f"{md.name}: missing example {m.group(1)}")
        return ex[m.group(1)]

    text = re.sub(r"\{\{FILE:(\S+?)\}\}", lambda m: read(src / m.group(1)), read(md))
    return re.sub(r"\{\{EX:(\S+?)\}\}", one, text)


SCRIPT_SRC = re.compile(r'<script\b[^>]*\bsrc="[^"]*"[^>]*>\s*</script>\s*')


def clean(h: str) -> str:
    """Drop pandoc's own CSS and every external <script>, and fail if any survive."""
    head_end = h.index("</head>")
    mark = h.index(HEAD_MARK)
    assert mark < head_end, "header include not in <head>"
    before = re.sub(r"<style\b[^>]*>.*?</style>\s*", "", h[:mark], flags=re.DOTALL)  # pandoc's default stylesheet
    h = before + h[mark:]
    h = SCRIPT_SRC.sub("", h)  # cdn.jsdelivr.net/npm/mathjax, polyfill.io, /usr/share/javascript/mathjax
    for bad in ("polyfill.io", "cdn.jsdelivr.net/npm/mathjax", "/usr/share/javascript/mathjax"):
        assert bad not in h, f"{bad} still in the page"
    assert not SCRIPT_SRC.search(h), "an external <script src> is still in the page"
    assert "<style" not in h[: h.index(HEAD_MARK)], "pandoc's default CSS is still in the page"
    return h


def embed(h: str, src: Path) -> str:
    def one(m: re.Match[str]) -> str:
        p = src / m.group(1)
        mime = MIME.get(p.suffix.lower())
        if mime is None:
            raise SystemExit(f"unknown figure type: {p}")
        return f'src="data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"'

    h = re.sub(r'src="((?!data:|https?:)[^"]+\.(?:png|jpe?g|svg|webp))"', one, h)
    assert not re.search(r'<img\b[^>]*src="(?!data:)', h), "a figure was not embedded"
    return h


def artifact(h: str) -> str:
    """The page without html/head/body: what the Artifact tool publishes."""
    m = re.search(r"<title>(.*?)</title>", h, re.DOTALL)
    title = m.group(1) if m else ""
    head = h[h.index("<head>") + 6 : h.index("</head>")]
    head = re.sub(r'<meta charset[^>]*>|<meta name="viewport"[^>]*>|<meta name="generator"[^>]*>', "", head)
    head = re.sub(r"<title>.*?</title>", "", head, flags=re.DOTALL)
    body = h[h.index("<body>") + 6 : h.rindex("</body>")]
    return f"<title>{title}</title>\n{head}{body}"


def build(name: str, src: Path, out: Path, prefix: str, pandoc: str, head: Path, ex: dict[str, str]) -> None:
    md, suffix, numbered, art = DOCS[name]
    dest = out / f"{prefix}_{suffix}.html"
    with tempfile.TemporaryDirectory() as tmp:
        expanded = Path(tmp) / md
        write(expanded, expand(src / md, src, ex))
        args = [pandoc, str(expanded), "-f", "markdown+fenced_divs+bracketed_spans", "-t", "html5", "--standalone",
                "--toc", "--toc-depth=2", "--mathjax", "-H", str(head), "-o", str(dest)]
        if numbered:
            args.append("--number-sections")
        subprocess.run(args, check=True)
    h = embed(clean(read(dest)), src)
    write(dest, h)
    print(dest, dest.stat().st_size)
    if art:
        a = out / f"{prefix}_{suffix}_artifact.html"
        write(a, artifact(h))
        print(a, a.stat().st_size)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="source directory (the markdown, examples.md, figs/)")
    ap.add_argument("out", type=Path, help="output directory")
    ap.add_argument("docs", nargs="*", help=f"which to build, of {', '.join(DOCS)} (default: every source present)")
    ap.add_argument("--prefix", required=True, help="file name prefix, e.g. PHYS221_Exam2")
    ap.add_argument("--mathjax", help="a local MathJax 3 tex-svg-full.js (default: fetched from npm and cached)")
    ap.add_argument("--pandoc", help="the pandoc binary (default: PATH, then pypandoc_binary)")
    a = ap.parse_args()
    src, out = a.src.resolve(), a.out.resolve()
    if unknown := [n for n in a.docs if n not in DOCS]:
        raise SystemExit(f"unknown document {unknown}: choose from {list(DOCS)}")
    docs = a.docs or [n for n, (md, *_) in DOCS.items() if (src / md).exists()]
    missing = [DOCS[n][0] for n in docs if not (src / DOCS[n][0]).exists()]
    if missing or not docs:
        raise SystemExit(f"nothing to build in {src}: missing {missing or [d[0] for d in DOCS.values()]}")
    out.mkdir(parents=True, exist_ok=True)
    pandoc, ex = pandoc_bin(a.pandoc), examples(src)
    with tempfile.TemporaryDirectory() as tmp:
        head = Path(tmp) / "header.html"
        write(head, header(mathjax(a.mathjax)))
        for name in docs:
            build(name, src, out, a.prefix, pandoc, head, ex)


if __name__ == "__main__":
    main()

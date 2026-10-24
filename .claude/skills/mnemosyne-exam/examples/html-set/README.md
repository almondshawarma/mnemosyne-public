# The HTML exam set

The tooling for SKILL.md §5b: pandoc markdown → self-contained HTML that opens on his phone (inline fonts, inline
MathJax SVG, base64 figures), plus Letter PDFs for the iPad. `phys221-exam2/` is the reference set
(`../phys221-exam2.md`).

| File | What it does |
|---|---|
| `build.py` | Expands includes, runs pandoc, strips pandoc's default CSS and every MathJax/polyfill `<script src>` (the build fails if any remain), inlines fonts, style and MathJax, embeds figures, writes the cram sheet's Artifact variant. Holds `fitMath()`, which shrinks wide display and inline math so nothing scrolls sideways at 360–400px. |
| `topdf.py` | Letter PDF: 1.45in right margin to annotate in, each `#` section on a new page, page numbers, PDF outline. |
| `shot.py` | Render check: MathJax errors, leaked `$`/TeX, sideways overflow, console errors, at each width. Exits 1 on failure. Optional screenshots. |
| `style.css` | The design system: tokens, light and dark, the box types, quiz-mode cards. Same system as the Exam 1 set. |
| `fonts.css` | Source Serif 4 and IBM Plex Sans as data URIs (both SIL OFL). |

## A source directory

```
guide.md        Review Guide        (numbered sections)
solutions.md    Solutions
cram.md         Cram Sheet          (+ <prefix>_Cram_Sheet_artifact.html, no html/head/body, for the Artifact tool)
formula.md      Formula Sheet Explained
examples.md     every worked example, once:  <!-- EX:id --> … <!-- END -->
drills.md       anything pulled in with {{FILE:drills.md}}
figs/           figures referenced as figs/x.png (any of png, jpg, svg, webp)
figs.py         optional: draws figs/ (house style: ink #1C2430, hot #B5361B, cold #2B6CB0, despined, white)
```

Any of the four documents that exist get built. `{{EX:id}}` drops in an example block, so the guide (chapter order)
and the solutions (lecture order) print the same text. `{{FILE:x}}` drops in a whole file.

Boxes are pandoc fenced divs: `::: {.box .intuition label="Intuition"}`, and likewise `.derive`, `.trap`, `.key`,
`.asked` ("You asked"), and `.example` (add `.star` for the professor's priority list). A quiz card is
`:::: {.box .example .card label="…"}` with its answer in a nested `::: kernel` div. The cram sheet's quiz switch is a
`::: quizbar` holding `<input type="checkbox" id="quiz">`.

## Build

```bash
uv run build.py <source-dir> <out-dir> --prefix PHYS221_Exam2
uv run shot.py <out-dir>/PHYS221_Exam2_Cram_Sheet.html --widths 375,1300
uv run topdf.py <out-dir>/PHYS221_Exam2_Review_Guide.html <out-dir>/PHYS221_Exam2_Review_Guide.pdf --footer "PHYS 221 · Exam 2 Review Guide"
uv run topdf.py <out-dir>/PHYS221_Exam2_Cram_Sheet.html <out-dir>/PHYS221_Exam2_Cram_Sheet.pdf --footer "PHYS 221 · Exam 2 Cram Sheet"
```

The scripts are PEP 723, so `uv run` brings their dependencies: pandoc from the `pypandoc_binary` wheel when it isn't on
PATH, and Playwright, which uses its own Chromium if installed, else the machine's Edge or Chrome.

**MathJax** is not vendored. `build.py` fetches `mathjax@3.2.2` from the npm registry once, checks the tarball
against the sha512 npm publishes (pinned in the script), and caches `es5/tex-svg-full.js` (2.2 MB) in
`~/.cache/mnemosyne-exam/`. This keeps 2.2 MB of minified third-party code out of the repo and the plugin, and the
pin makes builds reproducible. Offline, pass `--mathjax <file>`.

## Pandoc gotchas

- `$v = 343 $ m/s` is not math: a closing `$` can't follow a space. Put the unit inside: `$v = 343\ \text{m/s}$`.
- A pipe table's column widths come from the dash counts in its separator row (`|--|------|----|`); set them
  on purpose for wide tables.
- Display math is `$$…$$` on its own lines. Inline math holds no blank lines.

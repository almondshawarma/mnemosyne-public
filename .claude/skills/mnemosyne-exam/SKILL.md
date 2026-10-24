---
name: mnemosyne-exam
description: Prepare Aman for an exam from everything it covers and everything he already holds. Gathers the exam's lectures, practice exams, keys, and professor reviews (from Paperless through the mnemosyne tools, plus any he uploads), solves them, maps his Anki memory onto them, probes whether the cards he holds are ones he can use, then writes a cram sheet, worked solutions or a full review guide, and the formula sheet the course calls for (drafting his own, or explaining the provided one line by line). Works with whatever the course gives: practice exams, slide examples, a priority list, or only lectures. Use when he says "prep me for", "exam on…", "cram sheet", "formula sheet", "review guide", "go through the practice exams", or shares old exams, keys, or review sheets before a test.
---

# mnemosyne-exam

An exam prep session. It turns three things into the documents Aman studies from:

- **what the exam covers**: its lectures, the practice exams and keys, the professor's review sheet;
- **what he holds**: his Anki memory (mnemosyne's learner model), his graded mistakes;
- **what he can do with it**: a probe session before anything is written.

A card he holds is a claim that he can recall something, not proof that he understands it. Use the cards he holds
as **hooks**: name the card, then show the step from it to the higher-order thing the exam asks for. Test the hooks
first. What fails, and what has no card at all, gets the most space.

Read `mnemosyne-tutor` for how to teach to his memory (retrieval first, derivation-led, his mistakes) and
`mnemosyne-books` for his textbooks. This skill is the exam-shaped version of both.

## 1. The pack

Start with **`mnemosyne_exam_pack(course, exam)`** (`exam` like "Exam II"; empty means the next one). It returns:

- `exam`: the date and its lectures. When it isn't mapped (no lectures), ask him which lectures it covers and point
  him to the review page's exam map. Don't guess the scope.
- `rules`: what the room allows. `sheet: own` means he writes his own formula sheet (§5d), `provided` means
  the course hands one out (the cram sheet gets a "not on your sheet" section), and `unknown` means you ask him. Then
  add the answer to `exam:` for that course in `config/courses.yaml` (you're in his repo in Claude Code), and commit.
  `output`, when set, names the document set to write (§5).
- `formula_sheet`: what the room hands him, item by item, when he has filed this exam's sheet. It differs from
  exam to exam. When it says "not filed yet", ask him for it (a photo or PDF is fine, or he files it as
  "<COURSE> <Exam> Formula Sheet"). Without it, "not on your sheet" is a guess, so label it as one.
- `lectures`: each one's Paperless `docs`, its card `query`, counts (`strong` = hook candidates, `held`, `weak`), and the
  `questions` he wrote on its slides. An open question is a confusion: probe it, and answer it in what you write as
  well (a "You asked" box in the review guide, §5b).
- `prep`: review sheets, practice exams, and keys he filed, each problem mapped to lectures and topics, with what he
  did on it (`his_status`, `his_marks`) and its card coverage. `uncovered` lists problems no card touches.
- `open_mistakes` (from graded work) and `probes` (earlier sessions' passes and misses).

**Unfiled inputs don't block.** When the pack says the formula sheet or a lecture's docs aren't filed but he has
uploaded them to the chat, work from the uploads. At the end, offer to file them in Paperless (the sheet as
"<COURSE> <Exam> Formula Sheet") so the next pack has them.

## 2. Gather everything

- **Lectures:** read every deck with `mnemosyne_doc_read(doc_id, pages)`, 8 pages a call, following `more`. Read
  the page images: his handwriting, stars, and questions on the slides are his priorities and confusions
  (`config/annotations.yaml` explains his marks).
- **Prep material in Paperless:** `pack.prep` already lists its problems; read the documents themselves for the
  statements and figures.
- **Uploads:** he files only what he has worked or marked. Practice exams and keys he hasn't touched come as files
  he gives you (often in `~/Downloads`). Ask whether there are any before you start solving.
- **His textbook:** `mnemosyne_books` / `mnemosyne_book_problems` for the sections the lectures follow, and as a
  bank of fresh problems.
- **Slide labels that cite the book** ("cf. Problem 15.54", "Exercise 15.22"): pull the book's version with
  `mnemosyne_book_read` and solve both. MyLab uses the book's numbers, and the slide often has different numbers,
  only ratios, or a different problem under the same label. Say which is which.
- **His record:** the open mistakes in the pack, and the course's results in `mnemosyne_learner_profile`
  (`graded.results`: score next to readiness the day before).

**Courses differ in what they give you.** One has practice exams and keys, one has exam examples on the slides and a
priority list, and one has nothing but lectures. Use what exists. When there's no practice material, build the problem
set yourself:
- the lectures' own worked examples;
- his graded homework (`mnemosyne_learner_profile`);
- end-of-chapter problems for the sections the lectures cover (`mnemosyne_book_problems`).

Predict what the exam will ask from where the lectures spent their time, his stars, and anything the professor said
about the exam, and label it as a prediction everywhere it appears.

## 3. Solve, then map

1. **Solve every practice and review problem yourself, before looking at the key.** Then check against the key and
   say where they disagree; keys are sometimes wrong or ambiguous. Every number gets a units and limits check.
   Constants come from `data/anchors.yaml` or CODATA, never from memory.
2. **Find the exam's shape:** the topic distribution across the old exams, the formats, and the point values. Find
   the archetypes that repeat (his 207 sheet had twelve), the traps the distractors are built from, and the red
   herrings. With no old exams, predict the shape from the syllabus and the lectures, and say it's a prediction. Note the professor's priority list when there is one ("the most typical problems"; ★ in the documents),
   and whether past exams reused slide examples with the numbers changed (PHYS 221's Exam 1 did). If they did,
   write one **number-swap drill** per priority archetype: the same problem with new numbers, its answer computed
   in code, not by hand.
3. **Audit the provided formula sheet** (`sheet: provided`): re-derive every line from its origin and check its
   signs, factors, and units against the solved problems. Sheets have errors (PHYS 221 Exam 2's had
   `p = -BkA sin(kx - ωt)` where `+` is right for `y = A cos(ωt - kx)`). Flag each error in the cram sheet and in the
   formula sheet file (§5b), with the fix and how to see it.
4. **Map his memory onto it.** For each idea the exam tests, run `mnemosyne_search_cards` with the lecture's `query`
   and the problem's `topics`. Also search other courses' tags: a hook often comes from another course (canon 22's
   home domain), e.g. Gauss's law from PHYS 207 inside a NUEN problem. Sort each idea into one of:
   - **hook candidate**: a strong card (R ≥ 0.9, mature) on the idea or the thing right under it. Early in a
     semester nothing is mature yet; then take `held` cards (seen, R ≥ 0.85) and say so, since a young card is a
     weaker claim;
   - **fading**: seen, R < 0.9;
   - **weak**: R < 0.8 or 3+ lapses;
   - **gap**: no card, or `uncovered` in the pack.

## 4. Probe the hooks (about 15 minutes, before writing)

The point is to learn which cards he can *use*. One question per hook candidate, heaviest exam weight first, 8–12 in
all. Fold in his open mistakes too: a fresh problem for each, as in tutor §3a.

- **The question takes one step up from the card.** Never the card's own question. Ask him to:
  - apply it in a new setup;
  - predict a limit or a sign;
  - pick the method and say why;
  - find the assumption that breaks;
  - or derive the next result from it.
- **Ask one at a time**, then wait for his answer. Give no hints unless he asks.
- **Log each one** with `mnemosyne_log_probe`:
  - `hook`: the card's prompt; `note_id` when the hook is one card;
  - `question`, `right`, `hints`;
  - `note`: what broke, or how he did it.

  Log the misses too. A mistake's fresh problem goes to `mnemosyne_log_attempt` instead.
- **A pass makes the card a hook:** the sheets build on it in one line. **A miss makes it a gap:** he recalls it but
  can't use it yet, which is the most valuable thing this session finds. Say so plainly.
- **If he's short on time**, probe the 5 heaviest and go.

**Skip a lecture's probe** when its cards are almost all unseen, or it has no cards. There's nothing to test yet,
and every idea in it is a gap. Say so in the final message, list those lectures, and offer to card them through
`mnemosyne-cards`.

## 5. Write

Everything follows the shape of his own exam sets (`examples/`), which he liked. Write first-principles,
limits-first, and dense. Don't re-explain a probed hook; spend the space on the gaps.

**Pick the set.** Two separate choices; every course gets a cram sheet (§5a).

1. **The format** of the teaching and solutions, from `rules.output` (`config/courses.yaml`):
   - **`html-set`:** a review guide, solutions, and the cram sheet as HTML, with PDFs of the guide and cram sheet
     (§5b). Use it when the exam is derivations and worked examples he needs taught, not just solved.
   - **`latex`:** the cram sheet plus LaTeX worked solutions (§5c). Use it for proof-heavy math and typeset keys
     (MATH 309).

   When `output` isn't set, pick one, say which and why, and once he's happy with it, record it under that course's
   `exam:` and commit.
2. **The formula sheet**, from `rules.sheet`, whatever the format:
   - **`provided`:** audit it (§3) and explain it line by line (Formula Sheet Explained, §5b, item 4).
   - **`own`:** draft his sheet with him (§5d). It's LaTeX in either format, because it's a printed page sized to a
     rule.
   - **`none`:** no sheet document. The cram sheet's "night before" list is what he carries in his head.

### a. Cram sheet

HTML with MathJax and a **quiz mode**: a sticky checkbox blurs each kernel until he taps it
(`examples/quiz-mode.html` has the CSS and JS; the HTML set's `style.css` has it built in). Publish it as a private
Artifact so it opens on his phone, and also save the file. In order:

1. **How to use this:** a timed plan for the last hour or day.
2. **What the exam tests:** the distribution table from the solved exams, formats, and point values (or the
   predicted shape, labeled as one, §3).
3. **Triage:** "asked for → machine" or "trigger → tool". This is the classification step he does before writing
   anything (207's "10-second triage", 221's "Trigger → tool").
4. **Hooks:** for each probed hook, write *You hold:* the card he has (its prompt, near verbatim), then *Step up:*
   the chain from it to what the exam asks, one move per line, then *Check:* a limit or the units. This section is
   the reason the skill exists: it starts from his memory and climbs.
5. **Gaps:** probe misses, weak cards, and uncovered ideas, with the most space. For each: the idea from first
   principles, the derivation he should reproduce on blank paper, one worked example, and a fresh problem with the
   answer under quiz blur.
6. **Kernels and archetypes:** each pattern that repeats, what it looks like, its recipe, and where it was seen
   ("F25 Q16"). With a priority list, one quiz card per ★ problem.
7. **Number-swap drills** (when exams reuse examples, §3), as quiz cards.
8. **Traps**, ranked by how often they cost points, then **red herrings** and **sanity checks**.
9. **Not on your sheet: carry it in** (when the sheet is `provided`): what the solved problems needed that
   `formula_sheet` lacks, where the sheet gives a result only in a form he must rearrange, and the sheet's errors
   from the audit (§3).
10. **Exam-hall protocol** from the format and length: passes, timing, partial-credit tactics.
11. **The night before:** the 5–8 things to reproduce from a blank page. If they all come out clean, he's ready.

### b. The HTML set (`output: html-set`)

Self-contained HTML files built from pandoc markdown by `examples/html-set/` (its README has the commands, the
include syntax, and the box markup). `examples/phys221-exam2.md` is the reference set: start from a copy of its
sources and keep the parts this course's material supports. The build makes whichever documents have a source file.

1. **Review Guide** (`guide.md`): teaching from first principles in chapter order. It opens with the professor's
   priority list, if he gave one, as a table of where each problem is worked; otherwise with the exam's shape (§3).
   Every problem the material offers is worked in place: slide exam examples, practice and review problems, or,
   when there are none, the problem set you built (§2). The box types:
   - **intuition**: the physical picture;
   - **derive**: every step, none skipped;
   - **trap**: what costs points;
   - **key**: what he carries into the room;
   - **asked**: "You asked", one for every open margin question in `pack.lectures[].questions`, answered where its
     topic comes up (and still probed, §4);
   - **example**: a worked exam example, with ★ (`.star`) for the priority list.

   Add figures where a picture carries the idea (`figs.py`, house style).
2. **Solutions** (`solutions.md`): the same example blocks in lecture order, then the number-swap drills (when §3
   calls for them), then textbook practice problems with answers. A preface says where slide and book disagree (§2).
3. **Cram Sheet** (`cram.md`): §5a, with any drills pulled in from `drills.md`.
4. **Formula Sheet Explained** (`formula.md`, only when `sheet: provided`): the provided sheet line by line, in its
   order. For each line: what it
   means, units, where it comes from, when to use it, its trap, and an example. Flag the audit's errors in place
   (§3). End with "not on the sheet": what the problems needed that it lacks.

Write each worked example once in `examples.md` (`<!-- EX:id -->` … `<!-- END -->`) and include it with `{{EX:id}}`
in both the guide and the solutions, so the two can't drift. Drills go in their own file, included with
`{{FILE:drills.md}}`.

Then build, check, and print:
- `build.py <src> <out> --prefix <COURSE>_<Exam>` strips pandoc's CSS and every MathJax/polyfill `<script src>` and
  fails if any remain. It inlines the fonts and MathJax, embeds the figures, and writes the cram sheet's Artifact
  variant.
- `shot.py` on each page at 375 and 1300 px: no MathJax errors, no leaked `$`, nothing scrolling sideways. Fix and
  rebuild until it exits 0.
- `topdf.py` for the review guide and the cram sheet: Letter with a 1.45in right margin for iPad notes.

Pandoc gotchas:
- `= $ m/s` is not math, because a closing `$` can't follow a space. Write `= \text{m/s}$`.
- A pipe table's column widths come from the dash counts in its separator row.

### c. Worked solutions (LaTeX)

LaTeX → PDF (`examples/math309-worked-solutions.tex`). For every practice and review problem:
- the solution with each step's reason, and a check (substitute back, a second method, a limit);
- an **Answer** box;
- a **Geometric/physical picture** box where one helps;
- an **Exam tip** box for the trap.

Add a master answer grid for multiple-choice exams, and mark every disagreement with a key.

### d. His own formula sheet (only when `rules.sheet` is `own`)

Dense LaTeX in columns (`examples/nuen201-formula-sheet.tex`), sized to the room's rule (NUEN 201: one A4, two
sides). Spend the space on what he can't regenerate under pressure:
- what failed in the probes, and weak cards;
- sign and frame conventions;
- tables of values;
- the step templates for predicted questions;
- the checks.

Leave out what he holds as probed hooks: the sheet is for the gaps. Build it with him: draft it, then he cuts, adds,
and reorders. Writing it is part of studying, and it's his sheet. Compile with MiKTeX on the laptop
(`pdflatex`, or `xelatex` for Unicode). Check the page count against the rule, and tighten until it fits.

### Files

- **Where:** `~/Downloads`, where he keeps them now. On Windows, the session needs that folder granted first:
  request it, and the app asks him. Write the finished files there, and publish the cram sheet as a private Artifact
  (the `_artifact.html` variant from the HTML set).
- **Names:** `<COURSE>_<Exam>_Cram_Sheet.html` (and `.pdf` in the HTML set), `<COURSE>_<Exam>_Review_Guide.html` and
  `.pdf`, `<COURSE>_<Exam>_Solutions.html`, `<COURSE>_<Exam>_Formula_Sheet_Explained.html`;
  `<COURSE>_<Exam>_Worked_Solutions.tex` and its `.pdf`, and `<COURSE>_<Exam>_Formula_Sheet.tex` and its `.pdf`
  (e.g. `PHYS221_Exam2_Cram_Sheet.html`).
- **Math:** MathJax `\( \)` and `\[ \]` in hand-written HTML, never `$…$`. In the HTML set's markdown, pandoc's
  `$…$` and `$$…$$` are the source; pandoc writes `\( \)`, and `shot.py` catches any `$` that leaks.

## 6. Close the loop

- **Gaps a card would fix** (a fact he lacked, a trap he fell into): offer cards through `mnemosyne-cards`. They
  wait for his review like any other. Don't card the whole cram sheet. Cards come from what he now understands.
- **Questions he asks:** log them with `mnemosyne_log_question`.
- **His exam deck:** remind him that `~Exam::<COURSE>-<Exam>` is building and to work it daily. Point him to it for
  the weak and fading cards.
- **The final message** names what's still open: lectures whose probes were skipped (offer to card them), uploads
  to file in Paperless, and the provided sheet's errors.
- **After the exam:** he uploads the graded copy to Paperless (type Exam), and its debrief puts the score next to
  this session's probes and readiness. If the probes passed and the exam still went badly, the probes were too
  easy; say so next time.

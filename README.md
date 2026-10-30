# mnemosyne

>(NOTE: This cut is almost entirely AI-maintained and updated)

> **This is the public cut** of a private repo. The code, canon, skills, and docs are all here. Aman's own record
> is not: the collection's audits and run diffs, his status and exam dates, weekly audit records, and the exam
> prep (the exam skill's examples and `docs/exam-prep/`), which is built from his courses' material. The design
> docs keep their method and results without his own review numbers. Links to what's left out go nowhere.

Aman's memory system: a [Helios](https://github.com/almondshawarma/homelab) module that generates, verifies, schedules, and surfaces Anki cards. Anki is only the review surface: AnkiMobile on the phone for walks, and Desktop on the ThinkPad. This repo holds everything else.

> Fluency as a fallback for first-principles thinking: faster than the tortoise, sturdier than the hare.

**What mnemosyne can do for you, and how to use it: [the user guide](docs/user-guide.md).** How learning a concept,
making a clip of it, and keeping what you learned fit together: [learn it, make it, keep it](docs/learn-and-make.md).

The design, phases, and acceptance criteria live in [`PLAN.md`](PLAN.md). Rules for working in this repo are in [`CLAUDE.md`](CLAUDE.md). How cards are written is defined in exactly one place: [the card canon](.claude/skills/mnemosyne-cards/SKILL.md).

## Status

| Phase | Scope | State |
|---|---|---|
| P0 Hygiene | audit, dedupe, tag normalization dry run | ✅ 2026-09-23 |
| P1 Schema & canon | M-* note types, presets, migration, deterministic speech, controller | ✅ 2026-09-24; golden set fully reviewed 2026-09-26 |
| P2 Core | anki-core, Ledger tables, `/inbox`, Telegram approvals, MCP tools | ✅ 2026-09-25: MCP proposal → Telegram approve → on the phone; re-proposing is a no-op |
| P3 Ingest | Paperless → Claude → verify → dedupe → approve → write | acceptance met: iPad → cards in under 2 minutes (MATH 309 L03 on 09-25, MEEN 221 L14 on 09-26); five exam maps confirmed on 09-25. Added since: your questions (09-29), decks sent again (10-04), textbook readings (10-04), graded work (10-06; PHYS 207 Exam I debriefed), research papers (10-06), exam prep material (10-07: review sheets, practice exams, keys attached to their exam). Remaining scope: Obsidian callouts |
| P4 Scheduling | exam and walk decks, load governor, leech doctor, metrics, weekly report | built 2026-09-27: all of it runs at 04:15, plus nightly Anki backups and the learner model behind the tutor tools. Exam decks ✅: the four Exam I decks built, then removed themselves after their exams. Still pending: ±15% of budget for two weeks, which needs daily reviews to measure |
| P5 Audio & languages | formula speech, neural TTS, Spanish/Hindi/French/IPA | started 2026-10-05: IPA cards with Wikimedia recordings and ASL cards with Signbank videos, in Anki since 10-07; IPA in every language card (canon 23) |
| P6 Fluency | Arithmetica, derivation ritual | |

Snapshots of the collection come from `scripts/audit.py`.

## What it does

The [user guide](docs/user-guide.md) has each of these in full.

- **Cards from what he studies.** Lectures filed from the iPad, textbook sections (from the ebook or a scan),
  research papers, graded exams and homework, Telegram, and Claude sessions all become cards written by one canon.
  Every number is checked, near-duplicates are caught by embedding, and what the approval policy holds back waits on
  the review page.
- **Scheduling that runs itself at 04:15.** Exam decks ramp up retrieval 10 days before each exam and remove
  themselves after; walk decks hold the audio cards for AnkiMobile on walks; focus decks practice one subject on
  purpose; a load governor sizes each day's new cards from the time the week's reviews leave.
- **Speech.** Formulas are spoken through MathJax and the Speech Rule Engine (`17 times 8`, `one eighth`), so math
  cards work eyes-free with a Bluetooth controller.
- **Languages.** IPA with Wikimedia recordings, ASL with Signbank videos, and IPA on every Spanish, Hindi, and French
  card.
- **A learner model for Claude.** Any Claude with the Helios MCP can read what he holds and struggles with, search his
  textbooks with citations, and pull practice problems. Skills turn that into tutoring, exam prep (cram sheets,
  worked solutions, formula sheets, probes of the cards he holds), and a weekly audit that proposes changes to the
  canon with the evidence.
- **His questions.** Questions written on slides are read off each lecture, answered, sharpened, and kept in
  Obsidian; the ones that reach past the course go to Hyperion.
- **Leech doctor.** Cards he keeps failing are diagnosed against the canon each morning, with a rewrite or a
  replacement proposed for him to decide.
- **Lecture videos and clips.** Claude watches lecture videos (captions and key frames, never the video), dissects
  animations he admires, and writes Manim clips on amanim, rendered in a sandbox and published on `/watch`.

## How it works (target architecture)

```
iPad / Paperless ─┐
Telegram ─────────┼─▶ mnemosyne (Obelisk) ─▶ anki-core ⇄ self-hosted sync server ⇄ AnkiMobile / Desktop
Claude (skill) ───┤    extract → propose → verify → dedupe → speakify → approve → write
Obsidian ─────────┘    04:15 daily jobs · Ledger state · InfluxDB metrics
```

- **Lectures** filed in Paperless (`document_type: Lecture` plus a course tag) become cards automatically. Everything else is opt-in with the `mnemosyne` tag.
- **Numbers** are never trusted from an LLM. Every numeric card passes a units check (`pint`) and a value check against `scipy.constants` or `data/anchors.yaml`.
- **Exams and quizzes** come from the calendar, and filtered decks ramp up retrieval 10 days out.

## Working against the real collection

Since P2, cards reach the collection only through mnemosyne → anki-core, as upserts keyed on `MnemosyneID`. That covers new cards, and edits to cards already in Anki (`POST /drafts/{id}/revise`, which requires a reason). Nothing else opens the collection.

Two kinds of change still go through Anki Desktop:
- **Bulk or destructive changes** (deletes, mass suspends, repositions, preset edits): a reviewed dry-run diff committed to [`docs/runs/`](docs/runs), applied from the debug console. The code runs inside Anki's own process, so the collection file is never opened twice.
- **Schema changes**, which Aman makes by hand from a checklist in [`docs/desktop-sessions/`](docs/desktop-sessions).

The `~Exam::*` and `~Walk::*` filtered decks, and each deck's today-only new limit, are mnemosyne's own 04:15 jobs (P4, CLAUDE.md invariant 9's standing exception).

```python
# Anki Desktop → Tools → Debug Console (Ctrl+Shift+;)
import runpy
m = runpy.run_path(r"C:\Users\amans\OneDrive\Documents\Projects\mnemosyne\scripts\anki_apply.py")
m["step"](mw.col, r"...\docs\runs\<plan>.json", "<step>")                  # dry run
m["step"](mw.col, r"...\docs\runs\<plan>.json", "<step>", dry_run=False)   # apply
```

| Script | Does | Touches a collection? |
|---|---|---|
| `scripts/audit.py <colpkg>` | Read-only audit: counts, decks, tags, duplicates | Reads a copy of an export |
| `scripts/retag.py <colpkg> --out …` | Tag normalization diff | Reads a copy of an export |
| `scripts/migrate.py <colpkg> --out …` | P1 migration diff (dedupe, note types, moves, speech) | Reads a copy of an export |
| `scripts/speakify.py` | Deterministic TeX → speech (`17 times 8`, `one eighth`) | No |
| `scripts/anki_apply.py` | Applies a committed diff, idempotently, with a before-state check | Inside Anki Desktop only |
| `scripts/golden_review.py` | Collects golden cards awaiting review, and merges the review page's decisions back | No |
| `.claude/skills/mnemosyne-cards/review.py` | Builds a card batch for the review page, and summarizes the export | No |

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run pyright
```

Tests build throwaway collections from `tests/fixtures/` with the `anki` library. No test ever opens the real profile or logs in as Aman's sync user; integration tests use the `mnemosyne-test` sync user (see `docs/deploy.md`).

## Using the card skill with Claude

The canon ships as a skill, `.claude/skills/mnemosyne-cards/`, and as a private Claude Code plugin.

- **Claude Code in this repo:** already active from the working tree. Say "mnemosyne this" or "make cards".
- **Claude Code everywhere else:** install the plugin from this repo's marketplace. The repo is private, so only machines with git access to it can install; git's stored credentials handle that.

  ```bash
  claude plugin marketplace add almondshawarma/mnemosyne
  ```

  ```bash
  claude plugin install mnemosyne@mnemosyne
  ```

  The skill then appears as `mnemosyne:mnemosyne-cards`. The plugin has no `version`, so installs track commits on `main`. Auto-update is on (`"autoUpdate": true` on the marketplace in `~/.claude/settings.json`): a session refreshes it in the background within ten minutes of your first message, and the update loads next session. Inside this repo the working-tree skill loads too; they match whenever `main` is pushed.
- **claude.ai and the Claude apps (iPad, iPhone):** personal accounts can't sync skills from GitHub, so re-upload after canon changes.
  1. Build the zip from the last commit:

     ```bash
     git archive --format=zip --prefix=mnemosyne-cards/ -o mnemosyne-cards.zip HEAD:.claude/skills/mnemosyne-cards
     ```
  2. Upload it under **Settings → Capabilities → Skills**. Skills need code execution turned on in the same settings page.

### Reviewing cards on the review page

mnemosyne serves a review page at `http://<obelisk>:8110/review`, reachable on the tailnet from a phone, iPad, or laptop. The Obelisk homepage has a tile for it. It has three tabs:
- **From Claude:** batches Claude wrote in a session. Ask it to "send them to the review page". Approve, edit, or reject each card with a reason, add notes, mark left-out items that should be cards, then press **Finish** and tell Claude.
  - **Undo:** pressed Finish too early? **Undo finish** opens the batch again with your decisions kept, until Claude applies it.
  - **Media:** IPA and sign cards have a ▶ button that plays the recording or video the card will get.
  - Approved cards go to Anki right away. Claude takes the export back, merges golden batches, and proposes canon changes from the reasons. The procedure is [`review.md`](.claude/skills/mnemosyne-cards/review.md).
- **Waiting:** every pipeline card the approval policy held back (dedupe flags, questions, failed number checks, calibration), decided card by card instead of all-or-nothing in Telegram. Approved cards that Anki refused (usually a front that duplicates an existing note) show at the top, to reword or withdraw.
- **Exams:** exam maps to confirm, and each exam's lecture coverage and card count. mnemosyne builds `~Exam::*` decks from these maps every day at 04:15, from 10 days before each exam, and removes each deck the day after. The copyable deck search per exam is still there.

Each device asks for the approve token once. Without the page, the skill outputs a `mnemosyne-cards` JSON block.

## Layout

```
.claude/skills/  mnemosyne-cards (the canon), -tutor, -books, -exam, -weekly, -unpack, and -clips (clips, the look,
                 dissecting a video, references.md)
.claude-plugin/  plugin marketplace (the plugin root is .claude/)
src/mnemosyne/   the service: ingest, generate, verify, schedule, anki-core client, Ledger, web pages, videos, clips
services/        speech (MathJax + SRE), manim (one sandboxed render), watch (the watcher and study mode)
scenes/          Manim scenes by course, on mn_style.py; gallery/ (one demo per technique, stills committed)
config/          courses, decks/presets, approval policy, budget, topics, books, clips
desktop/         M-* note type definitions (fields, templates, CSS)
docs/            user guide, learn-and-make, deploy, security reviews, status, audits, desktop checklists, runs/, weekly/
scripts/         audit, migrations, anki_apply (inside Anki), render/render_runner (clips), watch (videos, studies)
tests/           fixture-collection tests
```

The technique library behind the clips is its own repo, **amanim** (`~/OneDrive/Documents/Projects/amanim`), with
the taste guide, `TASTE.md`.

Lecture material is processed privately and never published.

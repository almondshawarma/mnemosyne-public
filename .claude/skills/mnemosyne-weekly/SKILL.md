---
name: mnemosyne-weekly
description: The weekly audit of mnemosyne. Reads the week's cards, Aman's edits and rejections, how new cards are holding, leech diagnoses, skips, questions, probes, and exam coverage. Spot-checks one lecture against its cards, checks whether last week's changes worked, then proposes changes to the canon, config, and cards with the evidence for each, and applies only what he approves. Use when he says "weekly audit", "audit the week", "how did the cards do this week", "what should mnemosyne change", or on Sunday after the weekly report.
---

# mnemosyne-weekly

Once a week, a careful reader goes over everything mnemosyne made and how it went. The pipeline writes cards fast,
on the TAMU gateway. This session is slower and runs on Aman's own Claude account, so it costs him nothing extra.
It looks for patterns the pipeline can't see in itself: rules the canon keeps missing, sources that need more of his
time, cards that don't hold, and gaps no card covers. Then it changes how mnemosyne works, with his OK.

**Run it in Claude Code, in the mnemosyne repo.** Most changes land in files there (the canon, `config/`, the golden
set), and the audit's record is committed there too.

## Ground rules

- **Aman decides.** Nothing changes until he says yes to that item. Present one numbered list and apply only what
  he approves.
- **Evidence, not taste.** Every proposal cites what it rests on: his words from `said`, counts, card prompts.
  - A canon change needs at least three instances, from this week or across audits, or one explicit statement
    from him. One odd card is a card fix, not a rule.
  - Prefer sharpening or merging a rule over adding one. The canon is long, and every rule makes every prompt longer.
    Say so when a rule never shows up in the evidence, or when the evidence works against it: those are candidates to
    cut.
- **Small numbers are noise.** One week of FSRS data is thin. Compare sources, courses, and note types against each
  other rather than against absolute targets. Under about 30 cards, `fail_share` is anecdote: say "watch", don't act.
  New cards fail more in their first week by nature.
- **His time is the scarcest thing.** Upkeep minutes (time spent curating) are a cost. A change that holds more cards
  for review has to earn it. A change that lets more cards in raises his daily load: check `load` against the budget.
- **It never spends money.** No paid API keys, and no billing on his personal account: he approves any spending
  himself, each time (Aman, 2026-10-07). A change that makes more or bigger TAMU calls is fine, but say what it does
  to the $10 a day.
- **The invariants hold** (CLAUDE.md). The audit writes no cards to Anki itself, and it never suspends, deletes, or
  repositions. Card fixes go through the paths below. A schema change becomes a desktop checklist in
  `docs/desktop-sessions/`, never an edit.

## 1. Gather

1. **Last week's audit:** read the newest file in `docs/weekly/`. Its "Changes" section says what was changed and
   which number should move. Check each one in step 3.
2. **The evidence:** `mnemosyne_weekly_audit()` for the seven Anki days ending yesterday (`last="YYYY-MM-DD"` for
   another week). Read its `how_to_read` first. The sections are:
   - `intake`: cards per source, and how many went straight in, waited, were edited, rejected, or are still waiting;
     `held_because` counts why cards waited.
   - `said`: his edits (field by field, before and after) and rejections, with his reasons. Rejections by Claude are
     counted separately: those are dedupe misses Claude caught, not his taste.
   - `holding`: cards first studied in the window, and the share he has already rated Again, per course, note type,
     and pipeline-written vs other. `lapsed` lists the worst ones.
   - `leeches`: the leech doctor's diagnoses this week, and how many of those cards the pipeline wrote.
   - `skipped`: what lecture generation left out, and why.
   - `dedupe`: what the duplicate check did. It covers every card it checked, the cards it **dropped** without him
     seeing them, and what he did with the ones it flagged. Each drop comes with the note it matched and its whole
     card (`payload`). Reviewing the drops here is how wrong drops get caught (Aman, 2026-10-29): dedupe scores the
     whole card, and no threshold is precise (`docs/embeddings.md`, E1).
   - `questions`, `mistakes`, `probes`: where his understanding caught. Questions answered with no card, graded
     mistakes never retried, probes he missed (a card he recalls but can't use).
   - `exams`: the next three weeks, with readiness and lectures that have no cards.
   - `jobs_not_done`, `load`: pipeline health, minutes against the budget, upkeep.
3. **His current state:** `mnemosyne_learner_profile()` for what this week sits on top of.
4. **Stuck lectures:** `mnemosyne_stuck_documents()`. A lecture still waiting is a gap.

## 2. Spot-check one lecture

The numbers say where to look. Reading says what's wrong. Pick **one** lecture from this week: the one with the
most edits or rejections, the highest `fail_share`, or a probe miss. When nothing stands out, take the course audited
least recently (the `docs/weekly/` files say which).

1. Read the deck: `mnemosyne_doc_read(doc_id, pages)`, 8 pages a call, images on. His marks are on the images.
2. Read its cards: `mnemosyne_search_cards("tag:course::<code>::lNN*")`.
3. Judge them against the canon (`../mnemosyne-cards/SKILL.md`), the way he would on the review page:
   - **Coverage:** load-bearing content with no card; a must-card mark (★, "card") with neither a card nor a
     `skipped` entry; skips that were wrong.
   - **Form:** cards that break a canon rule. Name the rule.
   - **Truth:** a card that teaches the slide wrong, or teaches a slide's own error (check the physics yourself).
   - **Connection:** an idea he already held in another course that got a second card instead of a tag (canon 22).
4. Read about ten more of the week's cards from other sources, chosen at random, against the canon.

## 3. Judge

Write findings in four groups. Each finding gets a number, its evidence, and how confident you are.

1. **Did last week's changes work?** For each change: the number it should have moved, then and now. A change that
   didn't help gets revised or reverted. Say which.
2. **What worked:** sources, note types, and card forms that held, and that he approved without edits. Name them, so
   they're kept and copied.
3. **What didn't:**
   - repeated edits of one kind (his edits are the canon's misses);
   - rejection reasons that repeat;
   - a source or course whose cards fail more than the rest;
   - leech diagnoses that cluster (`not_atomic`, `ambiguous_cue`…) on pipeline cards;
   - many cards waiting for one reason (`held_because`);
   - dedupe drops that weren't repeats. A minimal pair (±1σ vs ±2σ, n_e→0 vs T_e→0), a limit card, and the reverse
     of a card are all distinct facts. Count them against `dropped`; that share is dedupe's error rate this week;
   - spot-check problems.
4. **Gaps:**
   - lectures without cards before an exam;
   - stuck lectures;
   - questions answered with no card where a card was due;
   - open mistakes never retried;
   - probe misses with no card for the step he couldn't take;
   - topics his questions keep returning to with few cards.

## 4. Propose

One list. For each item: what changes, why (finding numbers), what it costs him (minutes, load, TAMU spend), and
**the measure**: which number next week's audit checks.

The levers:
- **The canon** (`.claude/skills/mnemosyne-cards/SKILL.md`). Edit a rule's wording, add an example from this week's
  evidence, or cut a rule. Tag it like the others: `[weekly audit, YYYY-MM-DD]`. Every pipeline prompt imports the
  canon, so it takes effect on the next lecture after deploy.
- **The golden set** (`.claude/skills/mnemosyne-cards/golden/`, see its README). Cards he edited into shape, or
  approved untouched from a hard lecture, become worked examples for that course's prompt.
- **Config:**
  - `approval.yaml`: which sources auto-approve, and what forces review;
  - `budget.yaml`: minutes, new-card caps, deck weights (stay inside his stated priorities);
  - `topics.yaml`: missing vocabulary that cards keep needing;
  - `annotations.yaml`: marks the pipeline misreads;
  - `llm.yaml`: routes and limits, with the TAMU cost said out loud.
  - **dedupe's thresholds** (`DUPLICATE`, `FLAG` in `verify/dedupe.py`, a code change):
    - raise `DUPLICATE` when wrong drops keep showing up;
    - raise `FLAG` when flagged cards are nearly all approved (`flagged_outcomes`).
- **Cards:**
  - **New cards for gaps:** write them by the canon and propose them through the checked path (`stuck.md`, step
    4: `POST /inbox` with `checks: true`, run on the Obelisk). They go through the number checks, dedupe, and the
    approval policy like any others.
  - **Fixing an existing card:** give him an Anki search (`nid:…,…`) to paste in the Browser, select all, and Mark
    (Ctrl+K). The leech doctor diagnoses each one the next morning, and he applies the fix on the Leeches tab. The
    audit never rewrites a card in Anki itself.
  - **Questions to card:** name the question ids. He cards them from the review page.
  - **Restoring a wrong drop:** propose its `payload` again with `mnemosyne_propose_cards`, with its `source`. It
    comes back held, with its match shown, and he approves it on the review page. List the drops you'd restore
    together, so he decides them as one item.
- **Code:** a small, clear fix (a prompt line, a threshold, a filter) can be made in this session with tests, once
  he approves it. Anything larger becomes a task chip (spawn a task) or a line in `PLAN.md` §11, with the phase it
  belongs to.
- **Him:** some findings are about habits, not the system. Say them plainly and briefly: retry a mistake, map an
  exam, send the annotated deck again.

Keep the list to what matters: ten items at most, the highest-leverage first. Fold the rest into "watch".

## 5. Apply and record

1. Apply what he approved. Run `uv run pytest`, `uv run ruff check .`, `uv run pyright` after code or config
   changes.
2. Write `docs/weekly/YYYY-MM-DD.md`, dated the window's last day:

   ```markdown
   # Weekly audit · week to Sat Oct 10

   ## The week
   Three lines: cards made, his upkeep minutes, load against the budget, how new cards held.

   ## Last week's changes
   Each one: the measure, then → now, and kept / revised / reverted.

   ## Findings
   Numbered, with evidence, as presented.

   ## Changes
   Each approved item: what changed (file and commit), and the measure for next week.

   ## Declined and watching
   What he said no to (and why, if he said), and what's being watched.
   ```

3. Commit the record with the changes, deploy if code or config changed (CLAUDE.md, Helios conventions), and update
   the plugin when the canon changed (`docs/user-guide.md`, "Skills").
4. Tell him in a few lines: what changed, the one thing to watch, and anything only he can do.

## Pacing

The session should take him 20–30 minutes. Do the gathering, the spot-check, and the judging before you show him
anything. Then show the findings and the list at once, so his time goes to deciding.

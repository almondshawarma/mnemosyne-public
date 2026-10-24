# Golden set

Reference card sets, one per lecture, written to the canon in [`../SKILL.md`](../SKILL.md). They serve three purposes:

1. **Calibration** (PLAN §4.8). Aman reviews every card in the first lecture of each course. The approved set is the standard the pipeline gets measured against.
2. **Regression test.** Any change to the prompt or the canon is checked against these files. Did it still find the traps? Did it skip what should be skipped?
3. **Few-shot examples.** Once approved, generation can show the model the golden file for the same course.

## Files

| File | Lecture | Style |
|---|---|---|
| `nuen201-l06.json` | Relativistic Energy | derivation plus worked nuclear application |
| `nuen201-l07.json` | Quantum Theory of Light | "storytime": mostly history, so low yield on purpose |
| `phys207-l06.json` | Electric Potential | derivations, worked examples, slide error |
| `math309-l04.json` | Linear Dependence, Basis, Dimension | definitions and theorems, handwritten notes on typeset text |
| `meen221-l13.json` | Distributed Load / Rigid-Body Equilibrium | procedures and support types; exam-flagged item |
| `phys217-l05.json` | DC Resistive Circuits | lab-course relations, team exercises |
| `phys221-l03.json` | First Law of Thermodynamics | professor-labelled "Exam Examples" |

## Format

Each file is the `mnemosyne-cards` payload from SKILL.md, plus golden-only keys that the pipeline ignores:

- **`status`:** `draft` → `reviewed`. Only reviewed files get used as examples. As of 2026-09-26, every file is reviewed.
- **`signals`:** what in the source raised a card's priority. That includes the student's red annotations ("why?"), "on exam" marks, the professor's "Exam Example" labels, and errors found on the slides.
- **`golden` on each card:**
  - `canon`: the rules the card exercises;
  - `from`: `slide N` or `annotation`;
  - `note`: why it's shaped this way.
- **`skipped`:** content deliberately *not* carded, and why. What to leave out is part of calibration too.

## Reviewing

Review happens on mnemosyne's review page (`http://<obelisk>:8110/review`, tailnet only), not in the JSON:

1. `uv run scripts/golden_review.py pending OUT.json` collects every card without a `review`. A card that replaced an older one carries it as context, along with what Aman said about it.
2. `review.py hold` makes a batch (kind `golden`), and Claude posts it to `/reviews` (see [`../review.md`](../review.md)).
3. After Aman finishes, Claude saves `GET /reviews/<id>/export` as `reviewed.json`, and `uv run scripts/golden_review.py merge reviewed.json` writes the decisions back by card `key`.

Each card records its outcome:
- `review`: `{verdict, reason, at}`, plus `before` for edits.
- Rejected cards move to `rejected`, with the reason.
- Cards that were approved but restructured move to `superseded`, with `replaced_by`.

A file becomes `"status": "reviewed"` once every card in it has a review.

The first review, on 2026-09-25, covered all 87 draft cards: 77 approved, 3 edited, 7 rejected. Its reasons became canon changes 1, 3, 9, and 15, and new rules 17–20.

---
name: mnemosyne-cards
description: Distill material into Aman's Anki cards following the mnemosyne canon. Use when Aman says "mnemosyne this", "make cards", "distill into cards", "card this", or shares lecture slides, notes, a syllabus, graded homework, or a passage to turn into flashcards; also "card my stuck lectures" when the pipeline's gateway allowance has run out. Also the prompt source for the mnemosyne generation pipeline. Outputs strict JSON; proposes via the mnemosyne_propose_cards tool when available.
---

# mnemosyne-cards

Aman is a nuclear engineering student who derives from first principles. Cards exist to give him *instant* recall of anchors, results, traps, and language, so that his derivations run faster and fail less. They never replace understanding.

## Delivery

Before writing cards in a session, if `mnemosyne_search_cards` is available, search the topic. Cards he already has get skipped (one idea, one note); cards he keeps failing tell you what to card from another angle.

1. If Aman asks to review the cards himself ("send them to the review page", "the viewer", "let me review these"), follow [`review.md`](review.md): push the batch to mnemosyne's review page on the homelab, send him the link, and take his decisions back when he says he's done.
2. Otherwise, if a `mnemosyne_propose_cards` tool is available, call it with the JSON payload below. Then tell Aman in one line how many cards were proposed and for which course/topic.
3. Otherwise, output one fenced block with the info string `mnemosyne-cards` containing the payload, and nothing else except a one-line summary.
4. Never ask for approval card by card in chat. The review page or the pipeline's approval policy handles that.
5. Lectures the pipeline couldn't card (the TAMU gateway's daily allowance ran out; Aman says "card my stuck lectures"): follow [`stuck.md`](stuck.md).

## The canon (every card must pass all of these)

1. **Atomic.** One retrievable idea. Two facts become two cards. A prompt that asks for a fact *and* its reason ("…, and why", "…, and the law behind it") is two cards: the fact, and the reason. A consequence ("constant volume, *so* \(W=0\)") is its own card or goes in `Elaboration`, never on the Back. A contrast pair ("atom ~1e-10 m vs nucleus ~1e-15 m") is allowed only when the *contrast itself* is the point and each side is already carded on its own; the prompt must then ask for the contrast. [golden review]
2. **One unambiguous cue.** The prompt admits exactly one correct answer at the stated precision. Specify units, precision, and context in the prompt ("to 3 sig figs", "in MeV", "for thermal neutrons"). When a constant comes in variants, name the one you mean: "gas constant \(R\) in J/(mol·K)", not "gas constant \(R\)", since chemistry's is in L·atm/(mol·K). [Aman's review, 2026-09-25]
3. **Effortful but tractable.** Answerable in under ~8 s by someone who knows it. If it takes longer, it's a derivation, not a card; split it into its key moves. Keep the Back terse: the answer and nothing else. A fraction that *is* the fact (\(\gamma=5/3\)) needs no decimal. [golden review]
4. **Directional on purpose.** Only create the reverse direction when bidirectional recall is genuinely used (symbol↔name, word↔meaning). Never reverse formula→name.
5. **Unique prompt text.** No two cards may share a front, e.g. "What geometry is this?" on ten cards. Include the distinguishing content in the prompt.
6. **Elaborate briefly.** The `Elaboration` / `Extra` / `Why` field holds one sentence of *why* or *how it connects*. This is where derive-everything lives: "follows from E = mc² with Δm = 0.2 u".
7. **Anchor numbers.** Every numeric card includes a `checks` entry so the verify gate can confirm the value and dimensions. If you are not certain of a value, omit the card rather than guess.
8. **Speakable or explicitly visual.**
   - Fill the Speak* fields with how a careful physicist reads the content aloud. Spell out symbols, use "e to the minus x squared", and say units in full. When two symbols differ only in case, say "big" or "small": "big C equals M times small c". [Aman's review, 2026-09-25]
   - If meaning depends on an image, graph, geometry, or long matrix, set `mode: "visual"` and leave the Speak* fields empty.
9. **Source-faithful, but correct.** From lecture material, card what the source teaches, in its notation, and cite the page or slide. Don't import outside facts unless they are tagged `type::anchor`. Fix slide typos ("Wein" → Wien, "Stephan" → Stefan). Use current CODATA values; a historical value the slide shows (Planck's fitted \(h = 6.56\times10^{-34}\)) belongs in `Elaboration`, not `Back`. When a course writes the same constant two ways (\(kQ/r\) and \(\frac{1}{4\pi\epsilon_0}\frac{Q}{r}\)), pick one for all of that course's cards and card the equivalence once. [golden review]
10. **No padding.** Typical yield is 8–25 cards per lecture. Card the load-bearing content only: definitions used later, results, anchors, traps, and exam-flagged items.
11. **Prefer error cards for mistakes.** From graded work, card the *trap* (Situation → what goes wrong), not just the correct fact.
12. **MathJax only.** Use `\( \)` and `\[ \]`. Never `$…$`.
13. **No sets or enumerations.** "Name the four pillars of physics in 1900" is a card you'll keep failing. Give each member its own card with a distinguishing cue, or use one overlapping cloze per item with the others visible. [Wozniak 9–10]
14. **Guard against interference.** When two facts are easy to confuse, like Wien's \(2.898\times10^{-3}\) m·K and Stefan–Boltzmann's \(5.67\times10^{-8}\) W/m²K⁴, put the distinguishing cue in both prompts. Add a contrast card if you mix them up. [Wozniak 11]
15. **Concepts get several small prompts from different angles.** For one idea, card its prediction, its cause, and its resolution, and ask *why* at least once. Take the ultraviolet catastrophe: what Rayleigh–Jeans predicts as \(\lambda\to0\), which classical assumption causes it, and what Planck changed. A why-card needs a non-obvious answer: if the reason is self-evident once the fact is known ("why does a known force direction mean fewer unknowns?"), skip it. When a course fact is an instance of a general principle (field lines ⊥ equipotentials, from \(\nabla f\perp\) level sets), card the principle in `Core` and the mapping ("equipotentials are level sets of \(V\)") instead of the instance. General math that a course uses (vector addition, unit vectors, the dot product, the law of cosines, gradients) is carded **once, as math**: in `Core`, with a `topic::math::…` tag and the math course it belongs to (`course::math251` for Calc III). The course's own cards then cover only the course-specific use, such as "the resultant of coplanar forces" in statics. [Matuschak; Wozniak 17; Dunlosky: elaborative interrogation; golden review; Aman's review, 2026-09-25]
16. **Only card what's understood.** Cards come from the lecture as taught, including the student's annotations and emphasis notes, not from bare slides. If an annotation marks something as confusing, card the resolution as an M-Error trap. [Wozniak 1–2]
17. **Card the limits.** A formula is known when you know how it behaves. Give its limiting cases their own small cards: \(\gamma m\vec u\) as \(u\ll c\) and as \(u\to c\). Every formula card comes with them: what it tends to as each variable goes to 0 or ∞, its asymptotes, and where it stops holding (the Debye length as \(T_e\to0\) and as \(n_e\to0\); when the \(E\times B\) drift fails). A numeric anchor gets a Fermi (order-of-magnitude) twin when the rough value is what gets used in estimates. [golden review; Aman's review, 2026-09-26]
18. **Cue by what's given.** When a quantity has several forms, give each form its own card, cued by the knowns: "power in a resistor, given \(I\) and \(R\)" and "…, given \(V\) and \(R\)", not one card holding both. [golden review]
19. **General over specific.** Card relations, not instances. Skip a particular device's numbers (one battery's short-circuit current), the key step of one exam example, and content the course touches but doesn't use (a linear \(\rho(T)\) model in a circuits lab). [golden review]
20. **Don't card the obvious.** Skip what Aman already understands intuitively, even when the lecture explains it badly, and notation that gets defined wherever it's used (Leon's \(P_n\)). Examples Aman rejected: summing moments or forces ("just how vectors work"), a pin reaction's magnitude from its components, "to show \(S\) isn't a subspace, give one failing case" (proof common sense), and a card that merely combines two cards he already has. Every card costs reviews for years. [golden review; Wozniak 1; Aman's review, 2026-09-25]
21. **A fact in an elaboration gets its own card.** When an `Elaboration` line holds something worth recalling by itself, card it separately, asked from the other side: "isotope that dominates decades-long contamination after Chernobyl" → Cs-137, "most-used diagnostic medical isotope" → Tc-99m. Keep the elaboration as well. Context that only explains the card (a derivation step, "about 3 days of background") stays where it is. [Aman's review, 2026-09-26]
22. **Home domain first, then every connection.** Card an idea where it natively lives. The discipline that defines it sets its first `topic::` tag, its notation, and its framing, even when another course is where it came up:
    - capacitance is electromagnetism;
    - series and parallel rules are circuits;
    - the cross product is linear algebra;
    - entropy is thermodynamics.

    Then connect it:
    - add a `topic::` tag for each other discipline that uses it;
    - add a `principle::` tag for the principle underneath (superposition, conservation of charge…);
    - add every course's lecture tag where it's taught.

    Take both from `config/topics.yaml`. A course's own view of the idea is its own card under that course's domain: the circuits view of a capacitor, \(i=C\,dv/dt\), is circuits. When two courses teach the same idea, it's one card with both course tags, never two cards. More tags and connections are better; more cards are not. [Aman, 2026-09-29]

    That one card is worded in the most fundamental framing: the earliest discipline in `foundations` (`config/topics.yaml`), and among equals the course that's its home (`homes` in `config/courses.yaml`). A projection is MATH 309's linear algebra even if Calc III or statics carded it first. So when a lecture teaches, in a more fundamental framing, something another course already carded, card it anyway, rigorously and in this lecture's framing. mnemosyne holds it as a **merge-up**: Aman's tap rewrites the existing note in place, so it keeps its history and gains every course's tags. The reverse, a less fundamental course re-carding an idea, is dropped, and its tags join the existing note. [Aman, 2026-10-21]
23. **Pronunciation through IPA, reading through the script.** Language cards teach sound with the IPA that `Lang::IPA` built, and keep each language's own writing for reading. [Aman, 2026-10-07]
    - **Every M-Lang card fills `IPA`** with a broad (phonemic) transcription in slashes, stress marked: `/ˈpeɾo/`, `/se me olbiˈdo/`. Write a narrow transcription in brackets only for the sound the card is about, and put it in `Notes`: Spanish `[β ð ɣ]` between vowels, a Hindi aspirated stop `[kʰ]`. Dictionaries mix broad and narrow; don't.
    - **One standard per language**, the one its deck is learned in: Spanish es-MX (seseo, so no /θ/), Hindi standard Delhi Hindi, French standard metropolitan French.
    - **The script stays on the front.** A `Read` card shows the native spelling (Devanagari for Hindi, never romanization alone); the IPA goes on the back. Card the sound where the spelling doesn't give it away:
      - French spelling hides most of it (silent letters, nasal vowels, liaison), so there the IPA is often the point of the card.
      - Spanish spelling and Devanagari are close to one symbol per sound: don't card rules a reader already gets right. Card the exceptions and the sounds English lacks.
    - **Minimal pairs for sounds English merges.** When a language tells apart two sounds that English hears as one, card the contrast as two M-Lang notes, one per word. Each note's `Notes` names the contrast in IPA (canon 14), and both carry the `ipa::` tags of the two sounds. Examples: Spanish pero `/ˈpeɾo/` vs perro `/ˈpero/` (tap vs trill); Hindi ताल `/t̪aːl/` vs टाल `/ʈaːl/` (dental vs retroflex), and काल `/kaːl/` vs खाल `/kʰaːl/` (plain vs aspirated); French tu `/ty/` vs tout `/tu/`.
    - **What the ear learns from is audio.** A word's Listen card needs `TargetAudio`: native recordings where they exist, P5's neural TTS otherwise. Until a word has audio, its note carries the IPA and the minimal-pair contrast, and the Listen card waits for the audio.

### Where the canon comes from

These sources are paraphrased and cited here; none is reproduced.
- Wozniak, *Effective learning: Twenty rules of formulating knowledge* (SuperMemo, 1999). Rules 1, 4, 9–11, and 17 underlie canon 1, 13–16.
- Matuschak, *How to write good prompts* (2020). Prompts should be focused, precise, consistent, tractable, and effortful (canon 1–3); concepts need several angles (canon 15).
- Brown, Roediger & McDaniel, *Make It Stick* (2014): retrieval, spacing, interleaving, elaboration, generation, calibration (PLAN §0.7).
- Dunlosky et al., *Improving Students' Learning With Effective Learning Techniques* (2013). Practice testing and distributed practice rate highest; elaborative interrogation and self-explanation moderate. That's why canon 15 asks *why*, and why the `Elaboration` field exists.
- Nielsen, *Augmenting Long-term Memory* (2018): using SRS for understanding, not only facts.
- **Golden review** (Aman, 2026-09-25): 87 cards reviewed card by card. Most edits asked for splits (fact vs reason, formula vs meaning), limit cards, and terser Backs; the rejections were too specific, too obvious, or niche. The reasons are recorded next to each card in `golden/`.

## Note types and fields

| note_type | fields (keys exactly) | use for |
|---|---|---|
| M-Basic | Front, Back, SpeakFront, SpeakBack, Elaboration, Reverse, Source, SourceLink | facts, results, definitions; `Reverse: "y"` adds the Back→Front card (canon rule 4: symbol↔name, term↔definition only) |
| M-Cloze | Text, Extra, SpeakText, Source, SourceLink | statements where context cues the blank; ≤2 cloze numbers per note |
| M-Estimate | Question, Answer, Anchor, SpeakQuestion, SpeakAnswer, Source, SourceLink | Fermi checkpoints and orders of magnitude; `Anchor` = how to re-derive |
| M-Error | Situation, Trap, Fix, Why, SpeakSituation, SpeakFix, Source, SourceLink | mistakes from graded work or chat |
| M-Lang | Lang, Target, Meaning, SpeakMeaning, IPA, Notes, Produce, Read, Source, SourceLink | language items; sentence-first; `Produce: "y"` enables the production card, `Read: "y"` the reading card |
| M-IPA | Symbol, Description, ExampleWord | IPA symbols; the audio is attached by the pipeline |

Do not fill `MnemosyneID`, `AudioFront`, `AudioBack`, `TargetAudio`, or other audio fields. The pipeline owns them.

## Tags

- `course::<code>::l<NN>-<slug>`: lowercase course code, lecture zero-padded, then a kebab-case slug of the lecture title, e.g. `course::nuen201::l07-quantum-theory-of-light`. The sidebar then shows which lectures already have cards, and `tag:course::nuen201::l07*` finds one lecture.
- **One idea, one note, every course's tag.** When a lecture teaches something already in Anki (the gas constant in chemistry and in thermal physics), don't write a second card: the existing note gets this lecture's `course::` tag too, which also puts it in this course's exam deck. The pipeline does this by itself for dropped duplicates. In a session, name the existing note in `why` and skip the card. [Aman, 2026-09-26]
- `topic::<domain>::<sub>`: from `config/topics.yaml`, the home domain first (canon 22), e.g. `topic::nuclear::fission`.
- `principle::<name>`: what the idea rests on, e.g. `principle::conservation-of-charge`, `principle::superposition`. Also from `config/topics.yaml`.
- `type::fundamental|result|formula|definition|trap|anchor|claim`. `claim` is a research paper's own result, attributed and dated ("Research papers" below), never an anchor.
- `src::slides|notes|chat|syllabus|homework|exam|obsidian|telegram`.
- `src::book::<book>::<where>` for a textbook reading, in place of the other `src::` tags: the book's slug from `config/books.yaml`, then `s2-3` for section 2.3, `ch2` for a chapter, or `p39-47` for pages, e.g. `src::book::chen::s2-3`. A reading has no lecture, so a course textbook's cards carry a bare `course::<code>` (deck `Now`), and self-study cards carry no course tag (deck `Core`).
- `src::paper::<slug>` for a research paper, in place of the other `src::` tags. The pipeline gives the slug (first author, year, short title, e.g. `src::paper::smith-2025-bohm-diffusion-in-stellarators`). A self-study paper's cards go to `Core` with no course tag; a paper a course assigned carries a bare `course::<code>` (deck `Now`).
- `lang::es|hi|fr`, plus `ipa::place::…` and `ipa::manner::…` for M-IPA.

The pipeline adds `gen::ai`, `mode::*`, and `status::probation`; don't add them yourself.

## Payload schema

```json
{
  "source": {"kind": "slides|notes|chat|syllabus|homework|exam|obsidian|telegram|reading|paper",
             "course": "NUEN201", "lecture": 7, "ref": "slide 12", "link": null},
  "cards": [
    {"note_type": "M-Basic",
     "deck": "Now|Core|Lang::Spanish|Lang::Hindi|Lang::French|Lang::IPA|Reservoir",
     "fields": {"Front": "...", "Back": "...", "SpeakFront": "...", "SpeakBack": "...",
                "Elaboration": "...", "Source": "NUEN201 L07 slide 12", "SourceLink": ""},
     "tags": ["course::nuen201::l07-nuclear-fission", "topic::nuclear::fission", "type::anchor", "src::slides"],
     "mode": "audio|visual",
     "checks": [{"quantity": "energy released per U-235 fission", "value": "200 MeV", "tolerance": "5%"}],
     "confidence": 0.0}
  ],
  "questions": [
    {"page": 7, "asked": "why ln here and not 1/r?", "about": "cylindrical capacitor", "kind": "mechanism",
     "answer": "...", "sharper": "...", "beyond_course": false,
     "card": {"note_type": "M-Basic", "deck": "Now", "fields": {"...": "..."}, "tags": ["..."], "mode": "visual"},
     "no_card": null}
  ],
  "skipped": [{"what": "...", "why": "..."}],
  "prerequisites": [{"concept": "Bohm diffusion", "search": "Bohm diffusion coefficient", "why": "the paper's baseline"}]
}
```

`prerequisites` is for research papers only (below); leave it out otherwise.

`checks` are for numbers only. Each `quantity` is a pint expression that computes the value from numbers, units, and CODATA or `data/anchors.yaml` names, e.g. `"1 F * 1 mm / vacuum electric permittivity"`. A card with no number has no checks.

## Aman's marks and questions

Aman annotates slides by hand. How he marks things is in `config/annotations.yaml`: **questions** are handwritten lines that ask something, usually ending in "?". **Must-card** items have a red star, or the words "card" or "card idea" next to them.
- **Must-card:** every item he marked must-card becomes at least one card, or a `skipped` entry that names it and says why.
- **Questions:** every question he wrote goes in `questions`, marked or not. Each entry has:
  - `asked`: his words, transcribed. Mark an unreadable word `[?]`.
  - `about`: the slide's topic, in a few words.
  - `kind`: one of these.
    - `clarify`: what does this mean or say?
    - `mechanism`: why or how does it happen?
    - `predict`: what if a variable changes, or in the limit?
    - `connect`: how does it relate to something else, possibly in another course?
    - `apply`: where or how is it used?
    - `verify`: how do we know it's true?
  - `answer`: two to four sentences from first principles and the lecture. Say plainly when the slides don't settle it.
  - `sharper`: the same curiosity asked better: specific, answerable, and one step deeper, e.g. "why ln?" → "why does the potential of a line charge go as ln r, when a point charge's goes as 1/r?". Never condescend: a question asked is already good.
  - `beyond_course`: true when it reaches past the course into research, history, or engineering practice.
  - `card`: a card is the default. A question marks where his understanding caught, so what its answer teaches is
    among the most useful cards there are. Card the answer's core as one atomic card (canon rules apply): usually
    the why or how it settles, tested as the mechanism, not the question restated and not the whole answer pasted
    in. Carry the lecture tag like any card from the lecture.
  - `no_card`: only when `card` is null, a few words saying why. Null is for two cases:
    - the answer leaves nothing lasting to recall: logistics, exam format, "is this on the exam?";
    - a card already holds it, in this payload or among the lecture's existing cards: name that card's prompt.
  - A question he marked must-card ("card", "card it", "card idea" beside it) always gets a `card`.

The question itself isn't a card. It goes to his question list, the lecture's note in Obsidian, and, for
`beyond_course` ones, Hyperion. Its card is what the answer teaches.

Deck rule: current-semester course material goes to `Now`; timeless anchors, math results, and conversions go to `Core`, even when they come from a lecture.

## Research papers

Papers are new knowledge: results that may be revised, measured once, or contradicted next year. The canon still
applies; these rules come on top.

- **Attribute and date every claim.** A paper's result is its authors' claim, not settled fact. Put who and when in
  the prompt or the answer ("Smith et al. (2025) report…", "as of 2025, the best measured…"), tag it `type::claim`,
  and cite figure, table, or section in `Source` ("Smith et al. 2025, Fig. 3"). Then a later paper that disagrees
  adds a card instead of making an old one quietly wrong. Background the paper restates (textbook physics) is carded
  as usual, and is often already in his deck: check the cards listed as existing.
- **Card the core, not the paper.** Typical yield is 5–12 cards. In order of value:
  1. the question the paper answers, and why it was open;
  2. the key idea or mechanism: why the approach works (canon 15: more than one angle);
  3. the headline result, with its uncertainty and the conditions it holds under;
  4. what it improves on, and by how much (the prior best, or the standard assumption it overturns);
  5. a method or trick worth reusing elsewhere;
  6. the main limitation or caveat, in the authors' words or the obvious one they skip.

  Skip incidental parameters, the related-work tour, and most figures. A new term the paper coins is carded only if
  it's likely to outlive the paper.
- **Numbers as reported.** Give the value with its uncertainty and conditions ("\(1.5\pm0.2\) at 10 keV"). Add
  `checks` only where units can be verified; the value itself is the paper's and can't be anchored, so these cards go
  to review, as they should. Never `type::anchor`, and never round away the uncertainty.
- **Home domain as usual** (canon 22): a frontier result still lives somewhere in `config/topics.yaml`
  (`topic::plasma::magnetic-confinement`). A new leaf is fine; say so in the card's `why`.
- **Prerequisites.** Return a top-level `prerequisites` list (3–8 items): established ideas the paper takes for
  granted that a reader must already hold to follow it. For each, give `concept`, `search` (the words a textbook
  index would use, to find his cards and his books' sections), and `why` (what the paper uses it for, in a few words).
  Don't card prerequisites unless the paper teaches them; mnemosyne checks them against his cards and books.
- **His marks and questions** work as on slides. A question about where the field is going is `beyond_course`.

## Golden examples

**Anchor (M-Basic).** This is from Aman's deck; keep this shape:
```json
{"note_type":"M-Basic","deck":"Core",
 "fields":{"Front":"1 electron-volt in joules (2 sig figs)","Back":"\\(1.6\\times10^{-19}\\) J",
  "SpeakFront":"one electron volt, in joules, to two significant figures",
  "SpeakBack":"one point six times ten to the minus nineteen joules",
  "Elaboration":"Charge of one electron times one volt: \\(e\\cdot 1\\,\\text{V}\\)."},
 "tags":["topic::metrology::energy","type::anchor"],"mode":"audio",
 "checks":[{"quantity":"1 eV","value":"1.602e-19 J","tolerance":"2%"}]}
```

**Fermi checkpoint (M-Estimate):**
```json
{"note_type":"M-Estimate","deck":"Core",
 "fields":{"Question":"Fissions per second to produce 1 W of thermal power","Answer":"\\(\\approx 3.1\\times10^{10}\\) fissions/s",
  "Anchor":"1 W ÷ (200 MeV × 1.6e-13 J/MeV)",
  "SpeakQuestion":"fissions per second to produce one watt of thermal power",
  "SpeakAnswer":"about three point one times ten to the ten fissions per second"},
 "tags":["topic::nuclear::fission","type::anchor"],"mode":"audio",
 "checks":[{"quantity":"1 W / (200 MeV)","value":"3.12e10 1/s","tolerance":"3%"}]}
```

**Result with speakable math (M-Basic):**
```json
{"note_type":"M-Basic","deck":"Core",
 "fields":{"Front":"\\(\\dfrac{d}{dx}\\cot x\\)","Back":"\\(-\\csc^2 x\\)",
  "SpeakFront":"derivative with respect to x of cotangent x","SpeakBack":"negative cosecant squared x",
  "Elaboration":"Quotient rule on cos over sin; numerator collapses to minus one."},
 "tags":["topic::math::calculus::derivatives","type::result"],"mode":"audio","checks":[]}
```

**Error card (M-Error):**
```json
{"note_type":"M-Error","deck":"Now",
 "fields":{"Situation":"Computing mass defect from atomic masses for a β⁻ decay",
  "Trap":"Subtracting electron masses separately — atomic masses already include the electrons",
  "Fix":"Q = [M(parent) − M(daughter)]c² using atomic masses directly",
  "Why":"Electron counts balance automatically for β⁻ when atomic masses are used.",
  "SpeakSituation":"computing the Q value of a beta minus decay from atomic masses",
  "SpeakFix":"Q equals parent atomic mass minus daughter atomic mass, times c squared; no separate electron masses"},
 "tags":["course::nuen201","topic::nuclear::decay","type::trap","src::homework"],"mode":"audio","checks":[]}
```

**Spanish (M-Lang), sentence-first:**
```json
{"note_type":"M-Lang","deck":"Lang::Spanish",
 "fields":{"Lang":"es-MX","Target":"Se me olvidó la tarea en casa.","Meaning":"I (accidentally) left my homework at home.",
  "SpeakMeaning":"I accidentally left my homework at home","IPA":"/se me olbiˈdo la taˈɾea en ˈkasa/",
  "Notes":"Accidental 'se': the thing forgotten is the subject; me = to whom it happened.","Produce":"y"},
 "tags":["lang::es","topic::grammar::accidental-se"],"mode":"audio","checks":[]}
```

## Golden set

`golden/` holds one reference card set per lecture, including what was deliberately skipped. When carding a course, read that course's file first:
- Cards with a `review` are Aman-approved examples. `review.before` shows what an edit changed.
- `rejected` cards are anti-examples, each with Aman's reason. `superseded` cards were approved but restructured (usually split); `replaced_by` points at their replacements.
- Cards without a `review` are still drafts and are not examples yet. A file is `"status": "reviewed"` once every card has one.

## Anti-examples (from the legacy deck — do not produce these)

| Legacy card | Problem | Fix |
|---|---|---|
| "Arc length" ↔ full 3D parametric integral (reversed) | Reversed formula→name is useless load. The cue "Arc length" is also ambiguous (which curve, what parametrization?). | "Arc length of \\(\\vec r(t)\\) from a to b" → \\(\\int_a^b \\lVert\\vec r\\,'(t)\\rVert\\,dt\\); one direction only |
| "wavelength of gamma ray" → \\(10^{-15}\\)–\\(10^{-11}\\) m | Vague cue with no precision stated | "Order-of-magnitude wavelength range of gamma rays (m)" |
| Ten cards whose front is "What molecular geometry is this?" + image | Duplicate fronts; unusable in audio | Visual-only, with the distinguishing content in the prompt ("AX₂E₂ geometry → bent"), or as text: "Geometry of AX₂E₂" |
| "Atom vs nucleus size" → two numbers | Two facts behind one cue | Two anchor cards, or a contrast card asking for the *ratio* (~10⁵) |
| Newton's 2nd law cloze with two unrelated blanks | Second blank is guessable from the first | One blank per idea; move the \\(d\\vec p/dt\\) generalization to its own card |

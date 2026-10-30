# Mnemosyne — Plan

> Fluency as a fallback for first-principles thinking: faster than the tortoise, sturdier than the hare.

Mnemosyne is a Helios module that turns Anki into a dumb, excellent review surface. All the intelligence lives on the Obelisk: generation, tagging, exam targeting, leech repair, audio, and analytics.

Budget: **~1 hour/day, fragmented, mostly walking** (iPhone + AirPods + 8BitDo Micro).

Language priorities:
- **Spanish:** heavy.
- **Hindi, French:** light.
- **Arabic, Mandarin:** parked.

---

## 0. Principles

1. **Creation must never be the bottleneck.** The default is auto-generate, auto-approve, and prune after the fact. Human review is opt-in per source.
2. **Structure over volume.** Decks set *scheduling*. Tags carry *meaning*. Fields carry *modality*.
3. **Every card is atomic, has one clear cue, and is effortful.** The canon lives in `.claude/skills/mnemosyne-cards/SKILL.md`, and it is the only place card-writing rules are defined.
4. **Numbers are verified by machine, never trusted from an LLM.**
5. **Audio-first where it's natural** (languages, arithmetic, constants, ear training). **Visual-only where it isn't** (graphs, ASL, geometry).
6. **Support derivation; don't replace it.** Cards hold anchors, key moves, and traps. Full derivations are a separate ritual.
7. **Make It Stick, operationalized:**
   - retrieval → every card
   - spacing → FSRS
   - interleaving → mixed walk deck
   - elaboration → `Elaboration` field
   - generation → produce-direction language cards and error cards
   - calibration → error cards plus speed metrics
   - reflection → weekly report

## 1. Current state

See `docs/audit-2026-09-23.md` for the numbers. In short, the problem at the start was structure, not volume:
- decks used as a taxonomy, most of them empty;
- duplicate decks, tags, and notes;
- an overdue backlog, and reviews that came in bursts before exams;
- most notes contain MathJax, and FSRS-6 was already on.

The fix is restructuring plus a habit loop.

## 2. Architecture

```
 iPad / iPhone ──Share Sheet Shortcut──▶ Paperless-ngx ──(poll tag `mnemosyne`)──┐
 Telegram (helios-agent) ─────────────────────────────────────────────────────────┤
 Claude (claude.ai skill / Claude Code) ──helios-mcp: mnemosyne_propose_cards─────┤
 Obsidian vault (> [!card] callouts, via Syncthing) ──────────────────────────────┤
                                                                                  ▼
                                  ┌───────────────── mnemosyne (Obelisk) ────────────────┐
                                  │ ingest → extract → propose(LLM) → verify → dedupe    │
                                  │ → speakify → approve policy → write → project        │
                                  │ scheduler: 04:15 daily jobs, 5-min pollers            │
                                  │ state: Ledger tables  | metrics: InfluxDB → Grafana   │
                                  └──────────────┬───────────────────────────────────────┘
                                                 │ internal network only
                                  anki-core (vendored raslab/anki-sync-mcp)
                                  headless collection copy ⇄ self-hosted sync server (helios-anki-sync)
                                                 ⇅
                       AnkiMobile (iPhone: walking) · AnkiMobile (iPad) · Anki Desktop (ThinkPad)
```

**Components**

| Component | What | Notes |
|---|---|---|
| `anki-core` | Fork of `raslab/anki-sync-mcp` | The only server-side writer. Serialized, idempotent, sync-before/after-write. Destructive, schema, and full-sync flags OFF. |
| `mnemosyne` | Python 3.12 service (FastAPI + APScheduler worker) | Ingest, generation, verification, jobs, HTTP inbox. |
| helios-mcp tools | `mnemosyne_*` | Cloud instance: propose, search, and stats only. Never direct writes. |
| helios-agent | Telegram commands | Capture, approvals, exam setup, weekly report. |
| Ledger tables | `card_draft`, `card_source`, `course`, `exam`, `ingest_job` | Drafts live here, **not** in Anki. |
| InfluxDB `mnemosyne` bucket | Daily metrics | Grafana dashboard. |

**Invariants.** These are repeated in CLAUDE.md, and nothing may break them.
- One process per collection copy. Desktop, phone, and anki-core each own their own copy, and the self-hosted sync server on the Obelisk (`helios-anki-sync`, in use since 2026-08-25) reconciles them. AnkiWeb isn't used.
- **Note types and templates change only on Anki Desktop** in a deliberate session, because schema changes force a full sync. anki-core never gets schema flags.
- Every mnemosyne-created note has a `MnemosyneID` (UUIDv7). All writes are upserts keyed on it.
- anki-core does its daily deck jobs only at **04:15** (rollover is 04:00) to minimize sync races with the phone.

## 3. Collection design

### 3.1 Decks and presets

| Deck | Preset | Desired retention | New/day | Max reviews/day | Contents |
|---|---|---|---|---|---|
| `Now` | Now | 0.90 | governed | 400 | Current-semester course cards |
| `Core` | Core | 0.90 | governed | 400 | Fluency core: arithmetic, constants, conversions, Fermi anchors, math results, idioms |
| `Lang::Spanish` | Lang-major | 0.90 | governed | 400 | |
| `Lang::Hindi`, `Lang::French` | Lang-minor | 0.88 | governed | 150 | |
| `Lang::IPA` | Core | 0.90 | governed | — | |
| `Reservoir` | Reservoir | 0.85 | 0 | 40 | Past courses, low-priority backlog |
| `~Walk::Due`, `~Walk::New` | filtered | — | — | — | Rebuilt daily by mnemosyne |
| `~Exam::<id>` | filtered | — | — | — | Exists only in exam windows |

"Governed" means the load governor (§5.3) sets the value. When a semester ends, `Now` cards move to `Core` or `Reservoir` by rule: `type::fundamental` → Core, everything else → Reservoir. Tags are unchanged.

### 3.2 Note types (freeze in Phase 1)

Shared conventions:
- Math uses MathJax `\( \)` and `\[ \]`. Never `$`. Never `[latex]`.
- IPA font `_CharisSIL.woff2` and CJK/Devanagari/Arabic Noto subsets live in media with an `_` prefix.
- Generated media filenames use the prefix `mn_`.
- Every type carries `Source`, `SourceLink`, and `MnemosyneID`.
- The definitions (fields, templates, CSS) live in `desktop/notetypes/`. They are created on Desktop from the debug console with `scripts/anki_apply.py` `notetypes()`, which only creates missing types. Changing an existing type is a separate, deliberate desktop session.
- Every `tts` tag sits inside a `{{#Speak…}}` conditional, so a card with no speech emits no empty audio tag.

| Type | Fields | Templates | Audio |
|---|---|---|---|
| **M-Basic** | Front, Back, SpeakFront, SpeakBack, AudioFront, AudioBack, Elaboration, Reverse (flag), Source, SourceLink, MnemosyneID | Card 1 · Card 2 (Back → Front; only if `Reverse` non-empty) | pre-rendered if present, else `tts` on Speak* |
| **M-Cloze** | Text, Extra, SpeakText, Source, SourceLink, MnemosyneID | Cloze | `{{tts en_US:cloze:SpeakText}}` front / `{{tts en_US:cloze-only:SpeakText}}` back; SpeakText uses the same cN numbering |
| **M-Estimate** | Question, Answer, Anchor, SpeakQuestion, SpeakAnswer, Source, SourceLink, MnemosyneID | Card 1 | as M-Basic |
| **M-Error** | Situation, Trap, Fix, Why, SpeakSituation, SpeakFix, Source, SourceLink, MnemosyneID | Card 1: "Situation → what's the trap?" | as M-Basic |
| **M-Lang** | Lang, Target, TargetAudio, Meaning, SpeakMeaning, IPA, Notes, Produce (flag), Read (flag), Source, SourceLink, MnemosyneID | Listen (audio → meaning; only if `TargetAudio` present) · Produce (meaning → say target; only if `Produce` non-empty) · Read (text → meaning; only if `Read` non-empty) | TargetAudio pre-rendered; meaning via tts |
| **M-IPA** | Symbol, SoundAudio, Description, ExampleWord, ExampleAudio, Source, SourceLink, MnemosyneID | Symbol → sound + description · Sound → symbol | native |
| **M-Ear** | PromptAudio, Answer, SpeakAnswer, Source, SourceLink, MnemosyneID | Card 1 | native (generated intervals/chords) |
| **M-Sign** | Video, Gloss, Notes, Source, SourceLink, MnemosyneID | Gloss → sign · Sign → gloss | none, always `mode::visual` |

Template rule for walking: the back side re-renders the question **text** but does not replay question audio. Don't use `{{FrontSide}}` on audio types. Set `speed=` on tts tags; start at 1.15 for English.

Existing `Basic` and `Cloze` notes migrate to M-Basic and M-Cloze via Change Note Type in one desktop session (one full sync). The 171 "Basic (and reversed)" notes map to M-Basic:
- 87 keep Card 2 with `Reverse: y`: SI prefixes, polyatomic ions, fractions↔decimals, term↔definition, foreign phrases, and named 2×2 matrices.
- 78 formula→name reversals lose it. That's decided and listed in `docs/runs/2026-09-23-p1.md`.

Legacy notes get a MnemosyneID too: a UUIDv7 whose timestamp is the note's creation time, derived deterministically from the nid.

### 3.3 Tag taxonomy

Tags are lowercase kebab-case, hierarchical with `::`.

```
course::nuen201            course::nuen201::l07-quantum-theory-of-light          course::nuen201::u::relativity
topic::physics::em         topic::math::linear-algebra   topic::nuclear::reactor-types
type::fundamental | type::result | type::formula | type::definition | type::trap | type::anchor
src::slides | src::notes | src::chat | src::syllabus | src::homework | src::exam | src::obsidian | src::telegram | src::legacy
gen::ai | gen::human                 mode::audio | mode::visual
lang::es | lang::hi | lang::fr       ipa::place::retroflex  ipa::manner::nasal
status::probation                    (auto-approved AI cards, first 2 reviews)
```

Normalization map for legacy tags:

| Legacy tag | New tag |
|---|---|
| `Course::CHEM107::Exam2` | `course::chem107::exam2` |
| `Course::CHEM-107` | `course::chem107` |
| `MATH-251` | `course::math251` |
| `PHYS-206` | `course::phys206` |
| `ai-generated` | `gen::ai` |
| `Math::…` and `Science::…` deck-mirror tags | `topic::…` (`Science::` dropped; segments kebab-cased, `&` → `and`) |
| `Languages::Hindi::Devanagari-Script::X` | `lang::hi::script::x` |
| `Languages::Phonetics::X` | `ipa::place::x` or `ipa::manner::x` (Occlusive → `plosive`); bare `Languages::Phonetics` dropped |
| `Languages::Random-Foreign-Phrases`, `Languages::Latin` | `topic::idioms` |
| `type::missed-question` | `type::trap` |
| `type::*` | unchanged |
| *(every existing note)* | `src::legacy` added |

The rules live in `scripts/retag.py`. The reviewed dry run is `docs/runs/2026-09-23-retag.md`.

### 3.4 Migration (Phase 1, desktop, one sitting)

Steps 1–2 (back up, re-optimize FSRS, Check Media) were done in the P0 session.

Everything else is generated as reviewed dry-run diffs:
- `scripts/migrate.py` writes `docs/runs/2026-09-23-p1.{json,md}`.
- `scripts/retag.py` writes the retag diff.

They're applied from the Anki Desktop debug console with `scripts/anki_apply.py`. anki-core doesn't exist until P2, and the debug console runs inside Anki's own process, so invariant 1 holds. The order and the expected counts are in `docs/desktop-sessions/2026-09-p1-migration.md`:

1. Create the note types.
2. Set up presets and decks (`config/decks.yaml`).
3. Delete duplicates. The rule: keep the copy with review history, else the Aug 2026 copy.
4. Change the note types.
5. Move cards.
6. Retag.
7. Suspend one order of each multiplication pair.
8. Reposition the kept new multiplication facts, easiest first.
9. Set fields: MnemosyneID, Reverse, and the deterministic speech from `scripts/speakify.py`.
10. Add `mode::audio` / `mode::visual` tags.
11. Delete the legacy decks.
12. Full sync, deliberately uploaded from desktop.
13. Verify on AnkiMobile with the controller.

Deterministic speakify covers arithmetic, the unit circle, SI prefixes, and polyatomic ions. That's 669 cards `mode::audio`, 498 of them active once the multiplication duplicates are suspended.

## 4. Ingestion and generation

### 4.1 iPad lecture path (primary; no Canvas token)

Paperless-ngx is the inbox, because it already OCRs, archives, and backs up.

**The "→ Mnemosyne" Shortcut** works on iPad and iPhone and accepts PDFs and images from the Share Sheet (GoodNotes/Notability export, Files, Photos). It:
1. Picks the course from a menu fed by `GET mnemosyne/courses`, with a hardcoded fallback list.
2. Picks the kind: `slides | notes | syllabus | review-sheet | graded-homework | graded-exam`.
3. Asks for the lecture or assignment number.
4. POSTs to Paperless `/api/documents/post_document/` over Tailscale, with:
   - title `NUEN201 | L07 | slides`
   - tag `mnemosyne`
5. Shows a notification.

The fallback path is dropping files into `consume/mnemosyne/<COURSE>/` with `PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS`, with the lecture number taken from the filename.

**What Paperless already holds (checked 2026-09-24).** Every Fall-26 lecture is already filed with:
- `document_type` of `Lecture`, `Assignment`, `Exam`, or `Syllabus`;
- a course tag (`NUEN-201`) and the semester tag (`Fall-26`);
- a title like `NUEN 201 L05 - Lorentz Transformation and Relativistic Energy`.

That's enough metadata, so the worker reads it directly. The `COURSE | L07 | kind` title convention is only needed for files the Shortcut adds. `config/courses.yaml` maps course tags to course codes.

About 22 lectures and every syllabus are waiting. They'll be processed when P3 lands. On 2026-09-24 we decided against an interim intake path: phase order holds. *Update 2026-09-26:* once P3 was live, Claude carded the backlog lectures for the first exam cluster in session and Aman reviewed the held cards. The syllabi became exam maps, and new uploads go through the pipeline.

**Two inputs worth more than the model's reading of the slides:**
- **Emphasis.** What the professor stressed or called exam material, and what was confusing. Add it as a Paperless *note* on the document, or as stars/highlights in the GoodNotes export. The worker passes notes and annotations to the model as priority signals.
- **Graded work.** A returned exam (`document_type: Exam`) is in by default, like a lecture; an `Assignment` needs the `graded` tag, since a blank one is only context. Each gets a debrief (§4.6).

**Opt-in** (decided 2026-09-24, so tokens aren't spent on documents that don't matter):
- **Lectures are in by default** from P3 go-live onward. Tag a lecture `mnemosyne-skip` to leave it out.
- **Everything else needs the `mnemosyne` tag.** That includes graded work, readings, and review sheets, and also backlog lectures filed before go-live.

**Worker:**
- Polls Paperless every 5 minutes for current-semester documents (`paperless_semester_tag`) in a configured course that are opted in by the rules above and don't yet carry `mnemosyne-done` or `mnemosyne-error`.
- Before generating, looks up existing cards tagged with the same lecture (`course::X::lNN*`) and passes them in. Re-submitting a lecture then extends or refines its cards instead of duplicating them.
- Downloads the **original** file through the Paperless API. The token comes from Docker secrets. The laptop holds no copies.
- Sends the PDF itself to Claude (text layer plus page images). Paperless OCR text is only a fallback, since Tesseract is weak on handwriting.
- Retags the document `mnemosyne-done` or `mnemosyne-error`.

**Annotations and questions** (2026-09-29):
- **How Aman marks slides:** `config/annotations.yaml` records his existing habits: a red ★ or "card idea" means must-card, and a handwritten question is a question.
- **Must-card:** every must-card item becomes a card or a named `skipped` entry.
- **Questions:** each handwritten question comes back in the payload's `questions` with a short answer, a sharper version, a kind, and an optional card.
  - They're kept in the Ledger (`question`), shown on the review page's Questions tab, and mirrored into the lecture's own Paperless stub in `Sources/Paperless/`, as a block below the ingester's marker. Aman ticks them there; the stub links the day and the course. (A separate note per lecture, used 2026-09-29 to 10-04, was retired so the vault doesn't grow a second file per lecture.)
  - Questions that reach past the course go to Hyperion, at most 2 per lecture.
  - They're counted by kind in the Sunday report, so the mix can shift from clarifying toward why, what-if, and connection.
  - The tutor offers the open ones.
- **Backfill:** `POST /questions/extract/{doc}` reads a filed lecture for questions only.

**Decks sent again** (2026-10-04):
- **Fingerprints:** every carded lecture PDF's pages are fingerprinted (`deck_page`): a hash of each page's printed text, plus a small grayscale render that shows new handwriting.
- **The same deck:** an upload is a copy of an earlier one when they share at least half the slides of the smaller (at least 3). This holds whatever lecture number it was sent under. Handwritten notes, which have no text layer, match by lecture number. Slides never match notes.
- **Replacing the old copy:** the new copy replaces it in Paperless, with notes and tags carried over and the old copy sent to the trash. The title spans the lectures (`PHYS 207 L07-L08 - Capacitors`).
  - The old copy's cards get the new SourceLink, patched field by field in Anki.
  - Its questions and their vault note move too.
- **Carding only what changed:** a page is carded only if no carded copy in the course has the same slide unmarked, so recap slides a professor carries into the next lecture are skipped unless Aman writes on them. New cards take the tag of the lecture the copy was sent for.
- **A byte-identical file** adds nothing. Its title just gains the new lecture number.

**Readings** (2026-10-04): textbook sections, from `config/books.yaml`.
- **Where:** a section from the book's PDF outline (`2.3`), a chapter (`ch 2`), or pages (`p 39-47`), at most 45 pages at a time.
- **From the ebook:** the Calibre library is mounted read-only. The review page's **Read** tab picks a book and section (or `POST /readings`), and those PDF pages are carded.
- **From Aman's own copy:** he scans the pages into Paperless and gives them the `mnemosyne` tag, now or whenever he wants them carded.
  - Titled like "Chen 2.3", the poller cards it within 5 minutes. Otherwise it waits on the Read tab, which asks for the book and section and retitles the scan.
  - His marks on the scan lead (canon 16). When the book is in Calibre, the ebook's text for the section rides along as a clean reference.
  - Handwritten questions are kept like a lecture's.
- **Cards:** tagged `src::book::<slug>::<where>` (e.g. `src::book::chen::s2-3`). Self-study books go to `Core` with no course tag; a course textbook (`course:` in books.yaml) goes to `Now` with a bare `course::<code>`.
  - Readings are always held for review (`approval.yaml`).
  - A section is carded once (ingest_job kind `reading`) unless Aman asks again.
- **Outlines of every shape** (2026-10-06): chapters may be numbered, unnumbered over a numbered first section (Griffiths E&M), "Lecture N" (Susskind), or sit beside a part heading numbered like them (Griffiths QM). books.yaml corrects what a PDF gets wrong: `offset` (no page labels), `outline_shift` (bookmarks a page early), `number_sections` (sections without numbers, University Physics), `last_page` (something else merged after the book), and `text` (a misjudged text layer).
- **A book several courses share** maps chapters to courses (`course: {PHYS207: [21-32], PHYS221: [17-20, 33-36]}`); a reading's course follows its chapter, and chapters none covers are self-study.

**Research papers** (2026-10-06): `src/mnemosyne/papers.py`.
- **In:** a Paperless document of type Paper with the `mnemosyne` tag (the Shortcut's `paper` kind, course optional). The poller cards it within 5 minutes; `POST /papers/{doc_id}` does it now (`again` redoes it).
- **Cards:** the canon's "Research papers" section: attribute and date claims (`type::claim`), card the core (question, key idea, headline result with uncertainty, what it beats, a reusable trick, the caveat), numbers as reported and never anchored. Tagged `src::paper::<slug>` (slug from the Paperless title, plus the year). Self-study papers go to `Core`; one with a course tag to `Now` with a bare `course::<code>`. Always held (`approval.yaml`).
- **Copies:** page fingerprints are kept under `paper:<slug>`, so a copy filed again under the same title is carded only where it's new or newly marked.
- **Prerequisites:** the model lists what the paper assumes; each is checked against his cards (`learner.search`) and, when missing or weak, his books (the books index). They're kept in the `paper` table (migration 0013), shown as "Before you read" in the paper's stub, and listed in the learner profile's `papers`.
- **Theses** are books: Calibre and `config/books.yaml`.

**Books for the tutor** (2026-10-06): `src/mnemosyne/library.py`, read-only.
- **Status:** each PDF is judged from a sample of pages: born digital or scanned; text `clean`, `math-garbled` (math glyphs lost: TeX fonts, Mathematica exports), `ocr`, or `none`; and what its outline offers. Every result carries it, and Calibre carries it as `ai:*` tags (`scripts/calibre_tags.py`, dry run by default, touching only its own tags).
- **Tools:** `/books` (list, or one book's outline), `/books/search` (full text, SQLite FTS5 in a cache volume, warmed at startup), `/books/{key}/read` (printed text, printed page numbers, a citation, and page images when the text can't carry the math), and `/books/{key}/problems` (each book's problem style, difficulty marks, University Physics' Section groupings). MCP: `mnemosyne_books`, `mnemosyne_book_search`, `mnemosyne_book_read`, `mnemosyne_book_problems` on every instance.
- **Use:** the `mnemosyne-books` skill: cite every use, quote short, take math from page images unless the text is clean, and give practice problems one at a time in review sessions. The tutor links it.

### 4.2 Claude skill path

`.claude/skills/mnemosyne-cards/` is both the pipeline's prompt source and an uploadable claude.ai skill.
- **Mid-conversation** ("mnemosyne this", "distill into cards"): the skill applies the canon.
  - If the `mnemosyne_propose_cards` tool is connected (helios-mcp over the tailnet, e.g. Claude Code), it calls it. Public exposure for claude.ai is deferred to a security review (decided 2026-09-25).
  - Otherwise it emits a fenced `mnemosyne-cards` JSON block. Forward that to Telegram, or paste it into `POST /inbox`.
- **On iPad:** attach the lecture PDF in the Claude app and say "mnemosyne this". This is the same result as §4.1, with conversation as an added option.

### 4.3 Telegram

| Command | Action |
|---|---|
| `/card <text or photo or voice>` | Draft cards; voice goes through Whisper first |
| `/es <sentence>` | Spanish sentence mining → M-Lang |
| `/hi <sentence>` | Hindi sentence mining → M-Lang |
| `/fr <sentence>` | French sentence mining → M-Lang |
| `/exam` | Exam status |
| `/due` | Due counts |
| `/report` | Weekly report on demand |

Approval batches arrive as inline keyboards: **Approve all · Review · Reject**.

### 4.4 Obsidian

- **Watcher:** `> [!card]` callouts anywhere in the vault become drafts. They are keyed by a content hash, so edits update the card instead of duplicating it.
- **Tiers:** respect the `ai:` frontmatter tiers; `ai: none` notes are never read.
- **Projection (nightly, one-way):**
  - `Sources/Lectures/<COURSE>/<COURSE>-L07.md` gets a summary, the card list with retrievability, and the Paperless link.
  - Source notes get a `<!-- mnemosyne:start -->…<!-- mnemosyne:end -->` block with card count, mean R, and leeches.
  - Nothing outside the markers is ever touched.

### 4.5 Syllabus → exam map

**Exam dates come from the calendar.** The Helios Ledger's `calendar_event` table already holds every Fall-26 midterm, in the **Exams/Assignments** calendar, with titles like `PHYS-207-210 Exam II`. The same event appears once per Google account, so rows are deduplicated by title plus start time. Finals aren't on the calendar yet.

`kind=syllabus` goes through the LLM only for what the calendar can't give: the unit→lecture-range mapping for each exam. The resulting `exam` rows are confirmed via Telegram. Lecture cards get `course::X::u::<unit>` tags from this map.

**Quizzes and changes flow in automatically.** Helios's calendar ingester already pulls the Canvas calendar feed ("Aman Sharma Calendar (Canvas)") into the Ledger, and quizzes appear there when instructors post them, usually no more than a week ahead. The 04:15 job re-reads `calendar_event` every day and upserts `exam` rows:
- `kind: exam | quiz`;
- a new or moved event re-plans its deck the same night;
- quizzes default to covering the lectures since the last assessment, and use a shorter window (T−5).

**Build deadline** (set 2026-09-24): the first exam cluster runs Sep 30 – Oct 6, before P2/P3 can land. The second runs Oct 21–30: PHYS 207 II on 10/21, PHYS 221 II on 10/26, NUEN 201 II on 10/29, MEEN 221 II on 10/30. With exam decks starting at T−10, P2 + P3 should be live by **2026-10-11** to serve it.

### 4.6 Graded work → error cards

Built 2026-10-06 (`graded.py`), first run on PHYS 207 Exam I.

**One way in.** Every return becomes a PDF in Paperless, whatever the source. There is no Canvas or Gradescope integration (no Canvas token; Gradescope has no API).
- **Paper with the answer key** (PHYS 207): Aman marks it himself, and his red ink is the diagnosis. The convention is in `config/annotations.yaml` (`graded`).
- **Gradescope:** "Download Graded Copy". The applied rubric items are there even when the professor wrote no comments.
- **Canvas:** the submission with its rubric, saved or screenshotted.
- **His one line per miss** (red ink, or a Paperless note for online returns) is what matters most when nobody else wrote anything.

**The debrief** uses the `graded` route, the strongest model on the gateway (Opus 5), with pages at 150 dpi because the grading is handwriting. For every question that lost points, the model:
1. re-solves the question and says whether it agrees with the key;
2. finds the wrong move and quotes what Aman or the grader wrote;
3. sorts the miss: conceptual | fact | procedure | careless | time | misread;
4. gives the trap, the fix, and the why, plus the lecture and topic.

**Cards:** conceptual misses get M-Error cards and fact misses get M-Basic cards, always held for review. The other kinds are only logged, since a card can't fix carelessness; patterns across exams show up instead.

**Confirmation:** a diagnosis is confirmed only by something written: his note, a grader's comment, or a rubric item. One inferred from the key alone, or whose re-solve disagrees with the key, puts its doubt on the card, so Aman sees it on the review page. Approving the card confirms it.

**Where it goes:**
- **Ledger:** `graded_work` holds the score, the matched calendar exam, and the exam deck's readiness the day before (calibration). `mistake` holds each miss.
- **Learner profile:** `graded` has results, open mistakes, and the pattern by kind. The tutor drills fresh variants of each open mistake before the next exam (tutor skill §3a). *Since 2026-10-07* it logs every try through `mnemosyne_log_attempt` (`POST /mistakes/{id}/attempts`, propose token) into `mistake_attempt`: right or wrong, and whether it took a hint. The profile and the stub show "tried, clean", so "never tried" and "tried and still falling for it" look different. A try never closes a mistake; Aman's tick does.
- **Stub:** the exam's Paperless stub gets the debrief block, with each miss as a task. Ticking one marks it fixed.
- **API:** `GET /graded`; `POST /graded/{doc}` (`?again=true` after adding a note); `POST /mistakes/{id}/decision` (confirm, wrong + what happened, fixed, reopen).

### 4.6a Exam prep material (2026-10-07, `prep.py`)

P3's last piece of scope. Review sheets, practice exams, and keys were failing as lectures with no lecture number (#455).

- **What counts:** a Lecture- or Exam-type document whose title says review, practice, mock, sample, key, solutions, or answers, and that has no lecture number (`MATH 309 Exam 1 Review`). Aman files only what he has worked or marked; untouched ones he hands to a session instead.
- **Its exam:** the one its title numbers (`Exam 1 Review` → Exam I), else the next exam after it was filed.
- **What happens:** the model (route `prep`) reads it once. It returns the scope, format, and rules it states, the professor's emphasis and Aman's marks, and every problem, each with its lectures, its topics, and what he did on it. That goes to `exam_material`. **Nothing is carded.** Cards come from what he understands after working it. A marked practice exam (Exam type, tagged `graded`) is also debriefed like graded work.
- **The exam pack** (`GET /exams/pack`, `mnemosyne_exam_pack`) is where the `mnemosyne-exam` skill starts. It has:
  - the exam and the course's exam rules (`config/courses.yaml` `exam:`: own sheet, provided, or unknown);
  - each lecture's Paperless documents and card counts (strong = hook candidates, weak);
  - the prep material, each problem with its live card coverage (`uncovered` = no card anywhere);
  - his open mistakes and probes.
- **Documents:** `GET /documents/{id}/read` (`mnemosyne_doc_read`) gives 12 pages a call, as text with page images. Only course documents can be read (lecture, exam, assignment, or syllabus types with a course tag); nothing else in Paperless is readable with the propose token.
- **Probes** (`probe`, `POST /probes`, `mnemosyne_log_probe`): the skill asks one step up from a card he holds, before writing anything. A pass makes the card a hook, which the cram sheet builds on. A miss is a card he recalls but can't use. The learner profile carries the recent misses. Probes only add.
- **The skill** writes a cram sheet (HTML with quiz mode, published as a private Artifact), worked solutions (LaTeX PDF), and, when the course allows his own, a formula sheet that spends its space on the gaps. All three follow his Exam I sheets (`.claude/skills/mnemosyne-exam/examples/`).
- `POST /prep/{doc}` runs one now (`?again=true` to reread).

### 4.7 Pipeline stages

1. **extract:** document → pages plus text, and metadata from the title.
2. **propose:** an LLM call using the skill canon and the source, returning JSON cards (schema in SKILL.md).
3. **verify:**
   - `pint` dimensional check on every quantity.
   - Value check against `scipy.constants` or a curated `data/anchors.yaml` for nuclear data (NNDC-sourced). Any mismatch rejects the card.
   - A schema check on the note type and fields.
4. **dedupe:** embedding cosine (nomic-embed-text via Ollama) against existing notes. ≥0.92 means duplicate; 0.88–0.92 is flagged for review. Within one batch only ≥0.97 counts, since related cards on one idea are intended. Tuned 2026-09-25 from 0.85, which flagged same-topic cards. A duplicate of a note already in Anki isn't just dropped: that note gains the new card's `course::` tags, so one note serves every course that teaches the idea and lands in each one's exam deck (decided 2026-09-26).
5. **speakify:**
   - Deterministic rules for arithmetic.
   - MathJax (Node) TeX→MathML → **Speech Rule Engine** (ClearSpeak) for formulas.
   - LLM for mixed prose.
   - Anything whose meaning depends on an image gets `mode::visual`.
6. **approve:** by policy (§4.8).
7. **write:** upsert through anki-core by MnemosyneID, then sync.
8. **project:** Obsidian and metrics.

LLM routing:
- School material (slides, homework): TAMU AI Chat gateway. It falls back to the direct Anthropic API when the daily cap is hit.
- Chat and Telegram: direct API.
- Tagging and classification: local qwen is acceptable.
- Model names live in config, never in code.

### 4.8 Approval policy

`config/approval.yaml`:
- `slides`, `notes`, `chat`, `obsidian`, `telegram`, `legacy` → **auto-approve**, with `status::probation`.
- `graded-*`, `syllabus`, and any card with a failed or skipped verification → **human review** in Telegram.

**Calibration** (decided 2026-09-24): for the first lecture of each course, every card goes to human review. The approved set joins the skill's golden set, which is the regression test for prompt changes. After that, slides auto-approve with probation as above.

**Review viewer** (added 2026-09-25): any batch Claude generates in a session can go to the [review page](https://claude.ai/artifact/LqGHzXP3LPXrwCPTW3HSP7) instead, when Aman asks. Batches, payloads, and decisions live in the artifact's database, and Claude reads the decisions back with local tools, so approval stays with Aman (invariant 7). Approved batches wait in `drafts/` until P2 imports them into `card_draft`. The procedure is `.claude/skills/mnemosyne-cards/review.md`. *Update 2026-09-25:* the viewer moved into mnemosyne as the tailnet page `/review` (`review_set`/`review_item` in the Ledger). New cards are held as drafts until Aman decides them, approvals write straight to Anki, and the artifact is retired. Per-card decisions keep their reasons (`card_draft.decision_note`, `before_json`; `GET /review/decisions`), which Claude reads to propose canon changes. On a review set, a suspected duplicate is held with a flag rather than dropped, since Aman sees every card.

Probation cards marked with `*` during their first two reviews go straight to the leech doctor. Approval rate per source is tracked. When a source's approval rate falls below 85%, it auto-flips to human review until the prompt is fixed.

## 5. Scheduling

### 5.1 Priorities

The order is Exam windows > Now > Lang::Spanish > Core > Lang-minor > Reservoir. *Catch-up (2026-10-07):* Spanish is paused, so for now it's Exam windows > Now > Core > IPA and ASL > Reservoir. Anki can't express this directly, so it's implemented through:
- per-preset review caps,
- the governed new-card limits,
- the walk deck's search order.

### 5.2 Exam surfacing

Starting 10 days before each exam, mnemosyne builds or refreshes `~Exam::<COURSE>-<Title>` (e.g. `~Exam::PHYS207-Exam-I`) daily at 04:15. This happens right after the day's card write, and before walk decks, because a card can sit in only one filtered deck. `schedule/exam_decks.py` does it.

- **Which exams:** exams whose map Aman confirmed on the review page with at least one lecture. An exam inside the window with no lectures is reported as unmapped.
- **Search** (decided 2026-09-26): `(tag:course::<c>::lNN* OR …) -is:suspended (is:new OR prop:r<θ)`. It works per lecture, because that's how cards are tagged and exams are mapped. Unseen cards come in so they get seen, and seen cards stay only while their recall is below θ. This replaces the earlier plan of unit tags plus repositioning unseen cards, which could never surface while new/day is 0.
- **Threshold:** θ ramps linearly from 0.85 at T−10 to 0.95 at T−1, and stays at 0.95 on exam day.
- **Order:** retrievability ascending. Relative overdueness fails on tag searches in Anki 26.09.3.
- **Rescheduling:** "Reschedule cards based on my answers" is **on**. FSRS handles early reviews correctly, so nothing needs undoing afterwards.

The day after the exam, the deck is removed: its cards go home with their history. `POST /exams/decks` runs the same job on demand.

*How it got here:* on 2026-09-26 Aman built the first cluster's decks by hand ([`docs/desktop-sessions/2026-09-p3-exam-decks.md`](docs/desktop-sessions/2026-09-p3-exam-decks.md)). P4 adopted them by name.
- anki-core gained filtered-deck tools (`services/anki-core/PATCHES.md`, patch 3).
- It now grants mnemosyne the admin scope, fenced by the client's allowlist (CLAUDE.md invariant 9, standing exception).

### 5.3 Load governor

`schedule/governor.py`, at 04:15 after the exam decks (live 2026-09-26):
- **How it applies** (decided 2026-09-26): it sets each deck's **today-only** new limit through anki-core. That limit lapses by itself tomorrow, so a missed morning means no new cards, never too many. Presets keep new/day 0 and never change (CLAUDE.md invariant 9, standing exception).
- **Budget:** `walk_minutes` and `desk_minutes` in `config/budget.yaml`. The usual split is 40 + 15, which leaves ~20 minutes of the walking hour for Spanish comprehensible-input listening, which is not Anki.
  - *Catch-up budget (Aman, 2026-10-07):* 60 + 40, `max_new_per_day` 40, Spanish paused, because at 55 minutes the reviews already scheduled left too few new cards to keep up with lectures. Back to 55 once the `Now` backlog is through and reviews keep pace with lectures.
- **Nightly measurement:** mean seconds per review over the last 14 complete days, for the whole collection (filtered decks make per-deck timing unreliable). Under 50 timed reviews, it uses `governor.seconds_per_review`. Each day's minutes go to the Ledger (`load_day`); `GET /schedule` checks them against the budget ±15%.
- **Projection:** the mean daily review load over the next 7 days, from what's already scheduled, with overdue cards counted on day 0. New cards are sized by the simple cost model: one sustained new card a day costs `governor.new_card_reviews` (5) answers a day. The FSRS simulation is a later refinement if the ±15% check fails.
- **Size:** new cards today = (budget − projected load) ÷ cost per new card, clamped to 0…`governor.max_new_per_day` (25; 40 during the catch-up).
- **Allocation:** new/day is distributed across decks by priority weights, only among decks with new cards outside exam decks. A deck without any (no Spanish deck yet, say) passes its share on. Each deck's share is split between `~Walk::New` and the desk in proportion to its audio cards, so desk-only cards aren't starved during a long audio run like the multiplication facts. The desk part is its today-only limit:

| Deck | Weight | Catch-up (2026-10-07) |
|---|---|---|
| Spanish | 0.45 | 0 |
| Now | 0.30 | 0.45 |
| Core | 0.15 | 0.25 |
| Hindi + French | 0.10 | 0 |
| IPA | — | 0.15 |
| ASL (from 2026-10-05) | 0.05 | 0.15 |

IPA had no weight until 2026-10-07, so its cards never came in. A deck needs a weight to be governed at all, because presets keep new/day at 0. IPA's share is a desk limit: `Lang::IPA` isn't one of the walk homes, and its cards reach walks once they're due.

During the one-time backlog clear (§5.5, through 2026-10-04) Core, Now, Hindi, and French get nothing, and `~Walk::New` keeps bringing in the multiplication facts, 10 a day (`backlog_clear.walk_new`).

### 5.3a Focus decks (2026-10-05)

A subject Aman wants to practice on purpose gets `~Focus::<Name>::New` and `::Due`, from `focus:` in `budget.yaml`. They're rebuilt at 04:15 after the exam decks and before the walk decks.
- **New:** `new_per_day` new cards of the subject, taken out of the governor's daily total.
- **Due:** the subject's cards that are due, or whose recall is below `recall_below`, weakest first.

The first focus is Plasma (`topic::plasma`, Chen, and fusion cards). A focus removed from the config has its decks removed the next morning.

### 5.4 Walk decks

| Filtered deck | Search | Limit |
|---|---|---|
| `~Walk::Due` | `tag:mode::audio is:due -is:suspended`, retrievability ascending | 400 |
| `~Walk::New` | `tag:mode::audio is:new -is:suspended (note:M-Lang OR note:M-IPA OR deck:Core)`, order due | today's governed walk-new count; the deck is removed on a day with none |

`schedule/walk.py` rebuilds both at 04:15 (live 2026-09-26), `~Walk::New` first so yesterday's half-learned cards go home before `~Walk::Due` looks for due ones. Exam decks build before either. A rebuild never takes a card that's already in another filtered deck, so no `-deck:~Exam*` is needed.

### 5.5 The existing backlog (first two weeks)

- Overdue cards stay in their target decks and are cleared on walks at ~35/day. Expect ~40% Again; FSRS will relearn them fine.
- Cards stuck in learning get the same treatment.
- CHEM107 material goes to Reservoir at retention 0.85 and a 40/day cap.
- The new multiplication facts are introduced at 10/day **via audio**. That's the ideal walking content.

## 6. Walking mode

- **Hardware:** 8BitDo Micro in Keyboard mode, remapped in 8BitDo Ultimate Software (mobile). AnkiMobile ignores small 8BitDos in gamepad mode but accepts keyboard input.

Held with A pointing up and the ring and pointer fingers on L/R. The final map was set in the 8BitDo Ultimate Software app, and every key was confirmed working in AnkiMobile on 2026-09-24:

| Button | Key (8BitDo app) | Action |
|---|---|---|
| A | `1` | Again |
| B | `2` | Hard |
| Y | `3` | Good |
| X | `4` | Easy |
| + | `Space` | show answer |
| − | `Win+Z` (⌘Z on iOS) | undo last answer |
| R | `R` | replay |
| D-pad ↑, ↓ | `R` | replay |
| D-pad ← | `-` | bury |
| D-pad → | `Shift+8` (`*`) | mark → leech doctor |
| L, L2, R2, Star, Heart | Null | unmapped (next to the resting fingers, or used by the controller) |

- **Grading:** all four buttons. Hard counts as a pass in FSRS, so anything not actually recalled is Again.
- **AnkiMobile settings:** Auto Advance off; you set the pace with `+` and the grade buttons. Autoplay on. Deck-list widget or Shortcut for one-tap start.
- **Pocket safety:** test Guided Access with touch disabled plus keyboard input. Otherwise use minimum brightness and a pocket that faces the body.
- **Audio sources:**
  - Phase 1: iOS voices via `{{tts}}`. Zero media, works offline.
  - Phase 5: pre-rendered neural TTS per card (`AudioFront`/`AudioBack`) for consistency and offline quality. Use a local model on the Obelisk GPU while it exists, else cloud neural voices.
- **What walking is for:** languages (listening is the skill being trained), arithmetic facts, constants and anchors, IPA sounds, ear training, and definitions. Graphs, geometry, long formulas, and ASL are for the desk.

## 7. Languages

- **Spanish:**
  - Pick one variety; es-MX is the natural default in Texas.
  - Sentence-first. Build a frequency-ordered sentence set from Tatoeba (keep the attribution field) plus mined sentences from `/es`, podcasts, and shows.
  - Every note gets a Listen card; Produce is enabled for high-frequency items. Use cloze only for grammar points.
  - 10–15 new/day to start; the governor tunes it.
- **Hindi:**
  - Script is mostly done. Move to high-frequency vocabulary plus short sentences with Devanagari, IPA, and audio at 3–5/day.
  - The existing consonant cards become M-IPA-style, with sound audio.
- **French:** 3–5/day, same pipeline.
- **IPA** (built 2026-10-05): 93 M-IPA notes covering the pulmonic consonants, the 28 vowels, the common affricates, w, ʍ, and dark l (`data/ipa.yaml`).
  - Each sound is the Wikimedia Commons recording from its Wikipedia article, or a pinned file where the article's was wrong. `scripts/ipa_audio.py` fetched them, converted them to mp3, and committed them to `data/audio/ipa/`.
  - Author and license go in the Source field.
  - Tags: place/manner/voicing (or height/backness/rounding).
  - Example words come from English, Spanish, Hindi, and French first; those sounds also come first on walks.
  - The writer attaches each recording (`mnemosyne/audio.py`).
  - Example-word audio waits for P5's TTS.
  - **Reviewed 2026-10-07:** all 93 approved and written with their recordings. Aman's note asked for both directions to practice transcription. Each sound already has both (symbol → sound, sound → symbol). Whole-word transcription (hear a word, write its IPA) would be a new M-Lang template, a Desktop change; it's proposed for when words have audio.
  - **Later, sound features for IPA and languages** (Aman, 2026-10-29), the same idea as ASL's form:
    - **Features:** PanPhon's articulatory feature vectors and PHOIBLE's inventories give each segment its features, so neighbours can find confusable sounds (ɾ/r, dental/retroflex) by distance in feature space, not by the glyph, which embedding models collapse (every IPA glyph scored 1.00 against घ).
    - **Pronunciation:** later, a phoneme recognizer on his own recordings could check pronunciation.
    - **Data:** contributing recordings or transcriptions to open sets (Wikimedia Commons, Tatoeba audio) is the low-effort version of giving back.
- **IPA in every language's cards** (canon 23, Aman 2026-10-07):
  - Broad transcription in slashes in each M-Lang card's `IPA`, in one standard per language.
  - The native script stays on the front for reading.
  - Minimal pairs for the contrasts English merges: Spanish ɾ/r, Hindi dental/retroflex and plain/aspirated, French y/u.
  - Listen cards need audio: native recordings, or P5's TTS.
- **Arabic and Mandarin:** parked. When they start, the first decks are audio-native: Mandarin tones and Arabic letter sounds.
- **ASL** (scaffolded 2026-10-05): M-Sign, visual-only (desk, never walks).
  - **Vocabulary:** the 150 most frequent signs by ASL-LEX 2.0's frequency ratings from Deaf signers (`data/asl.yaml`, `scripts/asl_vocab.py`).
  - **Videos:** each sign's citation video from ASL Signbank (Hochgesang, Crasborn & Lillo-Martin; CC BY-NC-SA 4.0). It's matched by ASL-LEX's Signbank id and re-encoded small by `scripts/asl_videos.py`.
    - The videos live in the Obelisk's media folder (`~/helios/mnemosyne/media`, mounted read-only), not in git.
  - **Not used:** ASL Citizen (research-only, no redistribution), ASL-LEX's own videos (no reuse), and Lifeprint (only six clips are CC BY).
  - **Cards:** go to `Lang::ASL`, created on Desktop (`docs/desktop-sessions/2026-10-asl.md`). Grammar (space, classifiers, facial expression) needs a class or Deaf signers, not cards.
  - **Reviewed 2026-10-07:** all 48 approved and written with their videos. The remaining ~100 signs are fetched and staged on request.
  - **Later:** a camera checker for production (MediaPipe + an isolated-sign model) is possible as its own tailnet page. It's parked.
  - **Later, form as well as meaning** (Aman, 2026-10-29; `docs/embeddings.md`): an M-Sign card is embedded from its gloss alone, so the video adds nothing to search or dedupe.
    - ASL-LEX 2.0 codes each sign's form: handshape, selected fingers, flexion, location, movement, and iconicity. `data/asl.yaml` already matches each sign by its ASL-LEX/Signbank id.
    - Adding a "form" line to each sign's embedding text needs no Desktop change. It would let neighbours find look-alike signs (minimal pairs that differ in one parameter: ASL's version of the ±1σ cards) and families: initialized signs, and meaning carried by location (thinking at the forehead).
    - The tutor could then teach in families, not one sign at a time.
  - **Later, contributing to an ASL database** (Aman and a friend in SLP grad school with an ASL minor, 2026-10-29): a passion project, not a P-phase.
    - Where hearing learners can help without speaking for the language: tools and data infrastructure for Deaf-led projects (ASL-LEX, ASL Signbank, STEM sign glossaries), and coding or checking under a Deaf researcher.
    - Production data itself has to come from fluent Deaf signers.
    - The first step is an email to a lab, not building anything.
- **On the review page**, IPA and sign cards have a player (`GET /media/preview`) for the recording or video the card will get, so the match can be checked before approving.

## 8. Fluency outside Anki

- **Arithmetica:**
  - A randomized drill, since FSRS can't model randomized problems. It covers mixed operations, fraction↔decimal, powers and logs, and Fermi estimates graded by order of magnitude.
  - It runs as a Telegram mini-flow or a tiny PWA, 5 minutes in lines or before class.
  - Latency and accuracy go to InfluxDB; the Grafana panel is "time to correct answer".
  - Anki holds the lookup tables (squares, powers, logs, reciprocals, constants). Arithmetica practises *using* them.
- **Estimate-first habit:** every Fermi or anchor card has an `Anchor` field ("derive it from…"), so the card reinforces a derivation path, not just a number.
- **Derivation ritual:**
  - Once a month, re-derive 3 key results from a blank page in Obsidian (`Rituals/Derivations/`).
  - mnemosyne picks them from Core `type::result` cards with the lowest stability.
  - Any stuck step becomes an M-Error card.

## 9. Feedback loops

All of this is live since 2026-09-27 and runs at 04:15, after the walk decks and the governor.

- **Leech doctor** (`schedule/leech.py`):
  - **Candidates**, most urgent first: cards marked `*`, then Anki's leeches, then 5, 4, and 3 lapses, then FSRS difficulty above 9/10 with at least two lapses. Difficulty alone flagged the whole relearning backlog, so it isn't enough on its own. Image-only cards are skipped, because the doctor can't see them.
  - **Pace:** at most 6 new cases a night. A card comes back only after it lapses again, or when Aman marks it again.
  - **Diagnosis:** the LLM (route `leech`) gets the canon, the card, its last 20 answers, and its three nearest notes by embedding. It names one diagnosis: ambiguous cue, not atomic, interference, missing prerequisite, too long, or fine. It then proposes one action:
    - **rewrite:** edited in place, which keeps history. A legacy note gets a direct upsert on its MnemosyneID; a mnemosyne note is revised through the Ledger.
    - **replace:** new cards are written, and only once they're in Anki is the old card suspended.
    - **add:** new cards, and the old one stays.
    - **keep:** nothing changes.
  - **Decision:** Aman decides on the review page's **Leeches** tab: Apply, Edit, Keep as is, or Dismiss with a reason. Nothing is written before Apply. The mark and the `leech` tag come off once a case is closed.
  - The first real run, on 2026-09-27, diagnosed "Van der Waals Equation" as too long (split into its two correction terms), "cyan light wavelength" as an ambiguous cue, and a Hindi digit as interference.
- **Metrics** (`project/metrics.py`), for the Anki day that just ended:
  - reviews and minutes;
  - true retention over 30 days, overall and per deck;
  - the **fluency index**: the share of Core answers right in under 3 s;
  - due and overdue counts;
  - exam readiness: the share of an in-window exam's cards held at R ≥ 0.9;
  - leeches waiting, new cards let in, and approval rate by source;
  - **upkeep** (2026-10-07): minutes Aman spent deciding cards, batches, review sets, exam maps, and leeches. It's measured from the moments he decided them: decisions under 10 minutes apart are one sitting, and each sitting counts its span plus a minute. Auto-approved batches and Claude's own calls are left out. It's the system's cost to him, next to the review minutes it's for.

  They're kept in the Ledger (`metric_day`). They're also written to InfluxDB's `mnemosyne` bucket once `secrets/influx_token` exists (`scripts/obelisk_secrets.sh` makes a bucket-scoped token). The Grafana dashboard is `ops/grafana/mnemosyne.json`. Walk vs desk share is left out: Anki's review log doesn't record which deck a review came from.
- **Weekly report** (`project/report.py`): built in Sunday's 04:15 run and released to Telegram at 09:00 Chicago time, via the outbox. It covers load against the budget, upkeep against review minutes, retention, fluency, the leech doctor, exams in the next two weeks with readiness and uncarded lectures, the three slowest right answers in Core, and the new cards let in.
- **Weekly audit** (2026-10-07, `weekly.py`, the `mnemosyne-weekly` skill): the report says how the week went; the audit asks why, and changes how mnemosyne works. Sunday's report ends with its cue. Aman runs it in Claude Code, on his own account (no TAMU spend).
  - **Evidence** (`GET /weekly`, MCP `mnemosyne_weekly_audit`, read-only):
    - intake per source, and how much of it needed him;
    - his edits field by field, and his rejections in his words;
    - how cards first studied that week are holding, by course and note type;
    - leech diagnoses; what generation skipped (`card_batch.skipped_json`, since 2026-10-07);
    - questions, mistakes never retried, and missed probes;
    - exams with uncarded lectures; jobs not done; load and upkeep.
  - **Reading:** one lecture of the week is re-read against its cards for coverage, form, and truth, plus about ten other cards against the canon.
  - **Changes:** to the canon, the golden set, `config/`, cards (new ones through the checked `/inbox`; existing ones marked for the leech doctor), or small code fixes.
    - Each change cites its evidence and names the number next week's audit checks.
    - A canon rule needs three instances or Aman's word.
    - Nothing changes without his OK, item by item.
  - **Record:** `docs/weekly/YYYY-MM-DD.md`. Each audit starts by checking the last one's measures, so a change that didn't help gets reverted.
- **Claude as tutor** (`learner.py`, the `mnemosyne-tutor` skill):
  - **Profile:** at 04:15 a learner profile is snapshotted. It covers:
    - collection state per deck;
    - 30-day retention and load against the budget;
    - per-course memory (cards seen, strong, weak);
    - what Aman keeps failing and what he missed this week;
    - exams with readiness and their weakest cards;
    - his own recent reasons for rejecting or editing cards.
  - **MCP tools:** read-only, on every instance, so the propose token is enough: `mnemosyne_learner_profile`, `mnemosyne_search_cards` (every card on a topic with its retrievability, stability, and lapses), `mnemosyne_exam_status`, and `mnemosyne_card_stats`.
  - **The skill:** it reads the profile once per session and checks a topic's cards before explaining. It teaches retrieval-first and derivation-led, spends time on weak cards rather than mature ones, and interleaves. Mistakes and gaps come out of a session as proposed cards. It never writes to Anki.

## 10. Phases and acceptance criteria

| Phase | Scope | Done when |
|---|---|---|
| **P0 Hygiene** | `scripts/audit.py` in repo; FSRS re-optimize; Check Media; dedupe; tag normalization dry run | Audit JSON is committed and duplicates are listed |
| **P1 Schema & canon** (desktop) | Note types, presets, deck moves, migration, arithmetic speakify, skill canon with golden set, controller mapping | 500+ cards tagged `mode::audio`; a 10-minute walk works end-to-end on AnkiMobile |
| **P2 Core** | anki-core against a **test user on the self-hosted sync server** first, then the real user; mnemosyne service; Ledger tables; `/inbox`; Telegram approvals; helios-mcp tools | A card proposed from Claude on the tailnet (MCP tool) or pasted into Telegram appears on the phone after approval, idempotently (re-proposing does nothing). claude.ai access waits for the security review. |
| **P3 Ingest** | Paperless poller, Shortcut, lecture generation, verify gate, dedupe, syllabus→exams, error cards, Obsidian callouts | A NUEN 201 lecture PDF shared from the iPad yields tagged cards in under 10 minutes; one syllabus produces a confirmed exam map |
| **P4 Scheduling** | Walk decks, exam decks, load governor, leech doctor, metrics, weekly report (plus, since 2026-09-27, the learner model and tutor tools) | Daily time stays within ±15% of budget for 2 weeks; the exam deck builds and self-deletes |
| **P5 Audio & languages** | SRE speakify, neural TTS pre-render, Spanish pipeline, Hindi/French, IPA, M-Ear generation | 60%+ of due cards are audio-ready; the Spanish Listen/Produce flow is live |
| **P6 Fluency** | Arithmetica, derivation ritual, Obsidian projection polish | Latency trend is visible in Grafana |

P1 alone makes walking useful. Everything after that compounds.

## 11. Risks and deferred decisions

- **anki-core maturity:** read the fork's code before trusting it. Keep its backups on the NAS. Run a canary full-compare weekly (card count, note checksum) between the desktop export and the server copy.
- **Sync races:** keep server writes batched at 04:15 plus immediately after approvals, and never during active phone sessions. If conflicts appear, move approvals into the 04:15 batch.
- **AnkiMobile limits:** no JS answer hooks and limited controller support. Everything is designed around keyboard shortcuts.
- **Self-hosted sync server:** already in use (`helios-anki-sync`, the official `anki` sync server). Watch its version when upgrading Anki clients.
  - Since 2026-09-26, `ops/anki-backup.sh` archives `~/helios/stack/anki-sync/data` every night at 07:30 UTC. It stops only the sync server, for seconds, and checks the copy. It keeps 30 daily and 12 monthly copies in `/mnt/data/helios/backups/anki/` (`docs/deploy.md`).
  - Still open: an off-machine copy. The archives, and the Helios bundles, live on the Obelisk's own data disk.
- **TAMU gateway limits** (found 2026-10-07): $10 of tokens a day (reset 00:00 UTC), 1,000 requests and 100k tokens a minute. The API can't report usage. Both limits come back as HTTP 400 "Rate Limit Reached".
  - `generate/llm.py` recognizes it (`RateLimited`): it waits 30/60/120 s, then pauses all calls for 10 minutes. Jobs don't count it as a try and never tag an error for it (`pipeline.charged`).
  - Lectures go in parts of `limits.chunk_pages` (24) page images, so one request stays under 100k tokens. PHYS 207 L09's 55 slides didn't.
  - While lectures wait, Telegram nudges once a day (`notice`). A Claude Code session can card them on Aman's own account: `mnemosyne_stuck_documents`, then the canon, then `mnemosyne_settle_document`, which records the slides and marks the job done (refused unless cards were proposed for it). See `.claude/skills/mnemosyne-cards/stuck.md`.
  - No paid fallback (Aman, 2026-10-07): he approves any spending of his own money each time, so no API key. Overflow waits for the reset or is carded in a session.
- **Ollama and the GPU:** dedupe's embeddings come from Helios's Ollama, which needs the RTX 2080. On 2026-10-02 an unattended NVIDIA update left the driver's kernel module and libraries mismatched until a reboot (Oct 4), and every pipeline run failed at dedupe.
  - Dedupe now falls back to exact matching and holds every card for review.
  - The homelab's `ollama-gpu-guard.sh` (every 15 minutes) restarts Ollama if it comes up on CPU.
  - Still open: keeping NVIDIA packages out of unattended upgrades.
- **Copyright:** lecture slides are processed privately and never published. Keep attribution fields for Tatoeba and Wikimedia audio.

## 12. Vetted tools (from research)

| Status | Tools |
|---|---|
| Use | Anki 26.09 built-ins (FSRS-6, load balancer, easy days, Auto Advance, MathJax); FSRS Helper (desktop surgery); AnkiMCP add-on 124672614 (interactive Claude Code sessions on the ThinkPad) |
| Fork | `raslab/anki-sync-mcp` → anki-core |
| Reference | `anki-connect-server` (AGPL, headless); `Obsidian-Anki-Sync` (clozes inside math) |
| Skip | AnkiConnect-dependent Obsidian plugins as a backbone; consumer AI generators; Guru remote (desktop-only) |

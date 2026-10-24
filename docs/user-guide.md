# mnemosyne: the user guide

Everything mnemosyne does for you, and how to use it. The rest of the docs are about how it's built; this one is
about what to do. (Updated 2026-10-24: videos, clips, and the learn-and-make loop.)

**The idea:** you read, annotate, and ask questions as you already do. mnemosyne turns that into cards that pass
the canon, checks every number, holds anything uncertain for your OK, schedules it around your exams and your
daily budget, and gives Claude a memory of what you know so it can tutor you. Anki is only where you review.

## I want to…

| I want to… | Do this |
|---|---|
| card a lecture | Share the annotated PDF to the **→ Mnemosyne** Shortcut (course, `slides`). Cards in ~10 minutes. |
| add more notes to a lecture I already sent | Share the newer PDF the same way. Only new or newly marked slides are carded. |
| card a textbook section | Review page → **Read** tab → book and section. Or scan your own pages into Paperless, tag `mnemosyne`, title it "Chen 2.3". |
| card a research paper | Shortcut, kind `paper`, course **none**, title "Smith 2025 - Short title". Or file it in Paperless as type Paper with the `mnemosyne` tag. |
| learn from a thesis | Put it in Calibre and ask Claude to add it as a book. |
| get a returned exam debriefed | Upload it to Paperless as type Exam (homework: Assignment + `graded`). |
| prep for an exam | File the review sheets and practice exams you've worked (titled like `MATH 309 Exam 1 Review`), then tell Claude Code "prep me for MATH 309 Exam II". |
| make cards in a conversation | Tell Claude "mnemosyne this" or "card this". |
| approve or fix waiting cards | The review page (`http://<obelisk>:8110/review`), or the Telegram buttons. |
| study for an exam | Open `~Exam::<course>-<exam>` from the deck itself, or ask Claude "what should I study?". The full timeline is [Before an exam](#before-an-exam). |
| be quizzed / tutored | Ask Claude anything course-related, or "quiz me on…". It knows what you hold. |
| get a practice problem | "Give me a problem from Griffiths 2.3", or "a University Physics problem on Gauss's law". |
| find where my books cover something | "Where does my book explain the method of images?" |
| fix a card I keep failing | Press `*` on it during review. The leech doctor proposes a fix the next morning. |
| make mnemosyne better at writing my cards | Sundays, after the report: in Claude Code, "run the weekly audit". Claude reads the week, re-reads one lecture against its cards, and proposes changes with the evidence for each. You OK them one by one; the record goes in `docs/weekly/`. About 20–30 minutes. |
| card lectures stuck on the TAMU limit now | In Claude Code: "card my stuck lectures". Otherwise they card themselves after 6–7 p.m. Central. |
| keep a question | Write it on the slide ending in "?", or ask Claude and it logs it. |
| practice one subject on purpose | Study `~Focus::Plasma::New` and `::Due`. Add another focus in `config/budget.yaml`. |
| add a book | Put the PDF in Calibre, then ask Claude to add it. |
| learn from a lecture video | Ask Claude about it, or `uv run scripts/watch.py <link>` in Claude Code. It reads the captions and key frames, then points you to the moment. |
| get an animated clip of a concept | "Make a clip of <idea> for <course>, in my look." It's computed on amanim, in the glowing look, and lands on `/watch`. |
| take apart an animation I love | Write what you think works, then "let's dissect <link> from 1:20 to 1:50". Findings go into amanim's `TASTE.md`. |
| learn, make, and keep it all in one go | [Learn it, make it, keep it](learn-and-make.md): the whole loop. |

## Reviewing

- **Where:** AnkiMobile on walks, Anki Desktop on the ThinkPad. Everything syncs through `helios-anki-sync` on the
  Obelisk (not AnkiWeb).
- **The controller** (8BitDo Micro, Keyboard mode): space shows the answer; 1/2/3/4 are Again/Hard/Good/Easy; `r`
  replays audio; `*` marks a card (the leech doctor looks at it); `-` buries; ⌘Z undoes.
- **Your budget is 100 minutes a day for now** (catch-up, from 2026-10-07): 60 on walks, 40 at the desk. It was 55
  (40 + 15) and goes back once the course backlog is through. The load governor sizes new cards from the reviews
  the week already holds, up to 40 a day, split between walk and desk decks: Now 45%, Core 25%, IPA 15%, ASL 15%.
  Spanish, Hindi, and French are paused. All of it is in `config/budget.yaml`.
- **Decks:**
  - `Now`: this semester's courses. `Core`: timeless anchors, math, conversions, self-study books and papers.
    `Lang::*`: languages (Spanish, Hindi, French, IPA, ASL).
  - `~Walk::New` and `~Walk::Due` rebuild every morning for walks.
  - `~Exam::<course>-<exam>` appears 10 days before each exam and removes itself after. Study it from the deck
    itself, not the `~Exam` parent.
  - `~Focus::Plasma::New` / `::Due`: 5 new plasma cards a day plus anything weak, weakest first.
- **Everything scheduled runs at 04:15** Chicago time (Anki's day rolls over at 04:00): filtered decks, today's
  limits, metrics, the leech doctor, the learner snapshot.

## Getting cards in

### Lectures (automatic)

Annotate on the iPad, then share to **→ Mnemosyne** (setup: [`ipad-shortcut.md`](ipad-shortcut.md)). It files into
Paperless as type Lecture with the course tag, and cards arrive in about 10 minutes, with a Telegram message.

- **Your marks matter most.** A red ★ or "card idea" means must-card. A line ending in "?" is a question (a starred
  question is a priority). Highlights and notes are priority signals. Paperless notes on the document count too
  ("this is on the exam").
- **Send it again** after adding notes: only new or newly marked slides are carded; the old copy goes to Paperless's
  trash. A deck reused for the next lecture is recognized too.
- Lectures are in by default. Anything else needs the `mnemosyne` tag.

### Textbook readings

- **From the ebook:** review page → **Read** tab → book → section (`2.3`), chapter (`ch 2`), or pages as printed
  (`p 39-47`), up to 45 pages at a time. The cards wait on the Waiting tab.
- **From your own copy:** scan the pages into Paperless, tag `mnemosyne`, and title it with the book and where
  ("Chen 2.3", "Griffiths-QM 4.4", "young 21.3"). Your marks lead; the ebook's text rides along. Untitled scans wait
  on the Read tab for you to say which book and section.
- **Which course:** a course textbook's cards go to `Now` with the course tag. University Physics follows the
  chapter: ch 21–32 are PHYS 207, ch 17–20 and 33–36 are PHYS 221, the rest is self-study. Self-study books go to
  `Core`.
- Each book's names for scan titles are in `config/books.yaml` (e.g. `griffiths-em`, `griffiths-qm`,
  `griffiths-particles`, `susskind-qm`, `susskind-cm`, `young`, `chen`, `leon`, `haberman`,
  `hibbeler-statics`, `hibbeler-dynamics`, `strang`, `grady`, `bossavit`). Hibbeler's two halves each number their pages from 1, so a scan title
  or a page reading names the half: "Statics 4.3", "Hibbeler-D p 27".

### Research papers

- **File it:** the Shortcut's `paper` kind (course **none** unless a class assigned it), or Paperless type **Paper**
  plus the `mnemosyne` tag. Papers without the tag are just stored.
- **Title it "Author Year - Short title":** the title becomes the cards' tag (`src::paper::smith-2025-…`).
- **Annotate it** as you would slides: your stars and questions lead.
- **What you get:**
  - 5–12 cards on the paper's core: the question, the key idea, the headline result with its uncertainty, what it
    beats, a reusable trick, and the caveat. Every claim is attributed and dated ("Smith et al. (2025) report…"), so
    a later paper that disagrees adds a card instead of making one wrong.
  - **Before you read**, in the paper's Obsidian note: what the paper assumes you know, how well you hold each
    (held, weak, or no cards), and where your books cover the gaps. Shore those up first; the tutor will offer to.
  - Your questions, in the same note; ones about where the field is going can go to Hyperion.
- Paper cards always wait for your review. Re-annotate and file again under the same title, and only what's new is
  carded.
- **Theses** are books: put them in Calibre and ask Claude to add them, then read them chapter by chapter.

### Returned exams and homework

Upload the graded scan to Paperless as type **Exam** (homework: **Assignment** plus the `graded` tag). Mark misses
against the key if no one wrote why, or add one Paperless note per miss.

- **The debrief** lands in the exam's Obsidian note: score, readiness the day before, and each miss as a task.
- Conceptual misses become error cards (waiting for review); careless ones are logged as patterns.
- The tutor gives you fresh versions of each trap before the next exam and logs every try, so the note shows how
  many were clean. Tick a miss in the note once you've beaten it; two clean tries on different setups is a fair bar.
- If the scan can't show the full score, tell Claude the real one; the debrief keeps it.

### Syllabi and exams

Upload a syllabus (type Syllabus, `mnemosyne` tag). Exams come from your calendar; the syllabus maps each exam to its
lectures, which you confirm on the review page's **Exams** tab. Ten days out, the exam deck builds itself and ramps
up retrieval of those lectures' cards.

### In conversation

- **"mnemosyne this"**, with slides, notes, or a passage attached: Claude writes cards by the canon and proposes them
  (they wait for you). Large batches go to the review page's **From Claude** tab.
- **Telegram:** batches are announced with approve/reject buttons. Pasting a `mnemosyne-cards` block makes a batch.

### Languages

IPA sounds (with recordings) and ASL signs (with videos) are carded from curated lists; Spanish, Hindi, and French
share the `Lang::*` decks. Each gets a share of new cards (`config/budget.yaml`).

## Deciding what goes in

Nothing reaches Anki without passing the canon, the number checks, and dedupe. What's uncertain waits for you.

- **The review page** (`http://<obelisk>:8110/review`; add it to your home screen):
  - **From Claude:** batches Claude pushed in a session.
  - **Waiting:** every card held for you: approve, edit, or reject (a reason helps the canon learn).
  - **Questions:** your open questions.
  - **Read:** card a textbook section; scans waiting for a book name.
  - **Leeches:** the leech doctor's proposals.
  - **Exams:** maps to confirm and each exam's coverage.
- **Telegram** buttons do the same for whole batches. Whichever you use second says "Already…".
- **Changing a card already in Anki:** ask Claude; edits go through with a reason and update the note in place.
- **Numbers** never come from the model on trust: every numeric card is checked for units and against CODATA or
  `data/anchors.yaml`. A paper's own numbers can't be anchored, so those cards always wait for you.
- **"Similarity check unavailable"** on cards means dedupe couldn't reach the GPU; nothing is lost, they just wait.

## Studying with Claude

Any Claude with the Helios MCP (Claude Code, Claude Desktop on the tailnet) can read your memory and your books.
The skills (`mnemosyne-tutor`, `mnemosyne-exam`, `mnemosyne-books`, `mnemosyne-cards`, `mnemosyne-weekly`) tell it how to use them.

- **It knows what you hold.** Before explaining anything it checks your cards on it: what you own (it builds on
  that), what's fading (one question brings it back), what's weak (worth teaching), and what you've never carded.
- **It teaches the way your system is built:** retrieval first (it asks before it tells), derivations from what you
  own, weak cards from a new angle, interleaving, confidence checks, limits and asymptotes.
- **It uses your books.** It quotes and cites the book you actually use ("Griffiths QM, 2.3, p. 39"), in its
  notation, and gives practice problems one at a time, cited, letting you work them first. University Physics has
  answers to odd problems, so it can check those.
- **It starts from your open questions, open mistakes, papers, and the next exam.**
- **What comes out:** proposed cards for gaps and traps (they wait for you), and your questions, logged.
- **Before an exam** (`mnemosyne-exam`): it reads the exam's lectures and the prep material you filed or hand it,
  and solves the practice exams against the keys. Then it probes the cards you hold: one question a step past
  each card, for about 15 minutes. A card you can use becomes a hook the sheet builds on; one you can only recite
  becomes a gap it spends its space on. Out come a cram sheet (quiz mode, on your phone), worked solutions, and
  your own formula sheet when the course allows one.
- **Things to say:** "quiz me on Gauss's law", "what should I study tonight?", "explain spin from what I know",
  "give me a Griffiths problem on the harmonic oscillator", "what does Young say about thin films?", "I keep
  forgetting the Debye length", "go through my open mistakes for PHYS 207".
- On claude.ai the tools wait for a security review; the skills fall back to asking you.

### Keeping the skills current

The skills ship as the `mnemosyne` plugin from this repo (`.claude-plugin/marketplace.json`).

- **Claude Code on the laptop** (terminal and the desktop app's Code tab share it):
  `claude plugin update mnemosyne@mnemosyne`, then restart (or `/reload-plugins`). The marketplace has auto-update
  on, so new versions usually download by themselves at session start and apply at the next one.
- **claude.ai, the desktop app's chat, and the phone and iPad apps:** add the plugin to your account once, in
  **Customize → Plugins → Add**, from the GitHub repo `almondshawarma/mnemosyne` (it's private, so claude.ai needs
  access to it). Skills from an account plugin load in chat on the web, desktop, and mobile apps, and the plugin
  also syncs into Claude Code as `mnemosyne@synced`; then uninstall the command-line copy so the skills don't load
  twice.
- **On the phone, the tools don't come along yet.** The Helios MCP is tailnet-only, and claude.ai runs connectors
  from Anthropic's cloud, so mobile chat has the skills but not your cards or books. The tutor says so and works
  from what you tell it. Full tools on mobile need the MCP exposed, which waits for its security review.

## Before an exam

What happens by itself, and what's yours, from the first lecture to the graded copy (PHYS 207 Exam II on Oct 21 as
the example).

1. **Every lecture, as it's taught:** share the deck to the Shortcut the same day, annotated or not. Its cards come
   in at 04:15 the next morning as part of the day's new cards (`Now` gets 45% of them). When you finish annotating
   it later, send it again: only the new or newly marked slides are carded, and your questions get picked up.
2. **Every day:** review in Anki, walks and desk, within the budget (100 minutes for now). That's the whole daily
   job. The governor decides how many new cards come in, and the walk decks take whatever has audio.
3. **When old exams and keys come out** (Canvas): put them in Downloads, or file the ones you've worked in Paperless
   titled like `PHYS 207 Exam II Practice 2025`. Do the same with the exam's **formula sheet**, titled
   `PHYS 207 Exam II Formula Sheet`. The keys and sheets you just downloaded don't need filing.
4. **T−10 (Oct 11, 04:15):** `~Exam::PHYS207-Exam-II` builds itself from the mapped lectures: every unseen card,
   plus every seen card whose recall is below 85%, weakest first. The bar rises to 95% by the day before. Study it
   from the deck itself, every day, before the rest. Its cards leave the walk and new decks while it exists.
5. **Any time before the probes:** "work through the PHYS 207 Exam II practice exams with me" gets you the worked
   solutions (LaTeX PDF in Downloads: every problem solved and checked against the key, with the trap each
   distractor sets, the exam's topic split, and what the equation sheet leaves out). Already done for Exam II.
6. **About 3–4 days out (Oct 17–18): the probe session.** In Claude Code: "prep me for PHYS 207 Exam II". About 15
   minutes, one question at a time, each a step past a card you hold: apply it somewhere new, predict a limit, pick
   the method. Answer without notes. Every answer is logged.
   - A card you can use becomes a **hook**: the cram sheet starts from it and climbs to what the exam asks.
   - A card you can only recite becomes a **gap**: the sheet spends its space there, with a derivation to
     reproduce on blank paper and a fresh problem under quiz blur.
   - Open mistakes from Exam I come back as fresh problems with the same trap.
7. **Right after the probes:** the **cram sheet** (HTML with quiz mode, published as a private Artifact so it opens
   on your phone, and saved in Downloads), your own **formula sheet** for courses that allow one (NUEN 201), and
   proposed cards for the gaps a card would fix (review page, Waiting tab).
8. **The night before:** the cram sheet's last section: 5–8 things to reproduce from a blank page. If they all come
   out clean, stop.
9. **Afterwards:** upload the graded copy (Paperless, type Exam). The debrief puts your score next to the deck's
   readiness and the probes, turns conceptual misses into error cards, and the exam deck removes itself the day
   after the exam.

## Your questions

Every question you write on a slide (or ask Claude) is kept, with a short answer, a sharper version of it, and a kind
(clarify, mechanism, predict, connect, apply, verify).

- They land in the lecture's (or paper's) Obsidian note as tasks; tick one when you've got it.
- Ones that reach past the course go to Hyperion as investigations.
- The tutor offers them back, and the Sunday report shows how your questions are changing.
- Dataview queries for all open questions: [`obsidian-questions.md`](obsidian-questions.md).

## Your books

- **The library is Calibre** on the Obelisk. mnemosyne knows each book's chapters, sections, and printed page
  numbers.
- **Status tags** on every book in Calibre tell any AI how to read it without guessing: `ai:born-digital` or
  `ai:scanned`; `ai:text-clean`, `-math-garbled`, `-ocr`, or `-none`; `ai:read-text` or `ai:read-images-for-math`;
  `ai:outline-sections`/`-chapters`/`-none`; and `mnemosyne:<key>` for books it can read.
- **Most textbooks are "math-garbled":** their PDFs lose math symbols in the text layer, so equations are read from
  the page images. Clean text: University Physics, Scherz, Nahin. Boyce has no text at all (a scan without OCR).
- **Adding a book:** put the PDF in Calibre (Calibre-Web's upload, or `~/helios/stack/books/ingest`), then ask
  Claude to add it. It checks the outline, the page numbers, the bookmarks, and that the PDF is only the book (the
  University Physics file had another book merged into it), then tags it.
- **Calibre or Paperless?** If you'll read it chapter by chapter over weeks (a textbook, a thesis), Calibre. If you'll
  read it in a sitting and annotate it (a paper, an article), Paperless.

## Seeing how it's going

- **Every Sunday at 09:00**, a Telegram report: the week's load, retention, fluency index, backlog, exam readiness,
  and your questions.
- **Exam readiness** (share of an exam's cards held at 90%+): ask Claude or the review page's Exams tab.
- **Grafana** dashboards, once the InfluxDB token is set up ([`deploy.md`](deploy.md)).
- **Obsidian:** each lecture, paper, and exam has a note in `Sources/Paperless/` with mnemosyne's block (questions,
  debriefs, "Before you read") under the line that's yours.

## When something seems off

- **No cards after a lecture upload:** check that Tailscale was on, that the Telegram message came, and the
  document's tags in Paperless (`mnemosyne-error` means two tries failed; ask Claude to look).
- **Lectures waiting on the TAMU gateway:** your TAMU AI allowance is $10 of tokens a day, reset at midnight UTC
  (6–7 p.m. Central), and 100k tokens a minute. When the day's allowance is spent, lectures wait: they retry every
  poll without using up a try, and card themselves after the reset. Telegram says so once a day (⏳). To card them
  now on your own Claude account, open Claude Code and say "card my stuck lectures": it reads each deck, writes the
  cards by the canon, keeps your slide questions, and marks the lecture done so the pipeline doesn't card it twice.
  Long decks are carded in parts of 24 slides so no single request breaks the per-minute limit.
- **Cards say "similarity check unavailable":** the GPU needs attention. Check `nvidia-smi` on the Obelisk; a driver
  mismatch needs a reboot (outside 04:00–04:45).
- **A lecture went in under the wrong course:** ask Claude to refile it (`POST /pipeline/document/{id}/refile` with
  the right course, and the lecture number if its title doesn't carry one). It moves as if it had been sent there:
  title and course tag, its cards' lecture tag and Source, your questions, and, if that course already has a copy of
  the deck, the usual re-share. Uploads now refuse a file named for one course ("MATH 309 L07 - …") when another
  course was picked; the Shortcut's notification says "NOT filed" and why.
- **A card is wrong or badly worded:** press `*` on it, or ask Claude to revise it.
- **Backups:** the Anki server is archived nightly (30 daily, 12 monthly copies), plus the whole Helios stack.

## Where things live

| Thing | Where |
|---|---|
| Review page | `http://<obelisk>:8110/review` |
| Animated clips | `http://<obelisk>:8110/watch` |
| How clips look, and why | [the clips skill](../.claude/skills/mnemosyne-clips/SKILL.md), amanim's `TASTE.md` |
| How cards are written | [the canon](../.claude/skills/mnemosyne-cards/SKILL.md) |
| Courses, books, budget, topics | `config/courses.yaml`, `books.yaml`, `budget.yaml`, `topics.yaml` |
| Design and phases | [`PLAN.md`](../PLAN.md) |
| Running it | [`deploy.md`](deploy.md) |
| The iPad Shortcut | [`ipad-shortcut.md`](ipad-shortcut.md) |

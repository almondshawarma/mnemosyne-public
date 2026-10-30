# CLAUDE.md — mnemosyne

Mnemosyne is Aman's memory system. It is a **Helios module** that generates, verifies, schedules, and surfaces Anki cards. Anki (AnkiMobile on iPhone and iPad, Desktop on the ThinkPad) is only the review surface. This repo holds everything else.

Read before any non-trivial change:
1. `PLAN.md` for the architecture, phases, and acceptance criteria.
2. `docs/audit-*.md` for the current collection state.
3. `.claude/skills/mnemosyne-cards/SKILL.md`. This is the card canon and the single source of truth for how cards are written. Pipeline prompts **import** it; never duplicate its rules elsewhere.

Work phase by phase (P0→P6). Don't start a phase until the previous one's acceptance criteria pass. Before starting work, state which phase it belongs to.

## Hard invariants — never violate

1. **Never open a real collection file from two processes.** anki-core owns the server copy exclusively. Scripts that need collection access go through anki-core's API or run against a *copy*.
2. **No schema changes from the server.** Note types, fields, and templates are changed only on Anki Desktop, by Aman. anki-core runs with `ANKI_ALLOW_SCHEMA_CHANGES=false`, `ANKI_ALLOW_FULL_SYNC=false`, and `ANKI_ALLOW_DESTRUCTIVE=false`. If a task seems to need a schema change, stop and write it up as a desktop checklist in `docs/desktop-sessions/`.
3. **Never full-upload.** A full sync is always a human decision, made on desktop.
4. **All writes are upserts keyed on `MnemosyneID`** (UUIDv7). Re-running any job must be a no-op.
5. **Drafts never live in Anki.** They live in Ledger `card_draft` until approved.
6. **Numeric facts must pass the verify gate:** `pint` dimensions, plus `scipy.constants` or `data/anchors.yaml`. LLM output is never the source of truth for a number.
7. **Cloud-facing MCP tools can only propose.** Approvals, edits, and deletions happen via Telegram or local tools.
8. **Scheduled collection jobs run at 04:15 America/Chicago** (Anki rollover is 04:00). Don't add other scheduled writes.
9. **Destructive operations against the real collection** (delete, suspend en masse, reposition, preset edits) require a dry-run diff committed to `docs/runs/` and Aman's explicit OK in the session.
   - *Standing exception (Aman, 2026-09-26), for the P4 jobs at 04:15 only:*
     - Setting **today-only** new/review limits within the `config/budget.yaml` caps. These reset by themselves the next day, and presets never change.
     - Creating, rebuilding, and removing the `~Exam::*` and `~Walk::*` filtered decks, and (Aman, 2026-10-05) the `~Focus::*` decks listed in `config/budget.yaml`. Removing a filtered deck returns its cards home with their history.
     - Both are logged.
   - anki-core grants mnemosyne the admin scope for these. `anki/core_client.py` calls only the tools on its allowlist, and only today-only limits.
   - *Leech doctor (2026-09-27):* when Aman presses Apply on a **replace** proposal on the Leeches tab, that one card is suspended after its replacements are written. That press is his explicit OK for that card. Nothing is ever suspended in bulk this way.

## Development against Anki

- Integration tests use the **`mnemosyne-test` user on the self-hosted sync server** (`docker compose --profile test`, `scripts/integration_p2.py`) and fixture collections built from `tests/fixtures/`. Never use Aman's sync user in tests. Devices and anki-core all sync to `helios-anki-sync` on the Obelisk; AnkiWeb isn't used.
- `scripts/audit.py` is read-only and safe on any `.colpkg`. Use it before and after migrations and commit the JSON diff.
- Until anki-core exists (P2), bulk edits to the real collection are generated as a diff in `docs/runs/` and applied from Anki Desktop's debug console with `scripts/anki_apply.py`. That code runs inside Anki's own process and touches only notes whose current state matches the diff's `before`. Test apply code against fixture collections (`anki` is a dev dependency), never the real profile.
- Verify rendering on **AnkiMobile** before closing any template or audio task. Desktop rendering is not evidence. Test with the 8BitDo Micro in Keyboard mode:

| Key | Action |
|---|---|
| space | show answer |
| 1 / 2 / 3 / 4 | Again / Hard / Good / Easy |
| r | replay |
| * | mark |
| - | bury |
| ⌘Z | undo |

The full button map is in PLAN §6.

Anki gotchas:
- Fields are separated by `\x1f`.
- Math uses MathJax `\( \)` and `\[ \]`. Never `$…$` or `[latex]`.
- Media: generated files use the `mn_` prefix; fonts and other permanent assets use the `_` prefix so Check Media keeps them.
- A card can be in only one filtered deck at a time. Exam decks build before walk decks.
- Filtered-deck order "Relative overdueness" (enum 10) fails in Anki 26.09.3 whenever the search touches notes (`tag:`, field searches): `ambiguous column name: data`. "Retrievability ascending" (enum 8, `DYN_DUEPRIORITY`) works and is what walk/exam decks use.
- Audio templates must not include `{{FrontSide}}`, because it replays the question. Re-render the question text instead.
- `{{tts xx_XX:cloze:SpeakText}}` on the front and `cloze-only:` on the back is how cloze audio works. There is no per-card pre-rendered cloze audio.
- FSRS search: `prop:r<0.9`, `prop:s>30`, `prop:d>0.8`.

## Stack and layout

- **Python 3.12, `uv`, `ruff`, `pyright` (strict on `src/`), `pytest`.**
- Web and service: FastAPI + APScheduler.
- Libraries: pydantic v2 models, `httpx`, `structlog`. Speech: Node sidecar for MathJax + Speech Rule Engine.
- Commands: `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run pyright`, `docker compose up -d`.

```
src/mnemosyne/
  ingest/        paperless.py books.py (textbooks: Calibre, outlines, scan titles) telegram.py obsidian.py inbox.py
  generate/      propose.py  (loads SKILL.md canon)  prompts/
  verify/        quantities.py dedupe.py schema.py
  speak/         arithmetic.py sre_client.py llm_speak.py tts.py
  schedule/      exams.py exam_decks.py walk.py governor.py leech.py
  anki/          core_client.py   (only module that talks to anki-core)
  ledger/        models.py migrations/ decks.py (page fingerprints, so a re-sent deck is carded only where it changed)
  project/       metrics.py report.py obsidian.py (lecture question notes) hyperion.py (investigations via Helios MCP)
  merge.py       cross-course duplicates: one note per idea, in the most fundamental framing (merge, merge up)
  learner.py     the learner model: what Aman knows and struggles with (read-only; the tutor's MCP tools)
  questions.py   Aman's questions: from his slide annotations or sessions; answered, sharpened, kept
  graded.py      returned exams and homework: a debrief of every lost point, error cards, mistakes for the tutor
  audio.py       media the pipeline owns (IPA recordings, ASL videos), attached as cards are written
  library.py     Aman's books for the tutor and sessions: status (ai:* tags), read with citations, search, problems
  papers.py      research papers (Paperless type Paper): tags, prerequisites checked against cards and books
  prep.py        exam prep: review sheets, practice exams, keys attached to their exam; the exam pack; probes
  weekly.py      the weekly audit's evidence: the week's cards, Aman's decisions, how new cards hold, the gaps
  videos.py      lecture videos the tutor watched: transcript search, spans with contact sheets, frames, the watch queue;
                 studies (one span of an animation, measured densely, for dissecting its craft)
  clips.py       animated clips on /watch (rendered Manim scenes): the list, opens counted, videos cleared after
                 keep_days (code kept), rendered again on request
  web/           review.py review.html (/review: waiting cards, exam maps) watch.html (/watch: the clips)
services/speech/ Node: MathJax TeX→MathML → SRE ClearSpeak
services/manim/  job.py: one render inside a throwaway sandbox container (ManimCE image, pinned in scripts/render.py)
services/watch/  the watcher image: captions + key frames + contact sheets from a video, never the video (scripts/watch.py);
                 study mode (--study: one span densely; study.py's measurements, also run locally by scripts/watch.py)
scenes/          Manim scenes, by course, on mn_style.py (house style + layout check); worked examples for Aman too
                 gallery/ (one short demo per technique, stills committed: what chats pattern-match against),
                 bench/ (render-cost benchmarks), tests/ (`uv run --with manim==0.21.0 pytest scenes/tests`)
config/          courses.yaml approval.yaml budget.yaml decks.yaml llm.yaml topics.yaml (tag vocabulary) annotations.yaml (Aman's slide marks) books.yaml (textbooks) clips.yaml (how long a clip keeps its video)
data/            anchors.yaml (curated constants, NNDC-sourced nuclear data)
scripts/         audit.py retag.py migrate.py speakify.py anki_apply.py (runs inside Anki Desktop) calibre_tags.py (on the Obelisk) render.py (renders a scene in the Obelisk's sandbox; --publish adds cues.py's Resolve sound kit: marker EDL + tone WAVs) render_runner.py (the host's render queue: chats' scenes, re-renders) watch.py (watches a lecture video there; --study dissects a span, here by default)
desktop/         notetypes/ (M-* fields, templates, CSS; created on Desktop via anki_apply.notetypes)
docs/            audit-*.md (collection state) desktop-sessions/ runs/ weekly/ (the weekly audits' records)
drafts/          legacy: batches reviewed in the old claude.ai viewer before P2 (reviews now live in the Ledger)
.claude-plugin/  private plugin marketplace; the plugin root is .claude/ (the skill ships from there)
```

## Helios conventions

- Deploy with Docker Compose on the Obelisk. Target later: an LXC on the Proxmox host.
- Private access is via Tailscale; nothing is public. Exposing anything (e.g. MCP for claude.ai) needs its own security review first.
- mnemosyne runs as its own compose project from `~/helios/mnemosyne` on the `helios_default` network (`docs/deploy.md`). Hooks in the homelab stack (telegram bot, MCP `mnemo.py`) are edited on the Obelisk with `.bak-mnemosyne` copies; Aman commits homelab himself. Copies of the hook code live in `ops/helios-hooks/`.
- Secrets are Docker secret files in `~/helios/mnemosyne/secrets/` (gitignored), created by Aman with `scripts/obelisk_secrets.sh`. Claude never handles credentials. Never commit `.env` or tokens.
- Backups: `ops/anki-backup.sh` runs from Aman's crontab at 07:30 UTC. The Helios stack bundle runs at 08:00 UTC. Nothing on the host may stop `helios-anki-sync` between 04:00 and 04:45 Chicago time, because the 04:15 job needs it. The previous crontab is at `~/helios/crontab.bak-mnemosyne-20260926`.
- State lives in Ledger tables (`card_draft`, `card_source`, `course`, `exam`, `exam_material`, `ingest_job`, `paper`, `probe`, …). Add migrations; don't hand-edit.
- Dedupe embeds with Helios's Ollama (`helios-ollama`, GPU). If it's unreachable, dedupe falls back to exact matching and every card waits for review ("similarity check unavailable"). When that shows up, check `nvidia-smi` on the Obelisk. A "Driver/library version mismatch" means the host needs a reboot, which is Aman's call.
- Media: IPA audio is committed (`data/audio/ipa/`). ASL videos live in `~/helios/mnemosyne/media/` on the Obelisk (gitignored, mounted read-only at `/media`). Each folder has a `manifest.json` with attribution, and the writer stores files through `anki_media_store` once (`media_file`). Fetch scripts: `scripts/ipa_audio.py`, `scripts/asl_videos.py`; card builders: `scripts/ipa_cards.py`, `scripts/asl_cards.py`.
- Textbooks: the api mounts Calibre's library (`helios_cwa-library`) read-only. `config/books.yaml` lists the books; adding one is in `docs/deploy.md` ("Readings").
  - Every Calibre book carries `ai:*` status tags (born digital or scanned, whether its text can be quoted, what its outline offers) and `mnemosyne:<key>` when it's in books.yaml. `scripts/calibre_tags.py` writes them through `calibredb`; only those tags are ever touched.
- Animations: scenes are model-written Python, so they render only in the sandbox `scripts/render.py` starts on the
  Obelisk (no network, read-only root, no capabilities, capped CPU, memory, processes, and time). Finals land in
  `media/render/<slug>/` and play on `/watch`; drafts stay in `.drafts/`. Read every draft's stills before publishing.
  - Any chat can submit a scene as code (`mnemosyne_clip_submit`). `scripts/render_runner.py` (Aman's crontab, every
    minute) renders each one in its own throwaway sandbox container and checks the output before publishing.
    Reviewed in `docs/security/clip-submit.md`; keep its controls when changing any of this.
  - A clip's video is cleared `keep_days` after it was last opened: 14 by default (`config/clips.yaml`), or
    per clip from the page. Its code, stills, and metadata stay, and the page's "Render it again" queues the
    clip's own `scene.py` for the same runner.
  - Aman's style layers go into every render, read-only beside the scene:
    - **amanim:** his technique library (`~/OneDrive/Documents/Projects/amanim`; on the Obelisk `~/helios/amanim`,
      pulled nightly). Its commit is recorded in render.json. Improve it freely, with tests (`tests/test_physics.py`),
      and push.
    - **`scenes/brand.py`:** his design tokens, generated from `helios_tokens.yaml` and gitignored. mn_style maps
      their roles. Never edit token values.
  - The image is `mnemosyne-manim:1` (`services/manim/Dockerfile`: pinned ManimCE plus matplotlib).
    - Its Pillow 12 breaks ImageMobject in Manim 0.21, so scenes stay vector.
  - **The look (Aman, 2026-10-24):** he prefers glowing, flowing, spacey work to 3b1b's flat look.
    - `"look": "ethereal"` in a scene's CLIP puts the render through `amanim.post` (bloom, fringe, vignette, grain).
    - In-scene: `halo`, `depth_plate`, `ride`, and `settle`.
    - `.claude/skills/mnemosyne-clips/clips.md` has the rules, and `references.md` the work it comes from.
  - Reusable pieces in scenes get `# harvest:` marks, and `mnemosyne_clip_toolkit` lists them; move them into
    amanim.
- Metrics go to the InfluxDB bucket `mnemosyne`. The Grafana dashboard JSON lives in `ops/grafana/`.
- MCP tools are named `mnemosyne_*`. They live in the homelab's `mcp/server.py` + `mnemo.py`, patched by `ops/helios-hooks/`:
  - Every instance gets `propose_cards` plus the read-only `learner_profile`, `search_cards`, `exam_status`, and `card_stats`. Reading needs only the propose token. Proposed cards (and Telegram's) are held for review with the number check and dedupe results (`pipeline.propose_checked`).
  - Every instance also gets the read-only book tools: `books`, `book_search`, `book_read`, and `book_problems` (the `mnemosyne-books` skill).
  - Every instance also gets the exam prep tools: `exam_pack`, `doc_read` (course documents only), and the add-only `log_probe`, `log_attempt`, and `log_question` (the `mnemosyne-exam` and `mnemosyne-tutor` skills).
  - And `stuck_documents` and `settle_document`, for carding a lecture in a session when the TAMU allowance has run out. Settling only finishes a lecture the session proposed cards for.
  - And the read-only `weekly_audit`: the week's evidence for the `mnemosyne-weekly` skill.
  - And the lecture-video tools: the read-only `videos`, `video_search`, `video_watch` (transcript span + contact
    sheets), and `video_frames`, plus the add-only `video_request`, which queues a YouTube link for the `watcher`
    service (captions and key frames, never the video). And `video_study`, which queues a study of one span of an
    animation (add-only) and returns it once it's done: motion, cuts, palettes, composition, attribution.
  - And the clip tools: the read-only `clips`, `clip_submit` (a Manim scene's code, rendered in a sandbox and
    published to /watch), `clip_status` (the error, or the stills to check; `gallery-<name>` returns a gallery
    demo's code and stills), and the read-only `clip_toolkit` (amanim's API, the house style, the gallery, and
    harvest candidates, read live). The `mnemosyne-clips` skill is how a chat uses them.
  - `mcp-local` additionally gets `pending_batches` and `approve_batch`.
- Skills (the plugin root is `.claude/`): `mnemosyne-cards` (the canon), `mnemosyne-tutor` (tutoring from the learner model), `mnemosyne-books` (quoting, citing, and practice problems from his textbooks), `mnemosyne-exam` (exam prep: the pack, probes of the cards he holds, cram sheet, worked solutions, his own formula sheet), `mnemosyne-clips` (animated clips: writing a Manim scene in his style and publishing it through `clip_submit`; `clips.md` is the reference, `3d.md` the 3D part), and `mnemosyne-weekly` (the weekly audit: what the week's cards show, and changes to the canon, config, and cards that Aman approves item by item), and `mnemosyne-unpack` (when Claude does something Aman wants to understand: the reasoning against what he knows, the general patterns, a write-up and drill in his metis repo, and cards through the canon).
- Obsidian: respect the `ai:` frontmatter tiers and skip `ai: none` notes. Only write inside `<!-- mnemosyne:start -->…<!-- mnemosyne:end -->` markers, under `Sources/`.
  - The api container mounts only the vault's `Sources/`.
  - A lecture's questions go in its Paperless stub (`Sources/Paperless/`), found by `paperless_id`. They're one marked block just under the `<!-- HELIOS:below-is-yours … -->` line, which the homelab ingester keeps verbatim when it regenerates the stub's top each morning. Never write above that line, and never make a separate questions note: that was retired on 2026-10-05.
  - Daily Notes are private to AI. Stubs link the day (`day: "[[Sep 28, 2026]]"`) instead, and `docs/obsidian-questions.md` has the Dataview queries for Aman's templates.
- Tags: a card's first `topic::` tag is its home domain (canon 22). Every tag comes from `config/topics.yaml`, including the `principle::` ones. Add to that vocabulary rather than inventing parallel paths.
- Telegram goes through helios-agent. mnemosyne exposes handlers; it does not run its own bot.

## LLM usage

- Provider routing is set in `config/llm.yaml`:
  - School material goes through the TAMU AI Chat gateway: $10 of tokens a day (reset 00:00 UTC) and 100k tokens a minute, both reported as HTTP 400 "Rate Limit Reached". There is no paid fallback, by Aman's choice (2026-10-07): spending his own money needs his OK each time, so never add an API key or a billed route. When the allowance runs out, jobs wait without spending tries, and stuck lectures can be carded in a Claude session (`.claude/skills/mnemosyne-cards/stuck.md`). Long decks are sent in parts (`limits.chunk_pages`).
  - Chat and Telegram use the direct API.
  - Local qwen handles tagging and classification only; it never writes cards.
- Model IDs live only in config.
- Send lecture PDFs as documents (text plus page images). OCR text is only a fallback, because Paperless OCR is weak on handwriting.
- All generation returns JSON validated against the schema in SKILL.md. Invalid output is retried once, then goes to human review. Nothing is silently dropped.

## Style

- Small, typed, pure functions, with I/O at the edges. `anki/core_client.py` and `ledger/` are the only stateful modules.
- Every job is idempotent and logs a structured summary (`created`, `updated`, `skipped`, `rejected`).
- When unsure about Aman's preferences (tags, deck names, approval policy), check `config/` and PLAN.md first, then ask. Don't invent new conventions.

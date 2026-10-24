# Deploying mnemosyne on the Obelisk

mnemosyne runs as its own compose project (`compose.yaml`) from a clone of this repo at `~/helios/mnemosyne`. It joins the `helios_default` network, which is how it reaches `helios-anki-sync` (as `anki-sync`), and the Helios containers reach it as `mnemosyne:8110`. On the host, only the tailnet IP publishes port 8110.

| Service | What it does |
|---|---|
| `api` | FastAPI: `/inbox`, approvals, Telegram handlers, and the write job (runs after each approval and daily at 04:15) |
| `anki-core` | Vendored anki-sync-mcp (`services/anki-core`). The **only** server-side collection writer. It holds its own copy and syncs to the sync server as Aman's user. |
| `anki-core-test`, `integration` | Profile `test`. The same thing logged in as `mnemosyne-test`, with its own volume. Never touches the real collection. |

Sync topology: iPhone (AnkiMobile), ThinkPad (Desktop), and anki-core are three clients of the self-hosted sync server. Each owns its own copy (invariant 1). anki-core has no full-sync, schema, destructive, or admin tools (invariants 2, 3, 9).

## First deployment

1. **Clone** (Claude): `git clone git@github.com:almondshawarma/mnemosyne.git ~/helios/mnemosyne`
2. **Secrets** (Aman, once): `cd ~/helios/mnemosyne && bash scripts/obelisk_secrets.sh`. It creates `secrets/` and `.env`, and adds `ANKI_SYNC_TEST_USER`, `MNEMOSYNE_APPROVE_TOKEN`, and `MNEMOSYNE_PROPOSE_TOKEN` to the Helios `.env`. It prints only file names.
3. **Test user on the sync server** (Claude): add `SYNC_USER2: ${ANKI_SYNC_TEST_USER}` to `anki-sync` in `~/helios/stack/docker-compose.yml` (backup `docker-compose.yml.bak-mnemosyne`), then `docker compose up -d anki-sync`. This is a few seconds of sync downtime.
4. **Integration test**, test user only:
   ```bash
   docker compose --profile test run --rm integration python scripts/integration_p2.py seed
   ```
   ```bash
   ANKI_BOOTSTRAP_MODE=download_if_empty docker compose --profile test up -d anki-core-test
   ```
   ```bash
   docker compose --profile test run --rm integration python scripts/integration_p2.py run
   ```
   ```bash
   docker compose --profile test down
   ```
5. **Back up the real sync data** before anki-core first logs in as Aman: `cp -a ~/helios/stack/anki-sync/data ~/helios/backups/anki-sync-data-$(date +%F)`.
6. **Start**: `ANKI_BOOTSTRAP_MODE=download_if_empty docker compose up -d --build api anki-core`. On this first start, anki-core downloads the collection. It refuses to start if the server would require an upload.
   - Every later start uses the default, `disabled`: `download_if_empty` refuses a collection that already has cards, so anki-core would crash-loop. That happened at the first anki-core rebuild, 2026-09-26.
   - The persisted sync login keeps syncing either way.
   - The test instance works the same way: prefix its first `up` with the variable.
7. **Helios hooks** (Claude, with `.bak` copies; Aman commits when he reconciles homelab):
   - `helios-telegram`: every 30 s, announce `GET /telegram/outbox` batches with their buttons. Forward `mn|…` button presses to `POST /telegram/callback`.
   - `helios-mcp` (cloud-facing): `mnemosyne_propose_cards`, using the propose token.
   - `helios-mcp-local`: also `mnemosyne_approve_batch`, using the approve token.

## Day to day

- **P3 endpoints** (approve token):
  - `POST /pipeline/poll` runs a Paperless pass now.
  - `POST /pipeline/document/{id}` processes one document (a backlog lecture, say).
  - `POST /exams/sync` re-reads the calendar.
  - `POST /exams/map/{COURSE}` proposes an exam map from the syllabus, confirmed in Telegram.
  - `GET /exams` lists exams.
  - `POST /exams/decks` builds, refreshes, or removes the `~Exam::*` filtered decks now. It runs by itself at 04:15, after the card write.
  - `POST /schedule/run` rebuilds `~Walk::New`/`~Walk::Due` and sets today-only new limits now (`?dry_run=true` writes nothing). `GET /schedule` is the measured load. Both run by themselves at 04:15, after the exam decks.
  - Leech doctor: `POST /leeches/run` diagnoses now (`?dry_run=true` lists the candidates only). `GET /leeches` lists cases; `POST /leeches/{id}/decision` is the Leeches tab's call.
  - `POST /metrics/run` collects yesterday's metrics now. `POST /report/run` builds this week's report and returns it (`?deliver=true` sends it to Telegram within a minute).
- **Learner endpoints** (the propose token is enough, since they only read):
  - `GET /learner/profile` returns today's 04:15 snapshot (`?fresh=true` rebuilds it, which takes a few seconds).
  - `GET /learner/search?q=` searches cards by topic, with memory state.
  - `GET /learner/exams` and `GET /learner/stats`.
  - The MCP tools call these. After a hook change: `python3 ops/helios-hooks/patch_helios_p4.py`, then `docker compose up -d --build mcp mcp-local` in `~/helios/stack`.
- **Graded work** (approve token): `POST /graded/{doc_id}` debriefs a returned exam or homework now, and `?again=true` redoes it, say after adding a note in Paperless. `GET /graded` lists the debriefs. `POST /graded/{doc_id}/score` with `{"earned": 41, "possible": 50}` sets the real score when the scan can't show it all. `POST /mistakes/{id}/decision` takes `{"action": "confirm" | "wrong" | "fixed" | "reopen", "note": "…"}`.
- **Readings** (approve token):
  - `GET /readings` is the Read tab: books, scans waiting for a book and section, and recent runs. `GET /readings/books/{key}/outline` lists a book's sections.
  - `POST /readings` with `{book, where, doc_id?, again?}` cards a section in the background.
  - The api mounts the Helios volume `helios_cwa-library` (Calibre-Web-Automated's library) read-only at `/calibre`. Add a book to `config/books.yaml` with its Calibre id (the number in its folder name).
  - **Adding a book:**
    1. Put the PDF in Calibre: Calibre-Web's upload, or drop it in `~/helios/stack/books/ingest` on the Obelisk, where Calibre-Web-Automated imports it.
    2. Add it to `config/books.yaml` with its Calibre id, a name, aliases for scan titles, and `course:` if it's a current course's textbook.
    3. Check it: `books.outline` should list sections, and a printed page should land on the right PDF page. PDFs with page labels map by themselves; otherwise measure an `offset`. A PDF with no outline takes pages only.
       - Check that bookmarks land on their headings (Griffiths QM's were a page early: `outline_shift`), that sections have numbers (`number_sections` otherwise), and that the PDF is only the book (University Physics had another book merged after its index).
    4. Commit and redeploy the api.
    5. Tag it in Calibre: `python3 scripts/calibre_tags.py` shows what would change, `--apply` writes it.
- **Research papers:** a Paperless document of type Paper tagged `mnemosyne` is carded by the poller. `GET /papers` (propose token) lists recent ones with their prerequisites; `POST /papers/{doc_id}?again=` (approve token) cards one now.
- **Books for the tutor** (propose token; the `mnemosyne-books` skill):
  - `GET /books` lists the books with their status; `?book=<key>` gives one book's outline.
  - `GET /books/search?q=&book=` searches the full text. `GET /books/{key}/read?where=&images=auto|yes|no` returns a section's printed text with a citation, plus page images when the text can't carry the math. `GET /books/{key}/problems?where=&difficulty=` returns practice problems.
  - The full-text index is a cache in the `books_index` volume (`/cache/books.db`). The api warms it at startup and rebuilds a book when its PDF changes; deleting it is safe.
  - MCP tools: `python3 ops/helios-hooks/patch_helios_books.py`, then rebuild mcp and mcp-local.
- **Questions:**
  - `GET /questions` lists them (approve token). `POST /questions` adds one from a session (the propose token is enough; the MCP tool `mnemosyne_log_question` calls it).
  - `POST /questions/{id}/decision` takes `answered`, `dropped`, `reopen`, `card`, or `hyperion`.
  - `POST /questions/extract/{doc_id}` reads a filed lecture's handwritten questions without carding it again (backfill).
  - The api mounts the vault's `Sources/` at `/vault-sources`, and writes questions into the lecture stubs in `Sources/Paperless/` (`MNEMOSYNE_STUBS_DIR`). It reaches Hyperion through `http://mcp:8091/mcp`.
  - The MCP tool needs `python3 ops/helios-hooks/patch_helios_questions.py`, then a rebuild of mcp and mcp-local.
- **Metrics in Grafana:**
  1. Run `scripts/obelisk_secrets.sh` once more. It creates the `mnemosyne` bucket and a token scoped to it.
  2. Restart with `docker compose up -d api`.
  3. Import `ops/grafana/mnemosyne.json`, choosing the InfluxDB (Flux) data source.

  Until then `secrets/influx_token` is an empty placeholder, and metrics are kept in the Ledger only.
- **Review page endpoints** (approve token unless noted):
  - `POST /reviews` pushes a session batch; the propose token is enough, because nothing is approved on push.
  - `GET /reviews` and `GET /reviews/{id}`; `DELETE /reviews/{id}` removes a review only while nothing in it is decided.
  - `POST /reviews/{id}/items`, `PUT …/notes`, and `POST …/finish` are the page's calls.
  - `GET /reviews/{id}/export` and `POST …/applied` are Claude's.
  - `POST /batches/{id}/cards` decides single Waiting cards.
  - `POST /drafts/{id}/revise` edits a card that is already approved or in Anki. A reason is required, and the next write updates the note in place.
  - `POST /drafts/{id}/withdraw` takes back an approved card that never reached Anki.
  - `GET /review/decisions?since=` lists per-card decisions, with notes and what edits replaced.
- **Write job:** anki-core refuses a whole upsert when one new note's front duplicates an existing note. The job then halves the chunk until that note fails alone, so the rest still land. The note stays approved with its error, and shows at the top of the Waiting tab.
- **iPad:** `POST /intake` with the propose token. See `docs/ipad-shortcut.md`.
- **Review page:** `http://$HELIOS_HOST_IP:8110/review` from any device on the tailnet. The Obelisk homepage has a tile for it. It has three tabs:
  - **From Claude:** batches Claude pushed to `/reviews` in a session (the old claude.ai viewer). See the skill's `review.md`.
  - **Waiting:** every pipeline card still held in the Ledger, with approve, edit, or reject per card.
  - **Exams:** exam maps to confirm, and each upcoming exam's lecture coverage (editable).
  - The page asks for the approve token once per device and keeps it in that browser. Aman copies the token from `secrets/approve_token` into Vaultwarden; Claude never handles it.
  - On iPhone or iPad, use Share → Add to Home Screen.
  - Decisions made here and in Telegram both work. Whichever comes second is told "Already …".
  - Edits pass the same structural checks as proposals. What a card said before, and any note, are kept in `card_draft.before_json` and `decision_note` for canon review.

- **Deploy a change:** `git pull && docker compose up -d --build api anki-core`. anki-core runs with `MCP_SCOPES=read,write,admin` since P4. mnemosyne's client allowlists the tools it may call (`src/mnemosyne/anki/core_client.py`).
- **Health:** `curl http://$HELIOS_HOST_IP:8110/health`.
- **Logs:** `docker compose logs -f api anki-core`.
- **Pending batches:** `curl -H "Authorization: Bearer $(cat secrets/approve_token)" http://$HELIOS_HOST_IP:8110/batches?status=pending`.
- **Viewer-approved batches** (`drafts/*.json`): `docker compose exec api mnemosyne propose /app/drafts/<id>.json --origin viewer`. Copy the file in first, or pipe it with `docker compose exec -T api mnemosyne propose /dev/stdin --origin viewer < drafts/<id>.json`.

## Media (IPA audio, ASL videos)

- **IPA:** `python3 scripts/ipa_audio.py` (on the Obelisk: it needs ffmpeg) refetches anything missing from `data/audio/ipa/`. Commit the result.
- **ASL:** `python3 scripts/asl_videos.py --first N`, run in `~/helios/mnemosyne`, fetches Signbank videos into `media/video/asl/`, which is gitignored and mounted read-only at `/media`. Create `media/` before the first `up`, or Docker makes it as root.
- **Cards:** `uv run scripts/ipa_cards.py` and `uv run scripts/asl_cards.py MANIFEST` build payloads for the review page. The writer attaches the media when a card is written.

## Animations and lecture videos

Both run from a Claude Code session over ssh; nothing is scheduled.
- **Animations:** `uv run scripts/render.py scenes/<course>/<name>.py [--final]` runs the pinned ManimCE image (pulled on
  first use) in a sandbox. Finals land in `media/render/<slug>/`, which `/watch` serves.
  - **Clearing old videos:** each clip's video is deleted `keep_days` after its last open; the default is in
    `config/clips.yaml`, and the page sets it per clip.
  - **The render runner** renders chats' scenes and cleared clips. Run `mkdir -p media/render media/render-queue`
    once, then add a line to Aman's crontab (back it up first):
    ```
    * * * * * flock -n /tmp/mn-render.lock /usr/bin/python3 /home/amans/helios/mnemosyne/scripts/render_runner.py >> /home/amans/helios/mnemosyne/media/render-queue/runner.log 2>&1 # mnemosyne-render-runner
    ```
    Each render runs in its own container from the pinned ManimCE image in `scripts/render.py`
    (`docs/security/clip-submit.md`).
  - **The MCP clip tools** need `python3 ops/helios-hooks/patch_helios_clips.py`, then a rebuild of mcp and mcp-local.
  - **The render image:** build it once, and again after any change to `services/manim/`:
    `docker build -t mnemosyne-manim:1 services/manim`.
  - **Aman's style layers:**
    - amanim is cloned at `~/helios/amanim`, and `~/bin/nightly-git-pull.sh` keeps it current. Renders and the api
      (`../amanim`, read-only) use it.
    - `scenes/brand.py` is his generated token file. Copy it in by hand (it's gitignored), or add mnemosyne to the
      `distribute:` list of `helios_tokens.yaml`.
  - **`mnemosyne_clip_toolkit`** needs `python3 ops/helios-hooks/patch_helios_toolkit.py`, then a rebuild of mcp and
    mcp-local.
- **Lecture videos:** the `watcher` service watches what the MCP tool `mnemosyne_video_request` queues.
  - Before its first `up`, create its folders as the host user: `mkdir -p media/watch media/watch-queue`.
  - Then run `docker compose up -d --build api watcher`. Rebuild the watcher after any change to `services/watch/`, for
    example a yt-dlp bump when YouTube breaks fetching.
  - Failures land in `media/watch-queue/<id>.failed`, and `docker compose logs watcher` shows them too.
  - From a session, `uv run scripts/watch.py <link>` runs the same image directly.
  - Each video's folder, `media/watch/<id>/`, takes about 16 MB for a lecture: the transcript, frames, and contact
    sheets. The video itself is never kept.
  - The MCP tools need `python3 ops/helios-hooks/patch_helios_videos.py`, then a rebuild of mcp and mcp-local.
- **Studies** (study mode, 2026-10-24): the same `watcher` takes `<id>@<start>-<end>.json` requests from
  `mnemosyne_video_study` and writes `media/watch/<id>/study/<start>-<end>/`, about 1 MB per second of span (40–60
  MB for a typical study: the dense frames). Re-watching a video keeps its studies.
  - The image has Debian's ffmpeg, which yt-dlp uses to fetch only the span; it's about 830 MB.
  - From a session, `uv run scripts/watch.py <link> --study 1:20-1:50` runs here with the image's pinned packages;
    `--on obelisk` runs it in the image.
  - The MCP tool needs `python3 ops/helios-hooks/patch_helios_video_study.py`, then a rebuild of mcp and mcp-local.

## If cards say "similarity check unavailable"

Dedupe couldn't reach Helios's Ollama, so it fell back to exact matching and held every card for review. Nothing is lost.
- **Check the container:** `docker ps -a | grep ollama`.
- **Check the GPU:** `nvidia-smi`. "Driver/library version mismatch" means an NVIDIA update replaced the libraries under a running kernel module, and only a reboot fixes it. Reboot outside 04:00–04:45 Chicago time.
- **Ollama running on CPU:** the homelab's `tools/ollama-gpu-guard.sh` (cron, every 15 minutes) restarts it.
- **Re-checking held cards:** once Ollama is back, held cards can be re-checked by hand (`dedupe.classify` against `dedupe.index`), as on 2026-10-04 for Chen 1.4.

## If anki-core reports a full sync

A full sync is always Aman's decision, made on the desktop (invariant 3). anki-core stops writing and reports `FULL_SYNC required`. Resolve it on the ThinkPad, then remove anki-core's copy so it re-downloads:

```bash
docker compose stop anki-core
```

```bash
docker volume rm mnemosyne_anki_core_data
```

```bash
docker compose up -d anki-core
```

## Anki backups

`ops/anki-backup.sh` runs from Aman's crontab at 07:30 UTC, which is 02:30 in Chicago in summer and 01:30 in winter. That's clear of the 04:00 rollover, mnemosyne's 04:15 job, and the Helios stack backup at 08:00 UTC.
- It stops only `helios-anki-sync` for a few seconds and archives `~/helios/stack/anki-sync/data`. That's the collection every device syncs to, plus its media.
- It checks the copy (`PRAGMA integrity_check`, plus note, card, and review counts), then keeps it.
- Archives go to `/mnt/data/helios/backups/anki/`: `daily/` keeps the newest 30, and `monthly/` keeps the first archive of each month for 12 months.
- The log is `/mnt/data/helios/backups/anki/backup.log`.

The Helios stack bundle (`~/helios/helios-backup.sh`, 7 kept) also contains this directory. These archives are the small, frequent, Anki-only copies.

**Restore** (a human decision, like a full sync):
1. Pick the archive.
2. Stop the sync server: `cd ~/helios/stack && docker compose stop anki-sync`.
3. Move the current data aside: `mv anki-sync/data anki-sync/data.before-restore`.
4. Unpack the archive: `tar xzf /mnt/data/helios/backups/anki/daily/<archive>.tar.gz -C anki-sync`.
5. Start it again: `docker compose start anki-sync`.
6. Every device, and anki-core, now differs from the server. On the ThinkPad, sync and choose **Download** from the server. Do the same on the phone and iPad.
7. Reset anki-core as in the section above.

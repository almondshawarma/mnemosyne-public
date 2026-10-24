# anki-core: vendored anki-sync-mcp

Upstream: https://github.com/raslab/anki-sync-mcp at `57624bf` (2026-07-27), MIT (see `LICENSE`).
Vendored rather than forked on GitHub so it stays private with mnemosyne. Reviewed before use
(PLAN §11): a single worker thread owns the collection, every mutation carries an idempotency
receipt and syncs before and after, a server-requested full sync raises instead of running, and
full-sync, schema, destructive, and review-answer tools are off unless their flags are set.

## Patches

1. **`ANKI_SYNC_INSECURE_HOSTS`** (`config.py`). Upstream allows plain HTTP only to loopback.
   Aman's sync server (`helios-anki-sync`) speaks HTTP on the private Docker network, so hosts
   named in this variable may use `http://`. Everything else still requires HTTPS.
2. **`anki_notes_upsert`** (`collection.py` `upsert_notes`, `app.py`). Creates or updates notes
   keyed on one field's value, which for mnemosyne is `MnemosyneID`. The whole batch is validated
   before anything is written. Existing notes keep their deck, scheduling, and extra tags
   (e.g. `marked`); new notes need a deck that already exists. Tests: `tests/test_mnemosyne_patches.py`.

3. **Filtered decks** (`collection.py`, `app.py`), added 2026-09-26 for P4 exam and walk decks:
   - `anki_filtered_decks_upsert` creates or updates a filtered deck by name, and builds it.
   - `anki_filtered_decks_rebuild` rebuilds one by name.
   - `anki_filtered_decks_remove` empties one (its cards go home with their history) and deletes the
     empty deck.

   All three are at admin scope. They refuse a name that belongs to a normal deck, leave out the
   broken relative-overdueness order, and refuse retrievability order while FSRS is off. Tests:
   `tests/test_mnemosyne_patches.py`.

`tests/test_app.py` and `tests/test_phase1_app.py` list the new tools in their inventories, and the
README documents the new variable.

## Known upstream failure

`test_fsrs_reschedule_fingerprint_ignores_history_before_configured_date` fails on untouched
upstream too (checked 2026-09-25), so it's marked `xfail`. mnemosyne never enables FSRS rescheduling.

## Updating

Re-vendor with `git archive <sha> | tar -x`, re-apply the patches above, and run
`uv run pytest && uv run ruff check . && uv run pyright` in this directory.

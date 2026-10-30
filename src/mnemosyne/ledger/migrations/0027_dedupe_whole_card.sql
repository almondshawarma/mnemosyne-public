-- Dedupe on the whole card (Aman, 2026-10-29; docs/embeddings.md E1). Notes are compared by what they ask and answer,
-- not their first field, so each note's fields are cached here and refreshed incrementally (new notes, and notes Anki
-- says were edited since the last look). Every match is recorded, and a dropped card keeps its whole payload, so the
-- weekly audit reviews the week's drops (and can restore one) instead of Aman reviewing them as they happen.
CREATE TABLE note_text (
  note_id     INTEGER PRIMARY KEY,
  note_type   TEXT NOT NULL,
  fields_json TEXT NOT NULL,
  tags_json   TEXT NOT NULL,
  card_ids    TEXT NOT NULL,           -- JSON
  updated_at  TEXT NOT NULL
);
CREATE TABLE note_text_sync (
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  checked_at  TEXT NOT NULL,           -- the last look at the collection
  full_at     TEXT NOT NULL            -- the last time every note was read again
);
CREATE TABLE dedupe_match (
  content_hash TEXT PRIMARY KEY,       -- card_draft.content_hash
  origin       TEXT NOT NULL,
  kind         TEXT NOT NULL,          -- duplicate | flag | new
  score        REAL NOT NULL,
  note_id      INTEGER,                -- the nearest note (negative: a draft); NULL within a batch or unchecked
  matched      TEXT,
  dropped      INTEGER NOT NULL DEFAULT 0,
  card_json    TEXT,                   -- a dropped card in full (with its source), so the audit can restore it
  model        TEXT NOT NULL,
  repr         TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

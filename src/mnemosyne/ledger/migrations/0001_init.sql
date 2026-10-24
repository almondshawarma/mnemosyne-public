-- mnemosyne tables in the Helios Ledger (helios.db). Drafts live here, never in Anki (invariant 5).

CREATE TABLE card_source (
  id            INTEGER PRIMARY KEY,
  payload_hash  TEXT NOT NULL UNIQUE,          -- re-proposing the same payload is a no-op
  origin        TEXT NOT NULL,                 -- mcp | telegram | inbox | viewer | paperless
  kind          TEXT NOT NULL,                 -- slides | notes | chat | ... (SKILL.md source.kind)
  course        TEXT,
  lecture       INTEGER,
  ref           TEXT,
  link          TEXT,
  received_at   TEXT NOT NULL
);

CREATE TABLE card_batch (
  id            TEXT PRIMARY KEY,              -- short id shown in Telegram
  source_id     INTEGER NOT NULL REFERENCES card_source(id),
  status        TEXT NOT NULL,                 -- pending | approved | rejected | written | empty
  decided_by    TEXT,
  decided_at    TEXT,
  notified_at   TEXT,                          -- when Telegram was told about it
  message_ref   TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX ix_card_batch_status ON card_batch(status);

CREATE TABLE card_draft (
  id            INTEGER PRIMARY KEY,
  mnemosyne_id  TEXT NOT NULL UNIQUE,          -- UUIDv7; every Anki write is an upsert on it
  batch_id      TEXT NOT NULL REFERENCES card_batch(id),
  content_hash  TEXT NOT NULL UNIQUE,          -- same card proposed twice becomes one draft
  note_type     TEXT NOT NULL,
  deck          TEXT NOT NULL,
  fields_json   TEXT NOT NULL,
  tags_json     TEXT NOT NULL,
  mode          TEXT NOT NULL,
  checks_json   TEXT NOT NULL DEFAULT '[]',
  confidence    REAL,
  status        TEXT NOT NULL,                 -- pending | approved | rejected | written | error
  review_reason TEXT,                          -- why it needs review, or why it was rejected
  note_id       INTEGER,                       -- Anki note id once written
  error         TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  written_at    TEXT
);
CREATE INDEX ix_card_draft_status ON card_draft(status);
CREATE INDEX ix_card_draft_batch ON card_draft(batch_id);

CREATE TABLE course (
  code          TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  paperless_tag TEXT,
  deck          TEXT NOT NULL DEFAULT 'Now',
  active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE exam (
  id                  INTEGER PRIMARY KEY,
  course              TEXT NOT NULL,
  kind                TEXT NOT NULL,           -- exam | quiz
  title               TEXT NOT NULL,
  starts_at           TEXT NOT NULL,
  calendar_source_id  TEXT UNIQUE,             -- calendar_event (source, source_id), deduplicated
  lectures            TEXT,                    -- JSON list of lecture numbers, once mapped
  confirmed           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE ingest_job (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,                 -- paperless | obsidian | ...
  ref           TEXT NOT NULL,
  status        TEXT NOT NULL,                 -- queued | running | done | error
  attempts      INTEGER NOT NULL DEFAULT 0,
  error         TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  UNIQUE (kind, ref)
);

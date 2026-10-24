-- In-session reviews on /review (what the claude.ai viewer used to hold): a batch Claude wrote in a session,
-- reviewed card by card with reasons, notes, and left-out items to promote, then handed back to Claude.
CREATE TABLE review_set (
  id            TEXT PRIMARY KEY,              -- kebab-case, e.g. nuen201-l08-2026-10-02
  title         TEXT NOT NULL,
  kind          TEXT NOT NULL,                 -- lecture | golden | cards
  payload_json  TEXT NOT NULL,                 -- source, signals, skipped, canon titles; never changed after the push
  notes         TEXT NOT NULL DEFAULT '',
  promote_json  TEXT NOT NULL DEFAULT '[]',    -- indexes into skipped
  status        TEXT NOT NULL,                 -- open | reviewed | applied
  created_at    TEXT NOT NULL,
  finished_at   TEXT,
  applied_at    TEXT
);

CREATE TABLE review_item (
  review_id     TEXT NOT NULL REFERENCES review_set(id),
  key           TEXT NOT NULL,
  position      INTEGER NOT NULL,
  card_json     TEXT NOT NULL,                 -- the card as Claude wrote it (key, why, golden, context, question)
  mnemosyne_id  TEXT,                          -- its draft, new or already in the Ledger; NULL when it has none
  problems      TEXT,                          -- why it has no draft (schema problems, duplicate of a note)
  verdict       TEXT,                          -- approve | edit | reject; NULL until Aman decides
  reason        TEXT,
  edits_json    TEXT,
  decided_at    TEXT,
  PRIMARY KEY (review_id, key)
);

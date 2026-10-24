-- P4: the leech doctor, daily metrics, and the Sunday report (PLAN §9).
-- A leech case is one diagnosis of one card. A card gets a new case only when it lapses again after the last one
-- closed, or when Aman marks it again.
CREATE TABLE leech_case (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id        INTEGER NOT NULL,
  note_id        INTEGER NOT NULL,
  mnemosyne_id   TEXT,                      -- the note's MnemosyneID (every note has one since P1)
  note_type      TEXT NOT NULL,
  deck           TEXT NOT NULL,             -- home deck, even while the card sits in a filtered deck
  fields_json    TEXT NOT NULL,             -- the note as found
  tags_json      TEXT NOT NULL,
  signals_json   TEXT NOT NULL,             -- lapses, difficulty, retrievability, marked, recent ratings, similar notes
  lapses         INTEGER NOT NULL,
  status         TEXT NOT NULL,             -- proposed | fixed | kept | dismissed | failed
  diagnosis      TEXT,                      -- ambiguous_cue | not_atomic | interference | missing_prerequisite | too_long | fine
  explanation    TEXT,
  action         TEXT,                      -- rewrite | replace | add | keep
  proposal_json  TEXT,                      -- {"fields": {...}} for rewrite, {"cards": [...]} for replace/add
  problems_json  TEXT,                      -- schema or number-check problems in the proposal
  decided_by     TEXT,
  decision_note  TEXT,
  applied_json   TEXT,                      -- what was written, suspended, untagged
  found_at       TEXT NOT NULL,
  decided_at     TEXT
);
CREATE INDEX leech_case_card ON leech_case (card_id);

CREATE TABLE metric_day (
  day           TEXT PRIMARY KEY,           -- the Anki day the metrics describe (yesterday at 04:15)
  metrics_json  TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE TABLE learner_snapshot (
  id            INTEGER PRIMARY KEY CHECK (id = 1),   -- one row: the latest profile, rebuilt at 04:15 or on demand
  profile_json  TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE TABLE report (
  week           TEXT PRIMARY KEY,          -- the Sunday it covers up to, e.g. 2026-10-04
  text           TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  deliver_after  TEXT NOT NULL,             -- UTC; Telegram's outbox holds it until then
  notified_at    TEXT,
  message_ref    TEXT
);

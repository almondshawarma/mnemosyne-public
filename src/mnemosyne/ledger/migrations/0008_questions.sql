-- Aman's questions: from his slide annotations (the lecture pipeline) or logged in a session. Kept until he's answered,
-- carded, or dropped them; mirrored into a note per lecture under the vault's Sources/Lectures/.
CREATE TABLE question (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  origin          TEXT NOT NULL,             -- lecture | session
  doc_id          INTEGER,                   -- the Paperless lecture it was written on
  course          TEXT,
  lecture         INTEGER,
  lecture_title   TEXT,                      -- the Paperless title, e.g. "PHYS 207 L07 - Capacitors"
  lecture_date    TEXT,
  page            INTEGER,
  asked           TEXT NOT NULL,             -- his words
  about           TEXT,
  kind            TEXT NOT NULL,             -- clarify | mechanism | predict | connect | apply | verify
  answer          TEXT,
  sharper         TEXT,
  beyond_course   INTEGER NOT NULL DEFAULT 0,
  card_json       TEXT,                      -- a proposed card, if the answer is worth keeping
  status          TEXT NOT NULL,             -- open | answered | carded | dropped
  note            TEXT,
  draft_id        TEXT,                      -- the card's MnemosyneID once carded
  hyperion_id     TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  UNIQUE (doc_id, asked)
);
CREATE INDEX question_status ON question (status);

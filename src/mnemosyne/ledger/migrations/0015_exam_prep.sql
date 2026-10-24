-- Exam prep material (prep.py, 2026-10-07): a review sheet, practice exam, or key Aman filed in Paperless, attached
-- to its exam and read once for its problems. Never carded: cards come from understanding, after he works it.
CREATE TABLE exam_material (
  doc_id         INTEGER PRIMARY KEY,       -- the Paperless document
  exam_id        INTEGER,                   -- the calendar exam it prepares for; NULL when none matched yet
  course         TEXT NOT NULL,
  kind           TEXT NOT NULL,             -- review | practice | key
  title          TEXT NOT NULL,
  link           TEXT,
  summary        TEXT,
  scope_json     TEXT NOT NULL DEFAULT '[]',     -- what it says the exam covers, as written
  format         TEXT,                      -- the exam's format, when stated
  rules          TEXT,                      -- what's allowed in the room, when stated
  emphasis_json  TEXT NOT NULL DEFAULT '[]',     -- the professor's emphasis and Aman's marks, quoted
  problems_json  TEXT NOT NULL DEFAULT '[]',     -- each problem: number, summary, format, lectures, topics, his status
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX exam_material_exam ON exam_material (exam_id);

-- Probes (the mnemosyne-exam skill): a transfer question on a card Aman holds. A pass makes the card a hook to build
-- on, a miss shows a card he can recall but not use. Only adds, like mistake attempts.
CREATE TABLE probe (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  course      TEXT NOT NULL,
  exam_id     INTEGER,
  lecture     INTEGER,
  topic       TEXT,                          -- a topic:: tag
  hook        TEXT NOT NULL,                 -- what he holds: the card's prompt, or the idea
  note_id     INTEGER,                       -- the card's Anki note, when the hook is one card
  question    TEXT NOT NULL,                 -- the step up he was asked to take
  right       INTEGER NOT NULL,
  hints       INTEGER NOT NULL DEFAULT 0,
  note        TEXT,                          -- what broke, or what he did
  origin      TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX probe_course ON probe (course, created_at)

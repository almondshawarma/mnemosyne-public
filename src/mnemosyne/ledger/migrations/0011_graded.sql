-- Graded work (PLAN §4.6, 2026-10-06): a returned exam, quiz, or homework, and each mistake read off it. The tutor
-- reads mistakes through the learner profile; error cards come from them; the debrief goes in the document's stub.
CREATE TABLE graded_work (
  doc_id           INTEGER PRIMARY KEY,       -- the Paperless document
  course           TEXT NOT NULL,
  kind             TEXT NOT NULL,             -- exam | quiz | homework
  title            TEXT NOT NULL,             -- the Paperless title, e.g. "PHYS 207 Exam 01"
  exam_id          INTEGER,                   -- the calendar exam it was, when one matches
  taken_on         TEXT,                      -- the exam's date
  earned           REAL,
  possible         REAL,
  readiness_before REAL,                      -- the exam deck's readiness the day before (calibration)
  feedback         TEXT,                      -- key | rubric | comments | none: what the grading left to go on
  summary          TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE mistake (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id           INTEGER NOT NULL,
  course           TEXT NOT NULL,
  question         TEXT NOT NULL,             -- "5" (his copy's number), with the key's id in qid when there is one
  qid              TEXT,
  possible         REAL,
  earned           REAL,
  kind             TEXT NOT NULL,             -- conceptual | fact | procedure | careless | time | misread
  concept          TEXT NOT NULL,
  lecture          INTEGER,
  topic            TEXT,                      -- a topic:: tag
  his_answer       TEXT,
  correct_answer   TEXT,
  his_words        TEXT,                      -- what Aman (or the grader) wrote about it, verbatim
  trap             TEXT,
  fix              TEXT,
  why              TEXT,
  evidence         TEXT NOT NULL,             -- his_note | grader_comment | rubric | key_only | none
  confirmed        INTEGER NOT NULL DEFAULT 0, -- 1 once the diagnosis rests on his note or the grader's, or he said so
  card_ids         TEXT,                      -- JSON list of the MnemosyneIDs carded from it
  status           TEXT NOT NULL DEFAULT 'open',  -- open | drilled | fixed
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  UNIQUE (doc_id, question)
);
CREATE INDEX mistake_course ON mistake (course, created_at);

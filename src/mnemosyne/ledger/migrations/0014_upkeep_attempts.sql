-- When Aman decided each card on the review page. updated_at moves again when the card is written, so it can't time
-- the decision; the weekly report's upkeep line (minutes spent curating) needs the moment itself.
ALTER TABLE card_draft ADD COLUMN decided_at TEXT;

-- Fresh problems on a graded mistake (the tutor's "same trap, new surface"): every try, right or wrong, so the
-- learner model can tell "never tried" from "tried and missed twice". Only adds; the mistake's status stays Aman's.
CREATE TABLE mistake_attempt (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  mistake_id  INTEGER NOT NULL REFERENCES mistake(id),
  right       INTEGER NOT NULL,              -- 1 when he reached the right answer
  hints       INTEGER NOT NULL DEFAULT 0,    -- 1 when he needed a hint to get there
  problem     TEXT,                          -- the fresh problem, in a sentence (or the book problem it adapts)
  note        TEXT,                          -- what went wrong, or what he did differently this time
  origin      TEXT NOT NULL,                 -- mcp | review-page | ...
  created_at  TEXT NOT NULL
);
CREATE INDEX mistake_attempt_mistake ON mistake_attempt (mistake_id, created_at)

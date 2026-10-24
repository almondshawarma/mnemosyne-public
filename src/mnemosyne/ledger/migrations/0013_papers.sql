-- Research papers Aman files in Paperless (type Paper, tagged mnemosyne): what each was carded as, and what it
-- assumes he knows, checked against his cards and his textbooks (papers.py). The stub's "Before you read" reads it.
CREATE TABLE paper (
  doc_id         INTEGER PRIMARY KEY,       -- the Paperless document
  slug           TEXT NOT NULL,             -- src::paper::<slug>; copies of one paper share it
  title          TEXT NOT NULL,
  year           INTEGER,
  course         TEXT,                      -- set when the paper came with a course tag
  prerequisites  TEXT NOT NULL DEFAULT '[]', -- JSON: [{concept, why, search, cards, weak, mean_r, book}]
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX paper_slug ON paper (slug)

-- Page fingerprints of every lecture PDF the pipeline has carded, so a deck sent again (with more notes on it, or
-- reused for the next lecture) replaces its old copy and is carded only where it changed.
CREATE TABLE deck_page (
  doc_id    INTEGER NOT NULL,             -- the Paperless document
  course    TEXT NOT NULL,
  page      INTEGER NOT NULL,
  text_key  TEXT NOT NULL,                -- hash of the printed text; '' when a page has too little
  thumb     BLOB NOT NULL,                -- small grayscale render, zlib-compressed
  PRIMARY KEY (doc_id, page)
);
CREATE INDEX deck_page_course ON deck_page (course, text_key);

-- A new copy of a deck replaced an older one (the older one went to Paperless's trash).
CREATE TABLE doc_version (
  doc_id    INTEGER PRIMARY KEY,
  replaces  INTEGER NOT NULL,
  at        TEXT NOT NULL
);

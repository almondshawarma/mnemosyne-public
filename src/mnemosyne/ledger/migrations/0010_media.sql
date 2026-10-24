-- Media files mnemosyne has stored in the collection (mn_-prefixed), so each is uploaded once and re-sent only
-- when its content changes.
CREATE TABLE media_file (
  filename   TEXT PRIMARY KEY,
  sha256     TEXT NOT NULL,
  stored_at  TEXT NOT NULL
);

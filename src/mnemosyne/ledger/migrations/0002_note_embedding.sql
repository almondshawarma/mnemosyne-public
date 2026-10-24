-- Embedding cache for dedupe: one row per Anki note, keyed on the note and the text it embedded.
CREATE TABLE note_embedding (
  note_id     INTEGER PRIMARY KEY,
  text_hash   TEXT NOT NULL,
  vector      BLOB NOT NULL,           -- float32 little-endian
  updated_at  TEXT NOT NULL
);

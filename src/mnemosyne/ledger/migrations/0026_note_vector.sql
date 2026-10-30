-- Card embeddings for semantic neighbours (docs/embeddings.md, 2026-10-29): one row per note, model, and text
-- representation, keyed on the text so an edit re-embeds. Separate from note_embedding, which dedupe keeps on the first
-- field until E1 decides what it should embed.
CREATE TABLE note_vector (
  note_id     INTEGER NOT NULL,
  model       TEXT NOT NULL,
  repr        TEXT NOT NULL,           -- full | front | texwords (neighbors.note_text)
  text_hash   TEXT NOT NULL,
  text        TEXT NOT NULL,           -- what was embedded: the reranker reads it back
  card_ids    TEXT NOT NULL,           -- JSON: the note's cards, first one shown
  vector      BLOB NOT NULL,           -- float32 little-endian, unit length
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (note_id, model, repr)
);

-- Cross-course duplicates (2026-10-21, Aman): a held card remembers the Anki note dedupe matched, so the review page
-- can merge it there (its tags join that note) or merge up (that note is rewritten in this card's more fundamental
-- framing, keeping its history). A merged draft ends with status 'merged', not 'rejected'.
ALTER TABLE card_draft ADD COLUMN merge_into INTEGER;
ALTER TABLE card_draft ADD COLUMN merge_up INTEGER NOT NULL DEFAULT 0;

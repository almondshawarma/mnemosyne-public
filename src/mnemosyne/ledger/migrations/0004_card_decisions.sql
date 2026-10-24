-- Per-card decisions from the review page (/review): who decided, why, and what an edited card said before.
ALTER TABLE card_draft ADD COLUMN decided_by TEXT;
ALTER TABLE card_draft ADD COLUMN decision_note TEXT;
ALTER TABLE card_draft ADD COLUMN before_json TEXT;

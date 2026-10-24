-- What a lecture's generation chose not to card, and why (the canon's "skipped" list, 2026-10-07). Kept so the
-- weekly audit can check the skips: a must-card mark skipped, or load-bearing content called padding.
ALTER TABLE card_batch ADD COLUMN skipped_json TEXT;

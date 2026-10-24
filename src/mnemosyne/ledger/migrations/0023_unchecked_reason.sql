-- Chat and Telegram proposals go through the verify gate and dedupe now (pipeline.propose_checked, 2026-10-20).
-- Drafts held before that carried a P2 placeholder that claimed the checks were still to come; say what happened.
UPDATE card_draft
   SET review_reason = 'unchecked: proposed before chat and Telegram cards went through the checks, so neither numbers nor duplicates were checked'
 WHERE review_reason = 'verify_skipped: numeric and dedupe checks arrive in P3';

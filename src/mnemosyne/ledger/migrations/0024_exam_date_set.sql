-- Aman can move an exam from the review page (2026-10-20: NUEN 201 Exam II moved to Nov 3 before the calendar knew).
-- date_set_by: who set starts_at by hand (NULL = the calendar). The calendar sync leaves a hand-set date alone until
-- the calendar agrees with it, and records what the calendar says in calendar_starts_at so the page can show both.
ALTER TABLE exam ADD COLUMN date_set_by TEXT;
ALTER TABLE exam ADD COLUMN calendar_starts_at TEXT;

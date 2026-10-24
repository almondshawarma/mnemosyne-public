-- P4 load governor (PLAN §5.3). load_day is measured study time per Anki day, read back from the review log
-- each morning; it is what the "±15% of budget for 2 weeks" check reads. governor_run is what the 04:15 job
-- decided that day and why.
CREATE TABLE load_day (
  day           TEXT PRIMARY KEY,              -- local calendar date of the Anki day (rollover 04:00)
  reviews       INTEGER NOT NULL,
  minutes       REAL NOT NULL,
  measured_at   TEXT NOT NULL
);

CREATE TABLE governor_run (
  day                  TEXT PRIMARY KEY,
  seconds_per_review   REAL NOT NULL,
  projected_reviews    REAL NOT NULL,         -- mean per day over the next 7 days, overdue counted on day 0
  new_total            INTEGER NOT NULL,
  plan_json            TEXT NOT NULL,         -- allocation, walk and home splits, backlog flag
  applied_json         TEXT NOT NULL,         -- what anki-core was asked to do, and the walk deck counts
  created_at           TEXT NOT NULL
);

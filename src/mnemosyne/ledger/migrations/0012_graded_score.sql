-- The score Aman gave, when the scan couldn't show all of it; a rerun of the debrief keeps it.
ALTER TABLE graded_work ADD COLUMN score_set INTEGER NOT NULL DEFAULT 0;

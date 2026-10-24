-- A formula sheet the course hands out (prep.py kind 'sheet', 2026-10-07): every item on it, as printed, so the
-- cram sheet can say what isn't on it.
ALTER TABLE exam_material ADD COLUMN contents_json TEXT NOT NULL DEFAULT '[]'

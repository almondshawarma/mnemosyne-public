-- One proposed exam map per course (from the syllabus), confirmed by Aman in Telegram (PLAN §4.5).
CREATE TABLE exam_map (
  course        TEXT PRIMARY KEY,
  doc_id        INTEGER,                 -- the Paperless syllabus it came from
  status        TEXT NOT NULL,           -- pending | confirmed | rejected
  detail_json   TEXT NOT NULL,           -- [{exam title, starts_at, lectures, topics}]
  notified_at   TEXT,
  message_ref   TEXT,
  created_at    TEXT NOT NULL,
  decided_at    TEXT
);

-- One-off Telegram notices (2026-10-07): the daily "lectures are waiting on the gateway" nudge. A row means it was
-- sent, so it goes out at most once per key (one key per day).
CREATE TABLE notice (
  key          TEXT PRIMARY KEY,           -- e.g. stuck:2026-10-07
  sent_at      TEXT NOT NULL,
  message_ref  TEXT
)

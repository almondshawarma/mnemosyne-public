-- Scenes a chat submitted to be rendered (clips.submit, 2026-10-13): the rate limit counts these, and they're the
-- record of what code ran in the render sandbox (docs/security/clip-submit.md).
CREATE TABLE clip_submission (
    id           INTEGER PRIMARY KEY,
    slug         TEXT NOT NULL,
    by           TEXT NOT NULL,
    code_sha     TEXT NOT NULL,
    bytes        INTEGER NOT NULL,
    submitted_at TEXT NOT NULL
);
CREATE INDEX clip_submission_at ON clip_submission (submitted_at);

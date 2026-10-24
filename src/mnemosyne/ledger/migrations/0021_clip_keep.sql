-- How long each animated clip keeps its video (clips.py, Aman 2026-10-13). No row: the default from
-- config/clips.yaml. `days` NULL also means the default; `forever` 1 keeps it for good. `cleared_at` is when the video
-- was last deleted (the code, stills, and metadata always stay, so it can be rendered again).
CREATE TABLE clip_keep (
    slug       TEXT PRIMARY KEY,
    days       INTEGER,
    forever    INTEGER NOT NULL DEFAULT 0,
    cleared_at TEXT,
    updated_at TEXT NOT NULL
);

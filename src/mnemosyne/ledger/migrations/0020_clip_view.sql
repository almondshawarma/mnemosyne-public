-- Opens of an animated clip on /watch (clips.py, 2026-10-13). `via` is where the open came from: a card's link, the
-- page itself, or a study session. The weekly audit reads these to judge whether clips earn a place on cards (the
-- desktop session for a field: docs/video-plan.md, V4).
CREATE TABLE clip_view (
    id        INTEGER PRIMARY KEY,
    slug      TEXT NOT NULL,
    via       TEXT NOT NULL,
    viewed_at TEXT NOT NULL
);
CREATE INDEX clip_view_at ON clip_view (viewed_at);

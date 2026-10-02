-- Songs users imported (Spotify/YouTube) that the catalog lacks; the preview job and
-- desktop analyzers work this queue. Keyed by ISRC when known, else Deezer id.
CREATE TABLE IF NOT EXISTS wanted (
    key         text PRIMARY KEY,
    isrc        text,
    deezer_id   text,
    title       text NOT NULL,
    artist      text NOT NULL,
    duration_s  real,
    preview_url text,
    requests    int NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wanted_requests ON wanted (requests DESC);
CREATE INDEX IF NOT EXISTS recordings_deezer ON recordings ((external_ids ->> 'deezer'));

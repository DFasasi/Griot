-- Griot schema v1. The canonical merged TrackFeatures document lives in `recordings.doc`;
-- hot fields are denormalized into columns for filtering and vector search.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE recordings (
    id               text PRIMARY KEY,          -- MBID, or "fma:<id>", "local:<sha1>"
    mbid             uuid,
    isrc             text,
    title            text NOT NULL,
    artist           text NOT NULL,
    artist_ids       text[] NOT NULL DEFAULT '{}',
    duration_s       real NOT NULL,
    year             int,
    explicit         boolean,
    tier             text NOT NULL CHECK (tier IN ('A', 'A-open', 'B', 'C')),
    full_audio       boolean NOT NULL,
    analyzer_version text NOT NULL,
    bpm              real NOT NULL,
    camelot          text NOT NULL,
    lufs             real NOT NULL,
    valence          real,
    arousal          real,
    lastfm_playcount bigint,
    lb_listens       bigint,
    deezer_rank      int,
    preview_url      text,
    external_ids     jsonb NOT NULL DEFAULT '{}',
    emb_full         vector(512),
    emb_intro        vector(512),
    emb_outro        vector(512),
    lyr_open         vector(384),
    lyr_close        vector(384),
    doc              jsonb NOT NULL,             -- full TrackFeatures (structure, trajectories, ...)
    n_submissions    int NOT NULL DEFAULT 1,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX recordings_isrc ON recordings (isrc);
CREATE INDEX recordings_search ON recordings USING gin ((title || ' ' || artist) gin_trgm_ops);
CREATE INDEX recordings_pop ON recordings (lastfm_playcount);
CREATE INDEX recordings_emb_full ON recordings USING hnsw (emb_full vector_cosine_ops);
CREATE INDEX recordings_emb_intro ON recordings USING hnsw (emb_intro vector_cosine_ops);

-- Raw per-user analyses; merged into `recordings` (median / outlier rejection).
CREATE TABLE submissions (
    id           bigserial PRIMARY KEY,
    recording_id text NOT NULL,
    submitter    text NOT NULL,
    doc          jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recording_id, submitter)
);

CREATE TABLE bridges (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request    jsonb NOT NULL,
    response   jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Per-transition thumbs; the training signal for fitting cost weights.
CREATE TABLE bridge_feedback (
    id         bigserial PRIMARY KEY,
    bridge_id  uuid NOT NULL REFERENCES bridges (id) ON DELETE CASCADE,
    position   int NOT NULL,                     -- transition index within the bridge
    rating     smallint NOT NULL CHECK (rating IN (-1, 1)),
    created_at timestamptz NOT NULL DEFAULT now()
);

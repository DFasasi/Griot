-- Synced libraries, keyed by sha256 of the user's private sync code (the code itself is never stored).
CREATE TABLE IF NOT EXISTS libraries (
    id         text PRIMARY KEY,
    entries    jsonb NOT NULL DEFAULT '{}',   -- entry key -> library entry
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

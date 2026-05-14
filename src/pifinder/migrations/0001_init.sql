PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS firms (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id            TEXT UNIQUE,
    name                TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,
    address             TEXT,
    city                TEXT,
    state               TEXT,
    postal_code         TEXT,
    country             TEXT DEFAULT 'US',
    latitude            REAL,
    longitude           REAL,
    phone               TEXT,
    website             TEXT,
    email               TEXT,
    rating              REAL,
    user_ratings_total  INTEGER,
    business_status     TEXT,
    attorney_count      INTEGER,
    has_pi_practice_page INTEGER,
    last_website_post_at TEXT,
    established_year    INTEGER,
    discovered_via      TEXT NOT NULL,
    first_seen_at       TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at        TEXT NOT NULL DEFAULT (datetime('now')),
    raw_json            TEXT
);

CREATE INDEX IF NOT EXISTS idx_firms_normalized_name ON firms(normalized_name);
CREATE INDEX IF NOT EXISTS idx_firms_state_city      ON firms(state, city);
CREATE INDEX IF NOT EXISTS idx_firms_lat_lng         ON firms(latitude, longitude);

CREATE TABLE IF NOT EXISTS attorneys (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id           INTEGER NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    title             TEXT,
    practice_areas    TEXT,          -- json array
    bio_url           TEXT,
    years_practicing  INTEGER,
    bar_admissions    TEXT,          -- json array
    source            TEXT NOT NULL,
    UNIQUE(firm_id, name)
);

CREATE INDEX IF NOT EXISTS idx_attorneys_firm ON attorneys(firm_id);

CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id      INTEGER NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,         -- meta_ad / news / hiring / review / website
    source       TEXT NOT NULL,
    observed_at  TEXT NOT NULL,
    summary      TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_signals_firm_kind ON signals(firm_id, kind);
CREATE INDEX IF NOT EXISTS idx_signals_observed  ON signals(observed_at);

CREATE TABLE IF NOT EXISTS scores (
    firm_id        INTEGER PRIMARY KEY REFERENCES firms(id) ON DELETE CASCADE,
    score          INTEGER NOT NULL,
    bucket         TEXT NOT NULL,
    components_json TEXT NOT NULL,
    computed_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_scores_score ON scores(score DESC);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    location      TEXT NOT NULL,
    radius_meters INTEGER NOT NULL,
    query         TEXT NOT NULL,
    firm_count    INTEGER NOT NULL DEFAULT 0,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS cached_responses (
    cache_key   TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    body        BLOB NOT NULL
);

INSERT INTO schema_version (version) VALUES (1);

-- Hermes phase 1 schema. See 2026-08-02-personal-feed-spec.md section 4.
-- Forward-only: never edit this file after it has run anywhere. Add 002_*.sql.

CREATE TABLE IF NOT EXISTS sources (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    name          TEXT NOT NULL,
    feed_url      TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    etag          TEXT,
    last_modified TEXT,
    content_hash  TEXT,
    error_count   INTEGER NOT NULL DEFAULT 0,
    disabled_until TEXT,
    next_poll_at  TEXT,
    last_polled_at TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    guid          TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title         TEXT NOT NULL,
    author        TEXT,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    raw_html      TEXT,
    text          TEXT,
    word_count    INTEGER,
    extracted_at  TEXT,
    summarized_at TEXT,
    scored_at     TEXT,
    last_error    TEXT,
    error_stage   TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_id, guid),
    UNIQUE(canonical_url)
);

CREATE INDEX IF NOT EXISTS idx_articles_feed
    ON articles(summarized_at, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_pending_extract
    ON articles(extracted_at) WHERE extracted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_articles_pending_summary
    ON articles(summarized_at) WHERE summarized_at IS NULL;

CREATE TABLE IF NOT EXISTS summaries (
    article_id     INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    headline       TEXT NOT NULL,
    bullets_json   TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    metadata       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    score           INTEGER NOT NULL,
    rationale       TEXT,
    rubric_version  TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    signals         TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_scores_article ON scores(article_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ratings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    value      INTEGER NOT NULL CHECK (value IN (-1, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('expand', 'click_through')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_versions (
    version     TEXT PRIMARY KEY,
    body        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('stated', 'distilled')),
    created_at  TEXT NOT NULL,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 003_saved.sql — user-pinned articles.
--
-- Migrations are forward-only and must never be edited after running
-- anywhere. This is the first ALTER TABLE ADD COLUMN in the project:
-- SQLite has no IF NOT EXISTS for it, so re-running raises. That is
-- intentional — it makes a regression in the runner's version-skip filter
-- fail loudly on the next restart instead of passing silently.
--
-- saved_at is NULL for unsaved articles, an ISO-8601 UTC timestamp
-- otherwise: one column answers both "is it saved" and "in what order".

ALTER TABLE articles ADD COLUMN saved_at TEXT;

CREATE INDEX IF NOT EXISTS idx_articles_saved
    ON articles(saved_at DESC) WHERE saved_at IS NOT NULL;

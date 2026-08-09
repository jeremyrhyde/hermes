-- 004_unusable.sql — articles whose text we do not actually have.
--
-- Migrations are forward-only and must never be edited after running
-- anywhere. Like 003 this uses ALTER TABLE ADD COLUMN, which SQLite offers
-- no IF NOT EXISTS for, so a regression in the runner's version-skip filter
-- fails loudly on the next restart instead of passing silently.
--
-- Do NOT set PRAGMA user_version here. The runner derives the version from
-- the NNN_ filename prefix and sets it itself; a pragma in the file would be
-- a second source of truth for the schema version.
--
-- unusable_at is NULL for every normal article. It is set when extraction
-- still yields too little text after a refetch — a paywall stub or an email
-- teaser — which means the article is never summarized and never scored.
-- The row and its text are retained so a future re-evaluation needs no
-- refetch, but nothing clears this column automatically: it is a stored
-- verdict, not a computed one.
--
-- unusable_reason distinguishes 'paywalled' from 'thin after refetch (N
-- words)'. The distinction is what tells you whether to change a source or
-- fix a bug, so it is worth a column rather than a log line.

ALTER TABLE articles ADD COLUMN unusable_at     TEXT;
ALTER TABLE articles ADD COLUMN unusable_reason TEXT;

-- Partial, so it indexes only the handful of unusable rows rather than the
-- whole table. Supports the per-source count on the health panel.
CREATE INDEX IF NOT EXISTS idx_articles_unusable
    ON articles(source_id) WHERE unusable_at IS NOT NULL;

-- 007_dismissed.sql — articles the reader swiped out of the feed.
--
-- Migrations are forward-only and must never be edited after running
-- anywhere. ALTER TABLE ADD COLUMN, like 003-005: SQLite offers no IF NOT
-- EXISTS for it, so a runner regression fails loudly instead of silently.
--
-- Do NOT set PRAGMA user_version here; the runner derives it from the NNN_
-- prefix.
--
-- A timestamp, not a flag or a DELETE: the article, its summary, score and
-- rating all stay (ratings train the profile; Saved keeps its saved articles),
-- and "undo" is clearing the column. The ranked feed and the plain feed skip
-- dismissed rows; the Saved view does not.

ALTER TABLE articles ADD COLUMN dismissed_at TEXT;

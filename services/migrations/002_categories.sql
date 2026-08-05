-- Adds article_categories for topic filtering. See 2026-08-04-feed-categories-spec.md.
-- Forward-only: never edit this file after it has run anywhere. Add 003_*.sql.

CREATE TABLE IF NOT EXISTS article_categories (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    category   TEXT NOT NULL,
    PRIMARY KEY (article_id, category)
);

CREATE INDEX IF NOT EXISTS idx_article_categories_category
    ON article_categories(category);

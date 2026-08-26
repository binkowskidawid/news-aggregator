-- A held-out reference set, kept in the same tables as the main one.
--
-- Six prompt versions have now been measured against the same 118 articles. Picking the
-- best of six on one set makes the winner's number optimistic by an unknown amount, and
-- the two versions that matter differ on `precision_span` by three findings — a gap this
-- set cannot resolve in either direction. The answer is material the tuning never saw.
--
-- `split` rather than a second pair of tables: every axis the evaluation varies is a
-- column here, and the scorer already separates configurations by `run_id`, so a run over
-- the holdout matches only holdout labels without any change to `metrics.py`. What does
-- need the column is the loader, which rebuilds a set by deleting it first, and
-- `run_eval.load_gold`, which would otherwise sweep both sets in one pass.

ALTER TABLE gold_articles
    ADD COLUMN IF NOT EXISTS split text NOT NULL DEFAULT 'main';

ALTER TABLE gold_articles
    DROP CONSTRAINT IF EXISTS gold_articles_split_valid;

ALTER TABLE gold_articles
    ADD CONSTRAINT gold_articles_split_valid CHECK (split IN ('main', 'holdout'));

COMMENT ON COLUMN gold_articles.split IS
    'main = the 118 articles every prompt version was tuned against. '
    'holdout = annotated later, from days the tuning never sampled, and scored separately.';

CREATE INDEX IF NOT EXISTS gold_articles_split_idx ON gold_articles (split);

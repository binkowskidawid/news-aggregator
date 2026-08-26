-- Two things the application layer needs before it can exist: one place that answers
-- "which analysis does the reader see", and a shipped default that collects nothing.

BEGIN;

-- `analyses` is append-only, so an article carries every analysis ever made of it —
-- production passes, evaluation sweeps, and now three prompt versions side by side. The
-- feed needs exactly one row per article and must not pick from the other configurations.
--
-- The WHERE clause is not a filter over the data, it is a restatement of what production
-- does: grammar_mode 'schema' and the lead shown whenever the article carries one, both
-- fixed in analyzer/__main__.py and settled by measurement. An evaluation row that used
-- the same configuration is the same analysis and is allowed to win on recency; a row
-- from the `title` arm of a sweep over an article that has a lead is not, and would
-- otherwise be picked purely for being newer.
--
-- Changing the production configuration means changing this view in the same commit.
CREATE OR REPLACE VIEW article_latest_analysis AS
SELECT DISTINCT ON (an.article_id)
    an.article_id,
    an.id AS analysis_id,
    an.prompt_version,
    an.model_name,
    an.input_variant,
    an.category,
    an.category_confidence,
    an.overall_assessment,
    an.consistency_error,
    an.quotes_total,
    an.quotes_rejected,
    an.latency_ms,
    an.created_at
FROM analyses an
JOIN articles ar ON ar.id = an.article_id
WHERE an.parse_error IS NULL
  AND an.grammar_mode = 'schema'
  AND an.input_variant = CASE WHEN ar.lead IS NOT NULL THEN 'title_lead' ELSE 'title' END
ORDER BY an.article_id, an.created_at DESC;

COMMENT ON VIEW article_latest_analysis IS
    'One row per article: the newest analysis produced by the production configuration. '
    'The reader-facing layer and the operator panel both read from here, so neither '
    'repeats the choice of which of an article''s analyses counts.';

-- Every source ships inactive.
--
-- Migration 002 seeded five portals active because they were the measured set and the
-- audit had confirmed their feeds. Released as open source the same seed becomes a
-- default that starts collecting from five named publishers the moment someone runs
-- `make migrate`, and the operator — not the author — carries the robots.txt, terms and
-- text-and-data-mining duties that attach to it. A default that is wrong for most
-- installations is not a convenience.
--
-- The rows stay: they are the addresses the audit verified and they document how a
-- source is configured. Turning one on is now a deliberate act:
--     make sources                       -- what is configured, and what is reserved
--     make source-enable NAME='Interia'
--
-- The column default goes with the rows. `active boolean NOT NULL DEFAULT true` from
-- migration 001 means every future insert arrives switched on — including the ones
-- src/evals/gold.py makes for portals it meets in a labelled set, which are placeholders
-- carrying no feed address at all. Updating the rows without the default would leave the
-- property depending on every caller remembering to pass the flag.
ALTER TABLE sources ALTER COLUMN active SET DEFAULT false;

UPDATE sources SET active = false;

-- Gazeta.pl's robots.txt opens with an explicit reservation under Article 4(3) of
-- Directive (EU) 2019/790, withholding consent for mining "dążącej do wygenerowania
-- informacji obejmujących w szczególności wzorce, tendencje i korelacje" — a description
-- of this product. The reservation is a property of the source, so it belongs in the row
-- rather than in a comment no query can read.
ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS tdm_reserved boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN sources.tdm_reserved IS
    'The publisher has machine-readably reserved text-and-data-mining rights under '
    'Article 4(3) of Directive (EU) 2019/790. Advisory: nothing enforces it, and whether '
    'it binds a given use is a question for the operator and their lawyer.';

UPDATE sources SET tdm_reserved = true WHERE name = 'Gazeta.pl';

COMMIT;

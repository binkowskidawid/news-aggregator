-- Prompt v1.2.0 asks the model to restate each quoted fragment without the evaluation it
-- carries. Both halves of that answer are stored: the paraphrase itself, and how much of
-- the original survived it.
--
-- The score is a column rather than a filter on purpose. Where to cut is a knob, and a
-- knob turned against a 118-article gold set fits the instrument instead of the problem —
-- the same reasoning that made grammar_mode a column. One run produces the distribution,
-- the threshold is argued from it afterwards.
--
-- Nullable, with no backfill: the 500-odd findings already stored came from v1.1.0, which
-- never asked the question. NULL is the honest record of "not asked", and inventing a
-- value for them would put a measurement in the database that nobody took.

ALTER TABLE findings ADD COLUMN IF NOT EXISTS neutral_alternative text;
ALTER TABLE findings ADD COLUMN IF NOT EXISTS neutral_similarity numeric(5, 2);

ALTER TABLE findings DROP CONSTRAINT IF EXISTS findings_neutral_similarity_range;
ALTER TABLE findings ADD CONSTRAINT findings_neutral_similarity_range
    CHECK (neutral_similarity IS NULL OR (neutral_similarity >= 0 AND neutral_similarity <= 100));

COMMENT ON COLUMN findings.neutral_alternative IS
    'Model''s restatement of the quote without its evaluative charge; NULL before prompt v1.2.0.';
COMMENT ON COLUMN findings.neutral_similarity IS
    'token_sort_ratio(quote, neutral_alternative) over lowercased text, 0-100. '
    'Exactly 100 means the model changed nothing, i.e. there was no evaluation to remove.';

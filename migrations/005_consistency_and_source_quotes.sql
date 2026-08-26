-- Two defects found by auditing the production corpus rather than the code, both in the
-- part of the record the interface renders directly.
--
-- 1. `findings.quote` held the string the model sent, not the span the outlet published.
--    Quote matching folds typography, so a model that tidies "zapomnieli" into
--    „zapomnieli” still matched — and the stored row then put punctuation the outlet never
--    printed inside quotation marks attributed to that outlet. Offsets were always right;
--    only the text beside them was wrong, which is why nothing caught it.
--
-- 2. The rule that `overall_assessment` follows from `findings` was enforced on the
--    annotator and on nobody else. A verdict of `neutral` sitting beside a confident
--    finding, or `heavily_loaded` beside none, passed validation as a correct analysis.
--    Recorded, never repaired: the contradiction is a fact about the model, and rewriting
--    the verdict to agree would delete the only evidence of it.

ALTER TABLE analyses ADD COLUMN IF NOT EXISTS consistency_error text;

COMMENT ON COLUMN analyses.consistency_error IS
    'Set when overall_assessment contradicts the findings stored beside it. '
    'Diagnostic, not a failure: the article stays analyzed and the row stays as returned.';

-- Backfill the quotes from the offsets. Safe because the offsets are what was verified:
-- of 1281 non-fuzzy findings, 1275 already re-sliced to exactly the stored string, and the
-- six that did not differ only in quotation marks. Fuzzy matches are rewritten too — there
-- the source slice is the whole point, and `fuzzy_matched` still marks how it was found.
UPDATE findings f
SET quote = substring(
        CASE f.field WHEN 'title' THEN a.title ELSE a.lead END
        FROM f.quote_start + 1 FOR f.quote_end - f.quote_start)
FROM analyses an, articles a
WHERE an.id = f.analysis_id
  AND a.id = an.article_id
  AND substring(CASE f.field WHEN 'title' THEN a.title ELSE a.lead END
                FROM f.quote_start + 1 FOR f.quote_end - f.quote_start) IS DISTINCT FROM f.quote;

-- Backfill the consistency verdict for everything already stored, so the report counts the
-- contradictions this rule was written for instead of only those arriving after it. Pure
-- function of rows that are already here; the wording matches domain.consistency_error.
WITH confident AS (
    SELECT an.id,
           an.overall_assessment,
           count(*) FILTER (WHERE f.confidence >= 0.7) AS confident_findings
    FROM analyses an
    LEFT JOIN findings f ON f.analysis_id = an.id
    WHERE an.parse_error IS NULL AND an.overall_assessment IS NOT NULL
    GROUP BY an.id, an.overall_assessment
)
UPDATE analyses an
SET consistency_error = CASE
        WHEN c.overall_assessment = 'neutral'
            THEN 'neutral verdict beside ' || c.confident_findings
                 || ' finding(s) at confidence >= 0.7'
        ELSE c.overall_assessment || ' verdict with no finding at confidence >= 0.7'
    END
FROM confident c
WHERE an.id = c.id
  AND ((c.overall_assessment = 'neutral' AND c.confident_findings > 0)
       OR (c.overall_assessment <> 'neutral' AND c.confident_findings = 0));

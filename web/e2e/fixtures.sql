-- Corpus for the browser tests.
--
-- Locally these run against the real database, which is the point: the reader's path is
-- worth checking on the material it will actually meet. CI has no corpus, so it gets this —
-- three articles chosen for the shapes the renderer has to survive rather than for realism.
--
--   1. overlapping spans in a headline, which exist 68 times in the working corpus and
--      would otherwise nest <mark> elements and lose text
--   2. a finding in the lead, so the numbering has to continue across two fields
--   3. an article with nothing reported, which the page must present as an absence of a
--      finding rather than as a clean bill of health
--
-- Diacritics before every span are deliberate: an offset counted in bytes rather than
-- characters lands elsewhere, and that is the failure a reader could never see.

BEGIN;

INSERT INTO sources (name, base_url, rss_url, strategy, active)
VALUES ('Fixture', 'https://fixture.test', 'https://fixture.test/rss', 'rss', false)
ON CONFLICT (name) DO NOTHING;

WITH src AS (SELECT id FROM sources WHERE name = 'Fixture'),
inserted AS (
    INSERT INTO articles (source_id, url, url_hash, title, lead, fetch_level, content_hash,
                          status, category, published_at)
    SELECT src.id, v.url, md5(v.url), v.title, v.lead, 1, md5(v.title), 'analyzed',
           v.category, now() - v.age
    FROM src, (VALUES
        ('https://fixture.test/1',
         'Wstrząsająca relacja świadka',
         'Zażądał wyjaśnień od ministra',
         'polityka', interval '1 hour'),
        ('https://fixture.test/2',
         'Rada podjęła uchwałę o budżecie',
         NULL,
         'biznes', interval '2 hours')
    ) AS v(url, title, lead, category, age)
    RETURNING id, url, lead
),
analysed AS (
    INSERT INTO analyses (article_id, run_id, provider, model_name, prompt_version,
                          input_variant, grammar_mode, category, category_confidence,
                          overall_assessment)
    SELECT inserted.id, gen_random_uuid(), 'ollama', 'gemma4:latest', 'v1.1.3',
           CASE WHEN inserted.lead IS NOT NULL THEN 'title_lead' ELSE 'title' END,
           'schema',
           CASE WHEN inserted.url = 'https://fixture.test/1' THEN 'polityka' ELSE 'biznes' END,
           0.90,
           CASE WHEN inserted.url = 'https://fixture.test/1' THEN 'mildly_loaded' ELSE 'neutral' END
    FROM inserted
    RETURNING id, article_id
)
INSERT INTO findings (analysis_id, type, quote, field, quote_start, quote_end, explanation,
                      confidence, neutral_alternative)
SELECT analysed.id, v.type, v.quote, v.field, v.start, v.stop, v.explanation, 0.90, v.neutral
FROM analysed
JOIN inserted ON inserted.id = analysed.article_id AND inserted.url = 'https://fixture.test/1'
CROSS JOIN (VALUES
    ('emotional_load', 'Wstrząsająca', 'title', 0, 12,
     'Wartościujący przymiotnik zamiast neutralnego opisu.', 'Relacja świadka'),
    ('clickbait_hook', 'sająca relacja', 'title', 6, 20,
     'Fragment nakładający się na poprzedni, po to tu jest.', NULL),
    ('loaded_question', 'Zażądał', 'lead', 0, 7,
     'Czasownik nadający wypowiedzi ton konfrontacyjny.', 'Poprosił')
) AS v(type, quote, field, start, stop, explanation, neutral);

COMMIT;

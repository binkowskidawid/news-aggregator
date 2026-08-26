-- Initial schema: sources, articles, LLM analyses, and the hand-labelled gold set.
--
-- Requires PostgreSQL 18 for uuidv7(): time-ordered UUIDs index far better than uuidv4
-- because inserts land at the end of the B-tree instead of scattering across it.
--
-- Closed vocabularies are enforced with named CHECK constraints rather than native enum
-- types. The manipulation taxonomy is expected to grow (clickbait_hook was added after
-- the first review of the taxonomy), and relaxing a CHECK in a later migration is a
-- one-liner.

BEGIN;

-- ---------------------------------------------------------------------------
-- sources
-- ---------------------------------------------------------------------------

CREATE TABLE sources (
    id            uuid        PRIMARY KEY DEFAULT uuidv7(),
    name          text        NOT NULL UNIQUE,
    base_url      text        NOT NULL,
    rss_url       text,
    strategy      text        NOT NULL,
    selectors     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    active        boolean     NOT NULL DEFAULT true,
    last_fetch_at timestamptz,
    last_etag     text,
    created_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT sources_strategy_valid
        CHECK (strategy IN ('rss', 'static', 'browser', 'hybrid')),
    -- An RSS-first source without a feed URL is a configuration error, not a runtime one.
    CONSTRAINT sources_rss_needs_url
        CHECK (strategy <> 'rss' OR rss_url IS NOT NULL)
);

COMMENT ON TABLE sources IS 'One row per news portal feed or listing we fetch from.';
COMMENT ON COLUMN sources.strategy IS
    'Cheapest viable fetch tier: rss < static HTML < headless browser.';
COMMENT ON COLUMN sources.last_etag IS
    'Sent back as If-None-Match so unchanged feeds cost one 304 instead of a full body.';

-- ---------------------------------------------------------------------------
-- articles
-- ---------------------------------------------------------------------------

CREATE TABLE articles (
    id           uuid        PRIMARY KEY DEFAULT uuidv7(),
    source_id    uuid        NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    url          text        NOT NULL,
    url_hash     text        NOT NULL UNIQUE,
    title        text        NOT NULL,
    lead         text,
    published_at timestamptz,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    fetch_level  smallint    NOT NULL,
    category     text,
    status       text        NOT NULL DEFAULT 'pending',
    content_hash text        NOT NULL,

    CONSTRAINT articles_fetch_level_valid CHECK (fetch_level BETWEEN 1 AND 3),
    CONSTRAINT articles_status_valid
        CHECK (status IN ('pending', 'analyzed', 'failed', 'failed_permanent')),
    CONSTRAINT articles_category_valid CHECK (category IS NULL OR category IN (
        'polityka', 'kultura', 'technologia', 'sport',
        'biznes', 'geopolityka', 'zdrowie', 'inne'
    )),
    CONSTRAINT articles_title_not_blank CHECK (btrim(title) <> '')
);

COMMENT ON COLUMN articles.url_hash IS
    'Hash of the canonical URL with tracking parameters (utm_*, srcc, s) stripped. The '
    'same article reachable from a feed and a homepage must collapse to one row.';
COMMENT ON COLUMN articles.lead IS
    'NULL where the portal exposes headlines only. Nullability is load-bearing: it is '
    'exactly the asymmetry that makes per-portal indicator counts incomparable.';
COMMENT ON COLUMN articles.fetch_level IS
    'Which tier actually produced this row (1=rss, 2=static, 3=browser). Cost metric.';

CREATE INDEX articles_feed_idx ON articles (category, published_at DESC);
CREATE INDEX articles_pending_idx ON articles (id) WHERE status = 'pending';
CREATE INDEX articles_source_idx ON articles (source_id);

-- ---------------------------------------------------------------------------
-- analyses
-- ---------------------------------------------------------------------------

CREATE TABLE analyses (
    id                  uuid        PRIMARY KEY DEFAULT uuidv7(),
    article_id          uuid        NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    run_id              uuid        NOT NULL,
    provider            text        NOT NULL,
    model_name          text        NOT NULL,
    prompt_version      text        NOT NULL,
    input_variant       text        NOT NULL,
    grammar_mode        text        NOT NULL,
    source_label        text,
    category            text,
    category_confidence numeric(3, 2),
    overall_assessment  text,
    raw_response        jsonb,
    parse_error         text,
    latency_ms          integer,
    tokens_in           integer,
    tokens_out          integer,
    quotes_total        integer     NOT NULL DEFAULT 0,
    quotes_rejected     integer     NOT NULL DEFAULT 0,
    quotes_fuzzy        integer     NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT analyses_provider_valid CHECK (provider IN ('ollama', 'openrouter')),
    CONSTRAINT analyses_input_variant_valid
        CHECK (input_variant IN ('title', 'title_lead')),
    CONSTRAINT analyses_grammar_mode_valid CHECK (grammar_mode IN ('schema', 'json')),
    CONSTRAINT analyses_assessment_valid CHECK (overall_assessment IS NULL
        OR overall_assessment IN ('neutral', 'mildly_loaded', 'heavily_loaded')),
    CONSTRAINT analyses_category_valid CHECK (category IS NULL OR category IN (
        'polityka', 'kultura', 'technologia', 'sport',
        'biznes', 'geopolityka', 'zdrowie', 'inne'
    )),
    CONSTRAINT analyses_confidence_range
        CHECK (category_confidence IS NULL
               OR category_confidence BETWEEN 0 AND 1),
    CONSTRAINT analyses_quote_counts_consistent
        CHECK (quotes_rejected + quotes_fuzzy <= quotes_total),
    -- Either the response parsed or it did not. Both at once means a bug in the writer.
    CONSTRAINT analyses_outcome_exclusive
        CHECK ((parse_error IS NULL) <> (overall_assessment IS NULL))
);

COMMENT ON TABLE analyses IS
    'One LLM call. Every axis the evaluation varies is a column, so a comparison is a '
    'GROUP BY rather than a directory of result files.';
COMMENT ON COLUMN analyses.run_id IS
    'Groups one sweep. Repeating an article within a run is how stability is measured.';
COMMENT ON COLUMN analyses.input_variant IS
    'Whether the model saw the headline alone or headline plus lead. Persuasive technique '
    'concentrates in headlines, so this confounds any cross-portal comparison.';
COMMENT ON COLUMN analyses.grammar_mode IS
    'schema = full JSON Schema compiled to a sampling grammar; json = syntax only. The '
    'ablation exists because constrained sampling can push a weak model into shallower '
    'analysis while keeping the output structurally perfect.';
COMMENT ON COLUMN analyses.source_label IS
    'Portal name injected into the prompt. NULL in normal operation — the model is not '
    'told who published the text. Non-NULL only in the brand-bias probe.';
COMMENT ON COLUMN analyses.raw_response IS
    'Verbatim model output, including quotes later rejected as unverifiable.';

CREATE INDEX analyses_article_idx ON analyses (article_id);
CREATE INDEX analyses_run_idx ON analyses (run_id);
CREATE INDEX analyses_comparison_idx
    ON analyses (model_name, prompt_version, input_variant, grammar_mode);

-- ---------------------------------------------------------------------------
-- findings
-- ---------------------------------------------------------------------------

CREATE TABLE findings (
    id            uuid          PRIMARY KEY DEFAULT uuidv7(),
    analysis_id   uuid          NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    type          text          NOT NULL,
    quote         text          NOT NULL,
    field         text          NOT NULL,
    quote_start   integer       NOT NULL,
    quote_end     integer       NOT NULL,
    fuzzy_matched boolean       NOT NULL DEFAULT false,
    explanation   text,
    confidence    numeric(3, 2),

    CONSTRAINT findings_type_valid CHECK (type IN (
        'emotional_load', 'fear_appeal', 'overgeneralization',
        'loaded_question', 'unsourced_figure', 'clickbait_hook'
    )),
    CONSTRAINT findings_field_valid CHECK (field IN ('title', 'lead')),
    CONSTRAINT findings_confidence_range
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CONSTRAINT findings_span_valid CHECK (quote_start >= 0 AND quote_end > quote_start),
    CONSTRAINT findings_quote_not_blank CHECK (btrim(quote) <> '')
);

COMMENT ON TABLE findings IS
    'Only findings whose quote was verified against the source text. A quote the model '
    'invented never reaches this table — a product accusing named media of manipulation '
    'cannot cite sentences those media did not write.';
COMMENT ON COLUMN findings.field IS
    'Which part of the article the quote came from. Persuasive technique concentrates in '
    'headlines, and only some portals expose a lead, so a per-outlet comparison that does '
    'not separate the two measures our fetching strategy rather than their writing.';
COMMENT ON COLUMN findings.quote_start IS
    'Character offset into the field named by `field`, for highlighting in the UI.';
COMMENT ON COLUMN findings.fuzzy_matched IS
    'The quote matched only after fuzzy comparison. A rising share warns of drift before '
    'verbatim fidelity actually breaks.';

CREATE INDEX findings_analysis_idx ON findings (analysis_id);
CREATE INDEX findings_type_idx ON findings (type);

-- ---------------------------------------------------------------------------
-- fetch_errors
-- ---------------------------------------------------------------------------

CREATE TABLE fetch_errors (
    id            uuid        PRIMARY KEY DEFAULT uuidv7(),
    source_id     uuid        REFERENCES sources(id) ON DELETE SET NULL,
    url           text        NOT NULL,
    error_type    text        NOT NULL,
    error_message text,
    raw_response  text,
    occurred_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE fetch_errors IS
    'Portals change layout and break parsers. The raw response is kept because a report '
    'that a parser broke is not the same thing as the evidence needed to fix it.';

CREATE INDEX fetch_errors_recent_idx ON fetch_errors (occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Gold set
-- ---------------------------------------------------------------------------

CREATE TABLE gold_articles (
    article_id          uuid        PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    expected_category   text        NOT NULL,
    expected_assessment text        NOT NULL,
    kind                text        NOT NULL,
    note                text,
    labeled_by          text        NOT NULL,
    labeled_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT gold_articles_category_valid CHECK (expected_category IN (
        'polityka', 'kultura', 'technologia', 'sport',
        'biznes', 'geopolityka', 'zdrowie', 'inne'
    )),
    CONSTRAINT gold_articles_assessment_valid CHECK (expected_assessment IN (
        'neutral', 'mildly_loaded', 'heavily_loaded'
    )),
    CONSTRAINT gold_articles_kind_valid CHECK (kind IN (
        'neutral', 'loaded', 'borderline', 'quoted_speech'
    ))
);

COMMENT ON TABLE gold_articles IS
    'Articles in the evaluation set, with the expected classification. `kind` records why '
    'each was chosen so a model can be scored on the cases it is expected to fail.';
COMMENT ON COLUMN gold_articles.kind IS
    'borderline = superficially looks like a technique but is not (a sourced statistic, '
    'an open question); quoted_speech = loaded words belong to a quoted person, not the '
    'newsroom. Both separate genuine analysis from keyword matching.';

CREATE TABLE gold_labels (
    id          uuid        PRIMARY KEY DEFAULT uuidv7(),
    article_id  uuid        NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    type        text        NOT NULL,
    quote       text        NOT NULL,
    field       text        NOT NULL,
    quote_start integer     NOT NULL,
    quote_end   integer     NOT NULL,
    note        text,
    labeled_by  text        NOT NULL,
    labeled_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT gold_labels_type_valid CHECK (type IN (
        'emotional_load', 'fear_appeal', 'overgeneralization',
        'loaded_question', 'unsourced_figure', 'clickbait_hook'
    )),
    CONSTRAINT gold_labels_field_valid CHECK (field IN ('title', 'lead')),
    CONSTRAINT gold_labels_span_valid
        CHECK (quote_start >= 0 AND quote_end > quote_start),
    -- The same span must not carry two labels; the prompt forbids it of the model too.
    CONSTRAINT gold_labels_span_unique UNIQUE (article_id, field, quote_start, quote_end)
);

COMMENT ON TABLE gold_labels IS
    'Reference annotations. Scores computed against them measure agreement with the '
    'annotator, not ground truth — the distinction belongs in every report that uses them.';
COMMENT ON COLUMN gold_labels.note IS
    'Why this span was labelled. Makes a disputed label reviewable instead of arguable.';

CREATE INDEX gold_labels_article_idx ON gold_labels (article_id);

COMMIT;

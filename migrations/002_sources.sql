-- The six portals this project was measured against, with the address the audit confirmed
-- works. They are a starting point, not a recommendation: an operator picks their own.
--
-- This is configuration, not schema, and it lives here because `sources` was designed to
-- hold it: rss_url, strategy, selectors, active, and the two conditional-GET columns are
-- all per-portal settings. Keeping the list in the database means the ingest pass knows
-- no portal by name — it reads rows and follows strategy.
--
-- Feed addresses were chosen by `make audit-sources`, which re-runs the probe and reports
-- what each candidate returns.
--
-- DO UPDATE rather than DO NOTHING: the gold loader (src/evals/gold.py) inserts the same
-- portal names with a placeholder base_url and strategy 'static', so with DO NOTHING the
-- contents of this table would depend on whether `make gold-load` or `make migrate` ran
-- first. last_etag and last_fetch_at are deliberately absent from the SET list, so
-- re-running migrations does not discard the conditional-GET state.

BEGIN;

INSERT INTO sources (name, base_url, rss_url, strategy, selectors, active) VALUES
    ('Interia',
     'https://wydarzenia.interia.pl/',
     'https://wydarzenia.interia.pl/feed',
     'rss', '{}'::jsonb, true),
    ('Onet',
     'https://wiadomosci.onet.pl/',
     'https://wiadomosci.onet.pl/.feed',
     'rss', '{}'::jsonb, true),
    -- INACTIVE, and this is not a technical limit: the feed parses and robots.txt
    -- permits the crawl. Gazeta.pl's robots.txt opens with an
    -- explicit text-and-data-mining reservation under Article 4(3) of Directive (EU)
    -- 2019/790, in Polish and English, withholding consent for automated analysis
    -- "dążącej do wygenerowania informacji obejmujących w szczególności wzorce,
    -- tendencje i korelacje" — a description of this product. None of the other five
    -- portals carries one.
    --
    -- A comment is not a directive, so no parser enforces it; the Directive gives a
    -- machine-readable reservation effect against commercial mining regardless. Whether
    -- it binds a given deployment is a question for that operator and their lawyer, not
    -- for the crawler, so the default is not to collect. To reverse:
    --     UPDATE sources SET active = true WHERE name = 'Gazeta.pl';
    ('Gazeta.pl',
     'https://wiadomosci.gazeta.pl/',
     'https://wiadomosci.gazeta.pl/pub/rss/wiadomosci.htm',
     'rss', '{}'::jsonb, false),
    ('WP',
     'https://wiadomosci.wp.pl/',
     'https://wiadomosci.wp.pl/rss.xml',
     'rss', '{}'::jsonb, true),
    ('TVN24',
     'https://tvn24.pl/',
     'https://tvn24.pl/najnowsze.xml',
     'rss', '{}'::jsonb, true),
    -- No feed exists: three candidate addresses returned 404 and the homepage declares
    -- none. The listing is server-rendered Drupal, so this is tier 2 (static HTML), not
    -- the tier 3 the audit table assumed before the page was actually read.
    --
    -- The selector matches the card headline. It depends on the Drupal theme's class
    -- names and will break when the theme changes; that is why a source yielding zero
    -- articles writes a fetch_errors row with the response body attached.
    ('TV Republika',
     'https://tvrepublika.pl/',
     NULL,
     'static', '{"card": "h2[class*=\"__text\"]"}'::jsonb, true)
ON CONFLICT (name) DO UPDATE SET
    base_url  = EXCLUDED.base_url,
    rss_url   = EXCLUDED.rss_url,
    strategy  = EXCLUDED.strategy,
    selectors = EXCLUDED.selectors,
    active    = EXCLUDED.active;

COMMIT;

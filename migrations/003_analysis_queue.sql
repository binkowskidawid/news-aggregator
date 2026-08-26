-- Retry bookkeeping for the analysis pass.
--
-- `articles.status` was already the queue and `articles_status_valid` already admitted
-- 'failed_permanent'; what was missing is the counter that decides when an article gets
-- there. It matters now and not before because the analyzer runs unattended under
-- `restart: unless-stopped`: an article whose request always fails would otherwise be
-- retried every fifteen minutes for as long as the container lives.

BEGIN;

ALTER TABLE articles
    ADD COLUMN attempts        smallint    NOT NULL DEFAULT 0,
    ADD COLUMN last_attempt_at timestamptz;

COMMENT ON COLUMN articles.attempts IS
    'Failed analysis attempts caused by the provider (network, timeout, rejected '
    'request). A response that arrived but did not parse is NOT counted: under a grammar '
    'that is a configuration fault, so it goes straight to ''failed'' and is never '
    'retried automatically. Reaching the ceiling moves the row to ''failed_permanent''.';
COMMENT ON COLUMN articles.last_attempt_at IS
    'Backs off retries without any scheduling code: the pass selects rows whose last '
    'attempt is older than one hour times the number of attempts already made.';

COMMIT;

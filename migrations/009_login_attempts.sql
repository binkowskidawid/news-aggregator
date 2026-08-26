-- Failed sign-in attempts, so that a password can be slow to guess and also limited in how
-- often it may be guessed.
--
-- Argon2id makes one attempt expensive; it does not make a million attempts impossible. The
-- counter is kept in Postgres rather than in process memory because the application is meant
-- to run behind more than one worker, and a limit that resets per process is not a limit.
--
-- Rows are written only on failure and removed once outside the window, so this table stays
-- small and holds no successful-login history. It records an identifier and a timestamp:
-- deliberately not a user id, because the attempts worth counting include those against
-- addresses that have no account.

BEGIN;

CREATE TABLE login_attempts (
    id           uuid        PRIMARY KEY DEFAULT uuidv7(),
    identifier   text        NOT NULL,
    attempted_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX login_attempts_recent_idx ON login_attempts (identifier, attempted_at DESC);

COMMENT ON COLUMN login_attempts.identifier IS
    'Lowercased address, or the client address for attempts spread across many addresses. '
    'Personal data with a short retention: rows outside the window are deleted on the next '
    'attempt, so the table is not a log of who tried to sign in and when.';

COMMIT;

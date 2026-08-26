-- Accounts: who is reading, what they kept, what they follow.
--
-- Everything the corpus tables hold is public text someone published. These four tables are
-- the first personal data in this database, and whoever runs this installation — not
-- whoever wrote it — is the controller of it. That is why deletion is a real DELETE with
-- ON DELETE CASCADE rather than a `deleted_at` flag: a right-to-erasure request has to
-- leave nothing behind, and a soft delete leaves everything.

BEGIN;

CREATE TABLE users (
    id            uuid        PRIMARY KEY DEFAULT uuidv7(),
    email         text        NOT NULL,
    password_hash text        NOT NULL,
    role          text        NOT NULL DEFAULT 'reader',
    created_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT users_role_valid CHECK (role IN ('reader', 'admin')),
    CONSTRAINT users_email_shaped CHECK (position('@' IN email) > 1)
);

-- Case-insensitive uniqueness without the citext extension: an address differing only in
-- case is the same mailbox, so registering `Jan@example.pl` over `jan@example.pl` must
-- collide. The index doubles as the lookup login uses.
CREATE UNIQUE INDEX users_email_key ON users (lower(email));

COMMENT ON COLUMN users.password_hash IS
    'Argon2id via argon2-cffi. The parameters live with the hash, so raising the cost '
    'later re-hashes on next login rather than invalidating everyone.';

-- Server-side sessions rather than a JWT. The difference that matters is revocation: a
-- signed token stays valid until it expires no matter what happens to the account, and an
-- installation holding personal data needs "log this person out everywhere, now" to be one
-- DELETE.
--
-- The cookie carries a random token; this table stores only its SHA-256. A row here is
-- therefore not a credential, so a leaked backup or an over-broad SELECT hands out no
-- sessions. The primary key stays a uuid so nothing else has to key on a secret.
CREATE TABLE sessions (
    id           uuid        PRIMARY KEY DEFAULT uuidv7(),
    user_id      uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash   text        NOT NULL UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);

CREATE INDEX sessions_user_idx ON sessions (user_id);
CREATE INDEX sessions_expiry_idx ON sessions (expires_at);

CREATE TABLE saved_articles (
    user_id    uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    article_id uuid        NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
    saved_at   timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (user_id, article_id)
);

-- Ordering for "my saved articles", which is the only way this table is ever read.
CREATE INDEX saved_articles_recent_idx ON saved_articles (user_id, saved_at DESC);

-- One row per category followed. The category list is the same CHECK the articles table
-- carries; keeping it as a constraint rather than a lookup table means an unknown category
-- cannot be subscribed to, which is the failure mode a free-text column would allow.
CREATE TABLE subscriptions (
    user_id    uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    category   text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (user_id, category),
    CONSTRAINT subscriptions_category_valid CHECK (category IN (
        'polityka', 'kultura', 'technologia', 'sport', 'biznes', 'geopolityka',
        'zdrowie', 'inne'
    ))
);

COMMIT;

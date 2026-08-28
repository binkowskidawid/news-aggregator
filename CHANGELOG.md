# Changelog

Notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A change to `PROMPT_VERSION` is a change to what the software claims, so it belongs here even
when no code moved.

## [Unreleased]

### Fixed

- **The sign-in rate limit no longer locks out every account at once.** The second budget was
  counted against `request.client.host`, which behind the front end's `/api/*` rewrite is the
  `web` container for every reader alike — so ten failed attempts from anybody answered 429
  to everybody for fifteen minutes. The per-address budget is unchanged and always applies;
  the per-client one is now off unless `TRUST_PROXY_IP=1` states that a reverse proxy
  establishes the address, and then reads the rightmost `X-Forwarded-For` entry. See
  [OPERATOR.md](OPERATOR.md) § Serving it.
- **Password hashing no longer blocks the event loop.** Argon2id was called directly from
  async handlers, so every sign-in stalled the whole process for the duration of a hash —
  measured at 25 ms and 64 MiB per call. It now runs in a thread under a concurrency limit
  of four, which also caps what an unauthenticated `/auth/register` can reach.
- **A password hashed under older Argon2 parameters is now re-hashed on sign-in.**
  `needs_rehash` existed, was documented as the reason raising the cost does not lock anyone
  out, and was never called.
- One session refresh at most every five minutes, rather than an `UPDATE` on `sessions` for
  every authenticated request.
- Corrected the precision figure in the feed router's own docstring: 42%, as everywhere else,
  not 39%.
- `make dev` no longer prints a `/docs` address that answers 404 unless `API_DOCS=1`.
- `make source-enable`, `source-disable` and `admin` pass names and addresses as psql
  variables instead of interpolating them into the statement text.

### Changed

- The `full` compose profile now waits for the API's health check before starting the front
  end, and `CLAUDE.md` points at `AGENTS.md` instead of duplicating it.

## [0.1.0] — 2026-08-27

First public release. Prompt `v1.1.3`, measured on 64 held-out articles: underline correct
47%, underline and technique correct 42%, quotes verbatim 100%, topic category 86%. Full
conditions in [MODEL_CARD.md](MODEL_CARD.md).

### Added

- **Ingest** — tiered fetching (RSS, then static HTML), robots.txt re-checked on every pass,
  per-host throttling, conditional GET. Collects a URL, a headline and a lead; never an
  article body.
- **Analysis** — one schema-constrained call per article, every quote verified against the
  source text, `articles.status` as the work queue with retry back-off.
- **Evaluation harness** — labelled sets with a held-out split, Wilson intervals, thresholds
  anchored to published results for Polish rather than to invented levels. The sets under
  `eval/` carry headlines and leads from named publishers so the figures can be reproduced;
  publishers who reserve text and data mining rights are excluded from them, as they are from
  collection. See [NOTICE](NOTICE).
- **API** (FastAPI) — feed with category and findings filters, article detail with offsets
  into the original strings, accounts, saved articles, category subscriptions, `/me/export`
  for Articles 15 and 20 GDPR, and an operator panel behind an admin role.
- **Front end** (Next.js, Polish and English) — feed, article detail with the reported
  fragments marked in place, accounts, and the operator panel.
- **Documentation** — `MODEL_CARD.md`, `COMPLIANCE.md`, `OPERATOR.md`, `SECURITY.md`.
- `make admin EMAIL='...'` grants the operator role. Nothing in the running service can grant
  it: a service able to promote its own accounts is one bug away from promoting someone
  else's.

### Security

- Argon2id password hashing; session tokens stored only as SHA-256 digests.
- Sign-in rate limited by email address, on a constant-work path that hashes even when no
  account exists. A second budget per client address ships off — see Unreleased above and
  [SECURITY.md](SECURITY.md).
- The operator panel answers 404 rather than 403, and the API's own documentation routes
  (`/docs`, `/redoc`, `/openapi.json`) are unmounted unless `API_DOCS=1` — the front end
  proxies `/api/*` verbatim and the schema lists the operator paths.
- Security headers set in application middleware rather than assumed from a reverse proxy —
  the API's own `default-src 'none'`, and a looser Content-Security-Policy on the pages the
  front end serves.
- Request bodies capped at 64 KB, enforced against the declared `Content-Length` and by
  counting bytes as they arrive, so omitting the header under chunked encoding does not get
  past it.
- Both containers run as an unprivileged user.

### Known limitations

- More than half of what the system underlines is wrong. This is measured, published, and
  stated in the interface beside every analysis.
- Quality is measured for Polish only. Another language needs its own prompt file and its own
  annotated set.
- No password reset — there is no mail path in this project at all — and no SBOM, though
  dependencies are scanned on every run. The front end's policy keeps `'unsafe-inline'` in
  `script-src` and `style-src`, which Next and Tailwind both require. See
  [SECURITY.md](SECURITY.md).

[Unreleased]: https://github.com/binkowskidawid/news-aggregator/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/binkowskidawid/news-aggregator/releases/tag/v0.1.0

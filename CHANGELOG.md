# Changelog

Notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A change to `PROMPT_VERSION` is a change to what the software claims, so it belongs here even
when no code moved.

## [Unreleased]

## [0.1.0] — 2026-08-26

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
  anchored to published results for Polish rather than to invented levels.
- **API** (FastAPI) — feed with category and findings filters, article detail with offsets
  into the original strings, accounts, saved articles, category subscriptions, `/me/export`
  for Articles 15 and 20 GDPR, and an operator panel behind an admin role.
- **Front end** (Next.js, Polish and English) — feed, article detail with the reported
  fragments marked in place, accounts, and the operator panel.
- **Documentation** — `MODEL_CARD.md`, `COMPLIANCE.md`, `OPERATOR.md`, `SECURITY.md`.

### Security

- Argon2id password hashing; session tokens stored only as SHA-256 digests.
- Sign-in rate limited against two budgets, by address and by client address, on a
  constant-work path that hashes even when no account exists.
- The operator panel answers 404 rather than 403, and the API's own documentation routes
  (`/docs`, `/redoc`, `/openapi.json`) are unmounted unless `API_DOCS=1` — the front end
  proxies `/api/*` verbatim and the schema lists the operator paths.
- Security headers set in application middleware rather than assumed from a reverse proxy.
- Both containers run as an unprivileged user.

### Known limitations

- More than half of what the system underlines is wrong. This is measured, published, and
  stated in the interface beside every analysis.
- Quality is measured for Polish only. Another language needs its own prompt file and its own
  annotated set.
- No password reset, no request size limit, and no Content-Security-Policy on the front end.
  See [SECURITY.md](SECURITY.md).

[Unreleased]: https://github.com/binkowskidawid/news-aggregator/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/binkowskidawid/news-aggregator/releases/tag/v0.1.0

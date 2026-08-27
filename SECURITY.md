# Security policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
**[Security → Report a vulnerability](https://github.com/binkowskidawid/news-aggregator/security/advisories/new)**.

Please do not open a public issue for anything exploitable.

This project has **one maintainer and no service-level agreement**. Reports are handled on a
best-effort basis; expect acknowledgement in days rather than hours, and no bounty. If a
report is valid and you would like credit in the advisory, say so.

## Supported versions

The `main` branch only. There are no release branches and no backports.

## Scope

In scope — the code in this repository:

- authentication, sessions, and the account endpoints (`src/api/`)
- SQL construction and anything reachable through the feed or article endpoints
- the ingest layer's handling of remote content
- prompt injection that changes what the system stores or shows, rather than what one
  response says
- the front end (`web/`), including anything that renders model output or article text

Out of scope:

- **The model being wrong.** Fewer than half the findings hold up; this is measured,
  documented in [MODEL_CARD.md](MODEL_CARD.md), and stated in the interface. A wrong finding
  is not a vulnerability.
- **Prompt injection that only affects one response.** Article text is untrusted input by
  design. An injected instruction that makes the model produce a nonsensical analysis for its
  own article is expected; one that escapes into stored data, another article, or an
  operator-facing surface is not.
- Anything about a deployment you configured: your reverse proxy, your TLS, your database
  exposure, your choice of sources.
- Findings that require an operator to have already disabled a documented safeguard.

## Known gaps

Stated rather than hidden, and not to be reported as new:

- **No password reset.** There is no mail path in this project at all.
- **The front end's CSP allows inline scripts and styles.** Next inlines its own bootstrap
  into the document and Tailwind emits inline style attributes, so `'unsafe-inline'` stays in
  `script-src` and `style-src`. Removing the first means a per-request nonce threaded through
  next-intl's middleware; there is no nonce path for the second at all. Everything else in the
  policy is closed, and the API's is `default-src 'none'`.
- **No SBOM.** Dependencies are scanned on every run — `uv audit` against `uv.lock`,
  `pnpm audit` against the front end's, and CodeQL weekly — but nothing publishes a bill of
  materials.
- **CSRF rests on `SameSite=Lax` and the absence of CORS**, both deliberate: the front end and
  the API are served from one origin through a rewrite, so no cross-origin request is
  expected. There is no CSRF token. If you deploy the two on separate origins, this
  assumption stops holding and you need one.

## What the code does defend

- Argon2id password hashing; session tokens stored only as SHA-256 digests
- `httpOnly` + `SameSite=Lax` + `Secure` session cookies, `Secure` on unless explicitly
  disabled for local HTTP
- Rate limiting on sign-in against two budgets, by address and by IP
- Constant-work sign-in path — a missing account still hashes, so the response time does not
  disclose whether an address is registered
- The operator panel behind an admin role, answering 404 rather than 403 — and the API's
  own documentation routes (`/docs`, `/redoc`, `/openapi.json`) unmounted unless `API_DOCS=1`,
  because the front end proxies `/api/*` verbatim and the schema lists those same paths
- Security headers set in application middleware rather than assumed from a reverse proxy —
  the API's own, and a looser Content-Security-Policy on the front end's pages
- A 64 KB ceiling on request bodies, enforced both against the declared `Content-Length` and
  by counting bytes as they arrive, so omitting the header under chunked encoding does not
  get past it
- The API container runs as an unprivileged user (`uid 10001`), because the ingest process
  parses HTML from untrusted sources
- Quote verification against the source text, which is a correctness safeguard and an
  injection safeguard at the same time

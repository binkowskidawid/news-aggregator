# Operator guide

Running this yourself. Read [COMPLIANCE.md](COMPLIANCE.md) first if you intend to collect
from sources you do not own — the decisions there are yours, not the software's.

## The shape of the thing

Three processes, one database, one model:

```
ingest  ──▶  articles (status = pending)  ──▶  analyzer  ──▶  analyses + findings
                                                   │
                                              Ollama (local)
```

`articles.status` **is** the work queue — `pending` in, `analyzed` or `failed` out. There is
no queue table, no broker, no cron entry and no launchd agent: each process takes its own
`--interval` and `restart: unless-stopped` carries it across a reboot or a closed laptop lid.

## Sources

Every source ships **disabled**. Turning collection off is one command; turning it back after
the fact is not, so the default is off.

```bash
make sources                          # what is configured, active state, TDM reservations
make source-enable NAME='Interia'
make source-disable NAME='Interia'
```

Sources are rows in the `sources` table (`rss_url`, `strategy`, `selectors`, `active`,
`tdm_reserved`), not constants in code — the ingest layer knows no outlet by name. Adding one
is an `INSERT`; adding one that needs a different fetch strategy may not be.

`robots.txt` is checked with `protego`, not the standard library, which does not implement
`*`/`$` wildcards and lets through paths that are explicitly disallowed.

One source in the seed data (`Gazeta.pl`) carries a **TDM reservation** under Article 4(3) of
Directive (EU) 2019/790 and is disabled with `tdm_reserved = true`. It parses fine; that is
not the question. See [COMPLIANCE.md](COMPLIANCE.md).

## Running the pipeline

```bash
make ingest                     # one pass over active sources
make ingest ARGS="--dry-run"    # fetch and print, write nothing
make analyze                    # drain the pending queue
make pipeline                   # both, unattended, in the background
make logs                       # follow
make down                       # stop
```

Deduplication is `ON CONFLICT (url_hash) DO NOTHING` — a constraint, not code. Conditional
GET validators (`ETag`, `Last-Modified`) are stored per source and sent on the next pass.

Failures are split deliberately: a **provider** error increments `articles.attempts` and
backs off, a **parse** error marks the article `failed` with no retry. Under a constrained
grammar a parse failure is a configuration fault, and retrying it hides the signal that says
which setting is wrong. Retry cost: five articles in one production run died permanently on
something a retry would have fixed. That trade is deliberate and is recorded, not repaired.

## Ollama

**Run it natively on the host, not in a container** — including when everything else is in
Docker. A Docker Desktop VM on macOS caps memory well below the host and passes no GPU, so
larger quantisations neither fit nor reach Metal: 21.4 s per article versus 4.3 s. Containers
reach the host installation through `host.docker.internal`.

```bash
ollama serve
ollama pull gemma4:latest
```

`OLLAMA_NUM_CTX` must exceed the assembled prompt (~9,200 tokens) plus the response ceiling.
At or below 8192, llama.cpp slides the window and silently drops the beginning of the system
prompt — which looks exactly like a model ignoring its instructions. `OllamaProvider` raises
rather than let that pass.

## Changing the model

**This invalidates every number in [MODEL_CARD.md](MODEL_CARD.md), and nothing will tell
you.** The accuracy figures shown to readers live in `web/messages/{pl,en}.json` under
`reliability`; they are prose in a translation catalogue. Replace them with your own
measurement or delete them — leaving them in place turns a measured claim into a false one.

Measured, not assumed: the input variant is a property of the *model*, not of the task. The
lead helps `gemma4` (precision 41 → 46%) and *hurts* `qwen3-235b` (41 → 33%). Any model
change needs its own measurement.

The evaluation harness is in the repository, so you can run one:

```bash
make gold-candidates            # stratified sample from your own corpus, to annotate
make gold-load                  # load eval/gold_*.csv into Postgres
make eval MODEL="<tag>" ARGS="--input-variant both --grammar-mode both"
make eval-report                # aggregate every stored run
make smoke MODEL="<tag>"        # six diagnostic cases, before committing to a long sweep
```

Every axis that varies is a column on `analyses` (`model_name`, `prompt_version`,
`input_variant`, `grammar_mode`, `source_label`, `run_id`), so a comparison is a `GROUP BY`
rather than a directory of result files.

## Changing the prompt

Any wording change bumps `PROMPT_VERSION` in `src/analyzer/prompts.py`. Without that there is
no way to tell whether an edit helped, and the corpus quietly splits across versions:

```bash
make reanalyze-plan             # how many articles predate the current version
make reanalyze                  # requeue them; the analyzer drains the queue itself
```

Until `reanalyze-plan` reports one version row and `to_requeue = 0`, **no statistic computed
over the whole corpus is a statistic of one configuration.**

## Database

Plain numbered SQL in `migrations/`, applied by `make migrate`, tracked in
`schema_migrations`. Never edit a migration that has been applied anywhere — add a new one.

PostgreSQL 18 is required for `uuidv7()`. Note that 18+ images want the volume mounted at
`/var/lib/postgresql`, not `/var/lib/postgresql/data`; the older path makes the container
abort on startup.

```bash
make audit-data                 # offset, queue and consistency defects in the stored corpus
```

## Accounts and personal data

The API supports accounts (registration, sessions, saved articles, category subscriptions).
If you enable them, **you are the data controller** for whatever your users store.

What the code gives you: Argon2id password hashing, server-side sessions whose token is
stored only as a SHA-256 digest, `httpOnly` + `SameSite=Lax` + `Secure` cookies by default,
rate limiting on sign-in per email address, and account deletion that cascades.

A second budget, per client address, exists and is **off by default**. It has to be: the
front end proxies `/api/*` to the API, so every request reaches the API from the `web`
container and an address read off the connection names nobody. Counted against it, the
budget would be one bucket shared by every reader — ten wrong passwords from anybody would
answer 429 to everybody. Set `TRUST_PROXY_IP=1` only once a reverse proxy in front of this
sets or appends `X-Forwarded-For` itself; see "Serving it" below.

What it does not give you: password reset (there is no mail path), email verification, or any
retention schedule beyond deletion on request. Subscriptions are a stored set of categories —
nothing sends anything.

`COOKIE_SECURE=0` exists for local development over plain HTTP. In any deployment reachable
by anyone else, leave it at 1.

## The operator panel

There is one, at `/pl/ops` or `/en/ops`: corpus counts, which prompt versions the stored
analyses were produced under, queue depth, finding types, recent drift and fetch errors, plus
the same integrity checks `make audit-data` runs, on the same definitions. It is read-only —
nothing there changes any state.

It is behind the `admin` role, and **nothing in the application grants that role**. That is
deliberate: a running service able to promote its own accounts is one bug away from promoting
somebody else's. Register normally, then promote yourself from the machine that holds the
database:

```bash
make admin EMAIL='you@example.com'
```

**To an account without the role, every `/ops` path returns 404, not 403** — the operator
surface is not confirmed to people who go looking for it. If you have not run the command
above, the panel is indistinguishable from a page that does not exist. That is the intended
behaviour and it is also the first thing to check when it seems broken.

## Serving it

`make dev` is the development loop — Postgres, migrations, API and front end in one terminal.
It is not a deployment.

The deployment is the `full` compose profile, which builds and runs both images:

```bash
docker compose --profile full up -d --build
```

Both bind to loopback only — `127.0.0.1:3000` for the front end, `127.0.0.1:8000` for the
API. **Nothing in this repository terminates TLS or faces the internet**, and that is
deliberate: the reverse proxy is the one part of a deployment that belongs to whoever owns
the domain. Put one in front of port 3000, and give it three jobs:

1. **Terminate TLS**, and leave `COOKIE_SECURE=1`. The session cookie is `Secure` by
   default, and a browser silently discards it over plain HTTP — sign-in then appears to do
   nothing at all.
2. **Set `X-Forwarded-For` yourself** — `proxy_set_header X-Forwarded-For $remote_addr;` in
   nginx, or the appending form. Whatever the caller sent must not survive. Only once this
   holds may you set `TRUST_PROXY_IP=1`, which turns on the per-client half of the sign-in
   rate limit. Without it, leave the setting at 0: an unverified header is a rate limit the
   party being limited gets to write.
3. **Rate-limit `/api/auth/*`.** The application limits sign-in per email address; nothing
   in it limits registration, because no limit keyed on data the caller chooses would work.
   `limit_req` on that path is the answer, and it belongs where the addresses are real.

Do not publish port 8000. The API is reached through the front end's `/api/*` rewrite, which
is what keeps the session cookie same-site, and it is the only path the front end uses.

Measured cost of the analysis itself: a server without a GPU is enough — 21.4 s per article,
500 articles in about three hours.

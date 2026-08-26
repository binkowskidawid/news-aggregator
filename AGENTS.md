# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Copilot, Cursor, or any other
tool that reads `AGENTS.md`) when working with code in this repository.

## Essential Commands

```bash
# Start Postgres and apply the schema — all the feasibility gate needs
make up && make migrate

# Postgres, the API and the front end in one terminal; Ctrl-C stops all of it.
# First run needs `make sync` and `cd web && pnpm install`.
make dev            # front on :3000, API on :8000

# Fast local loop; mirrors CI exactly
make sync
make check          # ruff + ruff format + mypy strict + pytest
make web-check      # tsc + eslint + node:test, for web/

# Sanity-check one model against the diagnostic cases before a long sweep
make smoke MODEL="SpeakLeash/bielik-11b-v3.0-instruct:Q6_K"

# See every available command
make help
```

**Ollama must be running natively on the host** (`ollama serve`), not in a container. The
compose file declares an `ollama` service under the `server` profile for deployment only.

## Architecture Overview

A pipeline that fetches Polish news articles, classifies them by topic, and reports
specific language techniques with verbatim supporting quotes.

1. **Ingest** (`src/ingest/`) — tiered fetching, cheapest first: RSS, then static HTML,
   then a headless browser. Tiers 1 and 2 are in use; no portal has needed tier 3 yet.
2. **Analysis** (`src/analyzer/`) — one LLM call per article returns a schema-constrained
   JSON object; every quote it contains is then verified against the source text.
   `articles.status` is the work queue: `pending` in, `analyzed`/`failed` out.
3. **Evaluation** (`src/evals/`) — runs a labelled set through candidate models and
   produces the numbers that decide whether the local-model architecture holds.

Ingest and analysis run unattended as containers (`make pipeline`); each carries its own
`--interval`, so there is no cron entry or launchd agent anywhere in the system.

**The feasibility gate is answered and this is now a product.** The question it settled —
whether a locally hosted 7–12B model analyses Polish press language well enough to publish
— is answered by the figures in `MODEL_CARD.md`, which is the only place they are
maintained. The API and the reader-facing front end followed it; `README.md` states what
ships and how often it is right.

**Code layout:** everything lives flat under `src/`, never installed as a package.
`pyproject.toml` sets `[tool.uv] package = false`; `pythonpath = ["src"]` (pytest) and
`mypy_path = "src"` (mypy) make each package importable by its bare name
(`from domain.analysis import AnalysisResult`). `python -m` needs `PYTHONPATH=src`, which
the Makefile sets. `tests/`, `prompts/`, `migrations/`, and `eval/` stay at the repo root.

**Tech stack:** Python 3.14, PostgreSQL 18, Ollama (local models), OpenRouter (cloud
baseline), Pydantic v2, httpx, rapidfuzz.

## Core Modules

| Path | Purpose |
| --- | --- |
| `src/domain/analysis.py` | The output contract. One definition generates the JSON Schema, validates the response, and describes the database rows |
| `src/analyzer/prompts.py` | Message assembly: per-request nonce, input variant, optional source label |
| `src/analyzer/validator.py` | Quote verification with offsets mapped back to the original string |
| `src/analyzer/analyze.py` | The full path: prompt → model → parse → verify |
| `src/analyzer/providers/` | `LLMProvider` protocol with Ollama and OpenAI-compatible implementations |
| `src/analyzer/store.py` | The queue: pending selection with retry back-off, the analyses/findings writer shared with the eval harness, and `FailureTracker` |
| `src/analyzer/__main__.py` | One analysis pass; `make analyze` |
| `src/ingest/fetch.py` | HTTP hygiene, RSS and listing parsing, robots checks |
| `src/ingest/store.py` | Article upsert, `fetch_errors`, conditional-GET validators |
| `src/evals/diagnostics.py` | Six articles that separate analysis from vocabulary matching, each carrying the rationale for why it is diagnostic |
| `src/evals/smoke.py` | Pass/fail run over those cases |
| `src/config.py` | Environment settings; a small `load_dotenv` that does not override the shell |
| `prompts/` | Versioned prompt text. Files, not string literals |

## Development Patterns

- **One source of truth for the contract**: `AnalysisResult` produces the schema sent to
  the model, validates the reply, and mirrors the CHECK constraints in
  `migrations/001_init.sql`. Changing the taxonomy means changing all three together.
- **Provider protocol, not provider classes**: adding a backend means implementing
  `LLMProvider`. Nothing upstream of it knows which model answered.
- **Constrained sampling is measured, not assumed**: `grammar_mode` is a stored column,
  because forcing a schema can push a weaker model into shallower analysis while keeping
  the output structurally perfect. `schema` and `json` are both run.
- **Every axis the evaluation varies is a column** on `analyses` (`model_name`,
  `prompt_version`, `input_variant`, `grammar_mode`, `source_label`, `run_id`). A
  comparison is a `GROUP BY`, not a directory of result files.
- **Prompt text lives in `prompts/` and carries a version.** Any wording change bumps
  `PROMPT_VERSION`; without that there is no way to tell whether an edit helped.

## Testing Strategy

`pytest` with `asyncio_mode = "auto"`. Tests assert properties, not transcripts —
temperature 0.1 is not determinism.

The quote validator carries the heaviest coverage because it is the mechanism the product
rests on. Its offset tests all slice the *original* source string with the returned span
and compare against expected text; that is what the UI depends on and what a change to
normalisation would otherwise break silently.

New provider implementations need a test against a saved real response. A parser that
broke should fail in CI, not after a week of quiet wrong answers.

## Database Migrations

Plain numbered SQL in `migrations/` (`001_init.sql`, `002_...sql`). Apply with
`make migrate`. Never edit a migration that has been applied anywhere but this machine —
add a new one.

Requires PostgreSQL 18 for `uuidv7()`. Note that Postgres 18+ images want the volume
mounted at `/var/lib/postgresql`, not `/var/lib/postgresql/data`; the older path makes the
container abort on startup.

## Critical Implementation Notes

1. **A quote that cannot be found in the source text is discarded, always.** A tool that
   accuses named outlets of manipulation cannot cite sentences they never published. This
   is also the only defence against prompt injection that does not depend on the model's
   cooperation. Never add a repair heuristic that makes an unverifiable quote pass.
2. **`num_ctx` must exceed the prompt plus the response ceiling.** The assembled prompt
   measures roughly 9,200 tokens; at or below 8,192 llama.cpp slides the window and
   silently drops the start of the system prompt, which looks exactly like a model
   ignoring its instructions. `OllamaProvider` raises rather than let this pass.
3. **The model is never told which outlet published the text.** With outlets of opposing
   political profiles on one list, brand recognition could shift thresholds on identical
   language. `source_label` exists solely so the brand-bias probe can test that.
4. **OpenRouter requests always carry `require_parameters: true`.** Structured-output
   support is a property of the endpoint, not the model; without the flag the request may
   route to a vendor that ignores `response_format` and returns unconstrained output that
   looks constrained.
5. **Article text is untrusted input for the entire pipeline.** It stays inside
   nonce-delimited tags in the user turn, is never concatenated into the system prompt,
   and is never edited to remove instruction-like phrasing — that would alter the material
   we were asked to assess.
6. **A response that fails to parse is recorded, not repaired.** Its usual causes are
   configuration faults, and a heuristic that patches the JSON hides the signal saying
   which setting is wrong.
7. **Enum values are the wire format.** `emotional_load`, `polityka`, and the rest are
   what the model emits and what the CHECK constraints accept. Never translate or rename
   them casually.

## Engineering Rules

### Scope and verification

- Make the smallest change that solves the problem. Do not refactor adjacent code.
- Reuse the existing contract, provider protocol, and validator before adding new ones.
- Validate with `make check`. Before claiming a model result, run `make smoke`.
- Never report a measurement you did not take. Quote real output.

### Python and code organisation

- Type hints everywhere; `mypy --strict` must pass. No `Any` without a comment saying why.
- Pydantic models at every data boundary (model output, config files, API payloads).
- No bare `except:` — catch specific exceptions.
- Comments explain **why**, never **what**. Write them in English, like the rest of the
  code. Polish is reserved for prompt text, contract values, and generated reports.
- All application code lives under `src/`, flat. Never add a new importable top-level
  directory outside it.

### Data and performance

- No database queries inside loops — batch and map in memory.
- Use a connection pool, never a fresh connection per call.
- `asyncio.gather` for independent concurrent reads.
- Constraints belong in the schema, not only in application code.

### LLM work

- All model calls go through an `LLMProvider` implementation. Never call an HTTP endpoint
  directly from analysis code.
- Record `latency_ms`, `tokens_in`, and `tokens_out` on every call. They are how a
  configuration fault gets told apart from a capability limit.
- Never hand-type a model version or a package version — check the registry (`ollama list`)
  or let `uv add` resolve it.

### Logging

- Use the `logging` module, not `print()`, outside one-off scripts.
- Never log API keys or full prompt text at INFO level.

### Git

- `docs/` is deliberately ignored: it holds local working notes and the reports that
  `make audit-sources` and `make eval-report` regenerate. It is absent from a clone.
- Do not run state-changing git operations (commit, push, checkout, branch, reset, merge,
  rebase) unless explicitly asked. Read-only commands are fine.

### Accuracy and uncertainty

- Never invent package versions, API behaviour, or model capabilities.
- Verify claims against the repository, command output, or authoritative documentation
  before presenting them as fact.
- State uncertainty explicitly rather than guessing.

## Developer Tools

- Use `uv` for everything — never `pip install` or `poetry` in this repo.
- Python 3.14+.
- `make help` lists every shortcut; prefer it over hand-typed `docker compose` / `uv run`.
- Working notes live in `docs/JOURNAL.md` (**reverse-chronological — newest entry at the
  top**, so the current state is the first thing read) and `docs/PROGRESS.md` (status).
  Both are **local only and not in the repository**: where the directory is missing, the
  code and the root documents are the whole record.

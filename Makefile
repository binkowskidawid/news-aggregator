# news-aggregator. `make help` lists available commands.
.DEFAULT_GOAL := help
COMPOSE := docker compose

# Load .env if present so migrate/psql targets see POSTGRES_USER etc. Safe to omit on a
# fresh clone before `cp .env.example .env` — falls back to the defaults below.
-include .env
export
POSTGRES_USER ?= news
POSTGRES_DB ?= news

# Application code lives flat under src/ and is never installed as a package. pytest and
# mypy learn that from pyproject.toml; `python -m` needs to be told separately.
PYTHONPATH := src

.PHONY: help up down logs ps migrate clean \
	sync lint format typecheck test check install-hooks \
	audit-sources audit-data smoke eval eval-report ingest analyze pipeline \
	api sources source-enable source-disable reanalyze-plan reanalyze \
	dev web web-check api-types

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' Makefile | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

## --- Stack ------------------------------------------------------------------

up: ## Start postgres (the only service the evaluation gate needs)
	$(COMPOSE) up -d --wait postgres

down: ## Stop the stack (named volumes are kept)
	$(COMPOSE) down

logs: ## Follow logs for all running containers
	$(COMPOSE) logs -f

ps: ## Show container status
	$(COMPOSE) ps

# Applying every file on every run only worked while there was exactly one migration and
# exactly one fresh database. A ledger of what has run makes `make migrate` safe to repeat,
# which is what lets a migration be added without wiping the evaluation results.
PSQL = $(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U $(POSTGRES_USER) -d $(POSTGRES_DB)

migrate: ## Apply migrations/*.sql that have not been applied yet, in order
	@$(PSQL) -q -c "CREATE TABLE IF NOT EXISTS schema_migrations ( \
	    filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
	@for f in migrations/*.sql; do \
	  name=$$(basename "$$f"); \
	  if [ -n "$$($(PSQL) -tAq -c "SELECT 1 FROM schema_migrations WHERE filename = '$$name'")" ]; then \
	    echo "-> $$name already applied"; continue; \
	  fi; \
	  echo "-> applying $$name"; \
	  $(PSQL) -f "/$$f" || exit 1; \
	  $(PSQL) -q -c "INSERT INTO schema_migrations (filename) VALUES ('$$name')"; \
	done

clean: ## Stop the stack and DELETE named volumes (wipes all Postgres data)
	$(COMPOSE) down -v

## --- Local development ------------------------------------------------------

sync: ## Install/refresh local dependencies
	uv sync --all-extras

lint: ## Run ruff (lint + format check)
	uv run ruff check .
	uv run ruff format --check .

format: ## Apply ruff formatting and autofixes
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Run mypy in strict mode
	uv run mypy src tests

test: ## Run the test suite
	uv run pytest

check: lint typecheck test ## Run everything CI runs

install-hooks: ## Install the pre-commit hook (ruff on staged files)
	uv run pre-commit install

## --- Feasibility gate -------------------------------------------------------

smoke: ## Sanity-check one model on the diagnostic cases. Usage: make smoke MODEL=<tag>
	@test -n "$(MODEL)" || { echo "MODEL is required, e.g. make smoke MODEL=gemma4"; exit 1; }
	uv run python -m evals.smoke --model "$(MODEL)" $(ARGS)

audit-sources: ## Probe every configured portal for RSS/scraping viability
	uv run python -m evals.audit_sources

audit-data: ## Check the stored corpus for offset, queue and consistency defects
	uv run python -m evals.audit_data

## --- Application ------------------------------------------------------------

dev: up migrate ## Everything a reader needs, in one terminal. Ctrl-C stops both processes
	@test -d web/node_modules || { \
	  echo "web/node_modules is missing — run: cd web && pnpm install"; exit 1; }
	@echo ""
	@echo "  front  http://localhost:3000     (redirects to /pl; /en for English)"
	@echo "  api    http://localhost:8000/docs"
	@echo ""
	@trap 'kill 0' EXIT INT TERM; \
	  uv run uvicorn api.main:app --app-dir src --reload & \
	  cd web && pnpm dev

api: ## Run the HTTP service alone, with reload. Usage: make api [ARGS="--port 8001"]
	uv run uvicorn api.main:app --app-dir src --reload $(ARGS)

## --- Web --------------------------------------------------------------------

web: ## Run the front end in development. Needs `make api` in another shell
	cd web && pnpm dev

web-check: ## Everything CI runs for the front end
	cd web && pnpm typecheck && pnpm lint && pnpm test

api-types: ## Regenerate web/lib/api-types.ts from the FastAPI schema
	@uv run python -c "import json; from api.main import app; \
	  print(json.dumps(app.openapi(), ensure_ascii=False))" > web/lib/openapi.json
	@cd web && pnpm exec openapi-typescript lib/openapi.json -o lib/api-types.ts
	@echo "web/lib/api-types.ts regenerated from the live schema"

## --- Sources ----------------------------------------------------------------

# Every source ships inactive (migration 007). Switching one on is the operator's decision
# and carries the operator's duties — see OPERATOR.md before enabling anything.

sources: ## List configured sources: which are active, which reserve TDM rights
	@$(PSQL) -c "SELECT name, active, tdm_reserved, strategy, coalesce(rss_url, '-') AS feed \
	    FROM sources ORDER BY name"

source-enable: ## Start collecting from one source. Usage: make source-enable NAME='Interia'
	@test -n "$(NAME)" || { echo "NAME is required, e.g. make source-enable NAME='Interia'"; exit 1; }
	@$(PSQL) -c "UPDATE sources SET active = true WHERE name = '$(NAME)' RETURNING name, active, tdm_reserved"

source-disable: ## Stop collecting from one source. Usage: make source-disable NAME='Interia'
	@test -n "$(NAME)" || { echo "NAME is required, e.g. make source-disable NAME='Interia'"; exit 1; }
	@$(PSQL) -c "UPDATE sources SET active = false WHERE name = '$(NAME)' RETURNING name, active"

ingest: ## Fetch one pass from every active source. Usage: make ingest [ARGS="--dry-run"]
	uv run python -m ingest $(ARGS)

analyze: ## Analyse pending articles. Usage: make analyze [ARGS="--limit 3 --dry-run"]
	uv run python -m analyzer $(ARGS)

# Bumping PROMPT_VERSION changes only what is produced next, so a corpus accumulates
# versions: one article analysed under v1.1.0 sits in the feed beside one under v1.1.3,
# and no reader can tell. Requeueing is the whole repair — `analyses` is append-only, so
# the old rows stay as the historical record and the new pass simply wins on recency.
#
# Read from the module rather than repeated here: a version written down twice is a
# version that will disagree with itself.
CURRENT_PROMPT = $(shell PYTHONPATH=src uv run python -c "from analyzer.prompts import PROMPT_VERSION; print(PROMPT_VERSION)")

# Gold articles are excluded for the reason load_pending() excludes them: they are the
# measuring instrument, not the corpus.
STALE_ARTICLES = FROM articles a WHERE a.status = 'analyzed' \
    AND NOT EXISTS (SELECT 1 FROM gold_articles g WHERE g.article_id = a.id) \
    AND NOT EXISTS (SELECT 1 FROM article_latest_analysis l \
                    WHERE l.article_id = a.id AND l.prompt_version = '$(CURRENT_PROMPT)')

reanalyze-plan: ## Show how many articles predate the current prompt version, and write nothing
	@echo "current prompt version: $(CURRENT_PROMPT)"
	@$(PSQL) -c "SELECT l.prompt_version, count(*) AS articles \
	    FROM articles a JOIN article_latest_analysis l ON l.article_id = a.id \
	    WHERE a.status = 'analyzed' \
	      AND NOT EXISTS (SELECT 1 FROM gold_articles g WHERE g.article_id = a.id) \
	    GROUP BY 1 ORDER BY 1"
	@$(PSQL) -c "SELECT count(*) AS to_requeue $(STALE_ARTICLES)"

reanalyze: ## Requeue every article older than the current prompt version (run reanalyze-plan first)
	@echo "current prompt version: $(CURRENT_PROMPT)"
	@$(PSQL) -c "UPDATE articles SET status = 'pending', attempts = 0, last_attempt_at = NULL \
	    WHERE id IN (SELECT a.id $(STALE_ARTICLES))"

# Scheduling is the container's job: both services carry their own --interval and restart
# with the Docker daemon, so there is no crontab or launchd agent to install or forget.
pipeline: ## Run ingest + analyzer in the background (postgres starts as a dependency)
	$(COMPOSE) --profile pipeline up -d --build

gold-candidates: ## Sample analysed articles to annotate. Usage: make gold-candidates [ARGS="--neutral 40"]
	uv run python -m evals.candidates $(ARGS)

gold-check: ## Validate eval/gold_*.csv without touching the database
	uv run python -m evals.gold --check-only

gold-load: ## Rebuild the gold set in Postgres from eval/gold_*.csv
	uv run python -m evals.gold

holdout-check: ## Validate eval/holdout_*.csv without touching the database
	uv run python -m evals.gold --split holdout --check-only

holdout-load: ## Load the held-out set from eval/holdout_*.csv; leaves the main set alone
	uv run python -m evals.gold --split holdout

eval: ## Run the gold set through one model. Usage: make eval MODEL=<tag> [ARGS="..."]
	@test -n "$(MODEL)" || { echo "MODEL is required, e.g. make eval MODEL=gemma4"; exit 1; }
	uv run python -m evals.run_eval --model "$(MODEL)" $(ARGS)

eval-cloud: ## Run the gold set through the OpenRouter baseline. Usage: make eval-cloud [ARGS="..."]
	uv run python -m evals.run_eval --provider openrouter $(ARGS)

eval-report: ## Aggregate all stored runs into the comparison report
	uv run python -m evals.report

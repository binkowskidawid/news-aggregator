"""One analysis pass over the articles waiting in the queue.

    make analyze                                  # drain the queue
    make analyze ARGS="--limit 3 --dry-run"       # analyse three and print, write nothing
    make analyze ARGS="--limit 20"                # a bounded bite

Like ingest, this exits after one pass and leaves scheduling to whatever started it.
``--interval`` exists for the compose service, which runs under `restart: unless-stopped`:
there a process that exits is restarted at once, and the loop that would create hammers
Ollama instead of the portals.

The configuration is fixed rather than exposed: `grammar_mode` is `schema` and the model
is shown the lead whenever the article carries one, both settled by measurement. Comparing
configurations is what `make eval` is for; production has one right answer.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from collections import Counter
from typing import Final

import httpx
from psycopg_pool import AsyncConnectionPool

from analyzer.analyze import analyze_article
from analyzer.providers.base import GrammarMode, LLMProvider, ProviderError
from analyzer.providers.factory import build_provider
from analyzer.store import (
    CONSECUTIVE_FAILURE_LIMIT,
    FailureTracker,
    PendingArticle,
    bump_attempts,
    load_pending,
    mark_analyzed,
    outcome,
    save_analysis,
)
from config import Settings, load_dotenv
from db import pool

LOG_FORMAT: Final = "%(asctime)s %(levelname)-7s %(message)s"
GRAMMAR_MODE: Final[GrammarMode] = "schema"
TITLE_PREVIEW_CHARS: Final = 70

logger = logging.getLogger("analyzer")


def _describe(article: PendingArticle, assessment: str, findings: list[str]) -> str:
    title = article.title[:TITLE_PREVIEW_CHARS]
    return f"{assessment:14s} {', '.join(findings) or '-':30s} {title}"


async def _run_pass(
    connection_pool: AsyncConnectionPool,
    provider: LLMProvider,
    *,
    model: str,
    limit: int | None,
    dry_run: bool,
) -> int:
    """Analyse everything currently due. Returns the number of articles stored."""
    pending = await load_pending(connection_pool, limit)
    if not pending:
        logger.info("nothing pending")
        return 0

    run_id = uuid.uuid4()
    logger.info("run_id=%s model=%s %d article(s) due", run_id, model, len(pending))

    tracker = FailureTracker()
    by_type: Counter[str] = Counter()
    stored = 0
    unparsed = 0

    for article in pending:
        try:
            analysis = await analyze_article(
                provider,
                title=article.title,
                # Measured on the same 100 gold articles, so the comparison carries no
                # difference in the samples: showing the lead moves the located span from
                # 52% to 60% and cuts accusations against neutral texts from 12/37 to
                # 3/36. Sources that publish no lead fall back to the headline alone, and
                # `input_variant` records which of the two each row actually got.
                lead=article.lead,
                model=model,
                grammar_mode=GRAMMAR_MODE,
            )
        except ProviderError as exc:
            logger.warning("%s: %s", article.article_id, exc)
            if tracker.record_failure(article.article_id):
                logger.error(
                    "%d provider errors in a row — aborting the pass and charging none "
                    "of them to the articles",
                    CONSECUTIVE_FAILURE_LIMIT,
                )
                break
            continue
        tracker.record_success()

        status, category = outcome(analysis)
        findings = [finding.type.value for finding in analysis.findings]
        by_type.update(findings)
        if status == "failed":
            unparsed += 1
            logger.warning("%s: unparseable — %s", article.article_id, analysis.parse_error)
        else:
            logger.info("%s", _describe(article, analysis.overall_assessment or "?", findings))

        if dry_run:
            continue

        await save_analysis(
            connection_pool,
            article_id=article.article_id,
            run_id=run_id,
            provider=provider.name,
            model=model,
            analysis=analysis,
        )
        await mark_analyzed(connection_pool, article.article_id, status, category)
        stored += 1

    if dry_run:
        logger.info("dry run: nothing written")
    else:
        await bump_attempts(connection_pool, tracker.article_ids)

    logger.info(
        "%d stored (%d unparseable), %d provider failure(s) charged; findings: %s",
        stored,
        unparsed,
        len(tracker.article_ids),
        ", ".join(f"{name}={count}" for name, count in by_type.most_common()) or "none",
    )
    return stored


async def _run(args: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings.from_env()

    model = args.model or (
        settings.ollama_model if args.provider == "ollama" else settings.openrouter_model
    )
    if not model:
        logger.error("no model given; pass --model or set OLLAMA_MODEL/OPENROUTER_MODEL")
        return 2

    async with (
        pool(settings.database_url) as connection_pool,
        httpx.AsyncClient() as client,
    ):
        provider = build_provider(client, settings, args.provider)
        while True:
            await _run_pass(
                connection_pool,
                provider,
                model=model,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            if args.interval is None:
                return 0
            logger.info("sleeping %d s", args.interval)
            await asyncio.sleep(args.interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="model tag; defaults to the configured one")
    parser.add_argument("--provider", choices=("ollama", "openrouter"), default="ollama")
    parser.add_argument("--limit", type=int, help="analyse at most this many articles")
    parser.add_argument(
        "--dry-run", action="store_true", help="analyse and report without writing anything"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="keep running, pausing this many seconds between passes (for the container)",
    )
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

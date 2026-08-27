"""One ingest pass over every active source.

    make ingest                                 # fetch and store
    make ingest ARGS="--dry-run"                # fetch and report, write nothing
    make ingest ARGS="--source 'TV Republika'"  # one portal, for debugging a selector

Scheduling is the operating system's job, so this exits after one pass and cron decides
when the next one happens. ``--interval`` exists for the compose service, which runs
under ``restart: unless-stopped``: there, a process that exits is a restart loop
hammering the portals, so it keeps its own timer instead.

    0 */6 * * * cd /path/to/repo && make ingest
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Final

import httpx
from psycopg_pool import AsyncConnectionPool

from config import Settings, load_dotenv
from db import pool
from ingest.fetch import (
    CONNECT_TIMEOUT_S,
    READ_TIMEOUT_S,
    Throttle,
    fetch_source,
    robots_verdict,
    user_agent,
)
from ingest.store import (
    count_articles,
    load_sources,
    mark_fetched,
    mark_unchanged,
    record_error,
    record_fetch_failure,
    save_articles,
)

LOG_FORMAT: Final = "%(asctime)s %(levelname)-7s %(message)s"

logger = logging.getLogger("ingest")


async def _run_pass(
    connection_pool: AsyncConnectionPool,
    client: httpx.AsyncClient,
    names: list[str],
    dry_run: bool,
) -> int:
    """Fetch every active source once. Returns the number of articles newly stored."""
    sources = await load_sources(connection_pool, names)
    if not sources:
        # Every source ships inactive (migration 007), so this is the state a fresh install
        # is in rather than a fault — which is why it is a warning. At ERROR the very first
        # command an operator runs reports what looks like a broken install, and an
        # unattended pass fills its log with red for behaving exactly as designed.
        logger.warning(
            "no active sources matched %s. Nothing is collected until you enable a source: "
            "`make sources` lists what is configured and which publishers reserve "
            "text-and-data-mining rights, `make source-enable NAME='...'` turns one on. "
            "Enabling one is your decision and carries your duties — see OPERATOR.md",
            names or "(all)",
        )
        return 0

    throttle = Throttle()
    before = await count_articles(connection_pool)

    for source in sources:
        # Checked every pass rather than trusted from the audit: this runs unattended on a
        # schedule, and a portal's terms can change between one run and the next.
        verdict = await robots_verdict(client, throttle, source.address)
        if verdict == "DISALLOWED":
            logger.warning("%s: robots.txt disallows %s, skipping", source.name, source.address)
            if not dry_run:
                await record_error(
                    connection_pool, source.id, source.address, "robots_disallowed", verdict
                )
            continue

        result = await fetch_source(client, throttle, source)

        if result.not_modified:
            logger.info("%s: 304 not modified", source.name)
            if not dry_run:
                await mark_unchanged(connection_pool, source.id)
            continue

        if result.error_type:
            logger.error("%s: %s — %s", source.name, result.error_type, result.error_message)
            if not dry_run:
                await record_fetch_failure(connection_pool, source, result)
            continue

        with_lead = sum(1 for article in result.articles if article.lead)
        logger.info(
            "%s: %d article(s), %d with a lead, level %d%s",
            source.name,
            len(result.articles),
            with_lead,
            result.articles[0].fetch_level,
            "" if result.etag is None else ", etag stored",
        )
        if not dry_run:
            await save_articles(connection_pool, source, result.articles)
            await mark_fetched(connection_pool, source.id, result.etag)

    if dry_run:
        logger.info("dry run: nothing written")
        return 0

    new = await count_articles(connection_pool) - before
    logger.info("%d new article(s) stored, %d in total", new, before + new)
    return new


async def _run(args: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings.from_env()
    agent = user_agent(settings.contact_email, "ingest")

    async with (
        pool(settings.database_url) as connection_pool,
        httpx.AsyncClient(
            headers={
                "User-Agent": agent,
                "Accept": "application/rss+xml, application/xml, text/html",
            },
            timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            follow_redirects=True,
        ) as client,
    ):
        while True:
            await _run_pass(connection_pool, client, args.source, args.dry_run)
            if args.interval is None:
                return 0
            logger.info("sleeping %d s", args.interval)
            await asyncio.sleep(args.interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", nargs="*", default=[], help="limit the pass to these source names"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="fetch and report without writing anything"
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

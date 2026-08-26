"""Integrity checks over the corpus already in the database.

    make audit-data

The unit and integration suites test the code that writes these rows. This tests the rows
themselves, which is a different question: a defect introduced before a rule existed leaves
data behind that no later test will ever look at. Both of the faults this module was
written for were exactly that shape — offsets pointing at text the stored quote did not
match, and verdicts contradicting the findings beside them — and both were invisible from
the code, which had been correct since the day the rule was added.

Every check returns a count that must be zero. Anything non-zero exits non-zero, so this
can gate a deployment rather than merely inform one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Final

from analyzer.store import MAX_ATTEMPTS
from config import Settings, load_dotenv
from db import fetch_all, pool


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    why: str
    query: str
    """Must return exactly one row with one integer column."""

    params: tuple[object, ...] = ()


CHECKS: Final[tuple[Check, ...]] = (
    Check(
        name="quote matches its offsets",
        why="the interface highlights by offset and prints the quote; they must be one thing",
        query="""
        SELECT count(*) FROM findings f
        JOIN analyses an ON an.id = f.analysis_id
        JOIN articles a ON a.id = an.article_id
        WHERE substring(CASE f.field WHEN 'title' THEN a.title ELSE a.lead END
                        FROM f.quote_start + 1 FOR f.quote_end - f.quote_start)
              IS DISTINCT FROM f.quote
        """,
    ),
    Check(
        name="offsets inside the field",
        why="an end offset past the text truncates the highlight instead of failing loudly",
        query="""
        SELECT count(*) FROM findings f
        JOIN analyses an ON an.id = f.analysis_id
        JOIN articles a ON a.id = an.article_id
        WHERE f.quote_start < 0
           OR f.quote_end > char_length(CASE f.field WHEN 'title' THEN a.title ELSE a.lead END)
           OR f.quote_end <= f.quote_start
        """,
    ),
    Check(
        name="findings in a field the article has",
        why="a quote located in a lead that does not exist means the offsets index nothing",
        query="""
        SELECT count(*) FROM findings f
        JOIN analyses an ON an.id = f.analysis_id
        JOIN articles a ON a.id = an.article_id
        WHERE f.field = 'lead' AND (a.lead IS NULL OR a.lead = '')
        """,
    ),
    Check(
        name="analysed articles carry an analysis",
        why="the feed would list an article whose detail view has nothing to show",
        query="""
        SELECT count(*) FROM articles a
        WHERE a.status = 'analyzed'
          AND NOT EXISTS (SELECT 1 FROM analyses an WHERE an.article_id = a.id)
        """,
    ),
    Check(
        name="queue is not stuck",
        why="an article out of attempts must be retired, not retried forever",
        query="""
        SELECT count(*) FROM articles
        WHERE status = 'pending' AND attempts >= %s
        """,
        params=(MAX_ATTEMPTS,),
    ),
)

STATS: Final = """
    SELECT
        (SELECT count(*) FROM articles) AS articles,
        (SELECT count(*) FROM articles WHERE status = 'pending') AS pending,
        (SELECT count(*) FROM articles WHERE status = 'failed_permanent') AS retired,
        (SELECT count(*) FROM analyses) AS analyses,
        (SELECT count(*) FROM analyses WHERE parse_error IS NOT NULL) AS parse_errors,
        (SELECT count(*) FROM analyses WHERE consistency_error IS NOT NULL) AS inconsistent,
        (SELECT count(*) FROM findings) AS findings,
        (SELECT count(*) FROM findings WHERE fuzzy_matched) AS fuzzy,
        -- A tripwire, not a metric. The explanations are the only free text this product
        -- publishes, they are written for a Polish reader, and a model that drops an
        -- English clause into one is visible in the interface immediately. Counted rather
        -- than checked: the stopword list cannot tell a leak from an outlet quoting an
        -- English phrase, so a rising number is a reason to look, not a build failure.
        (SELECT count(*) FROM findings
         WHERE explanation ~* '\\m(the|of|and|that|which|with|for|from|this)\\M')
            AS english_leaks
    """


async def _run(_: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings.from_env()

    async with pool(settings.database_url) as connection_pool:
        stats = (await fetch_all(connection_pool, STATS))[0]
        results = [
            (
                check,
                next(
                    iter((await fetch_all(connection_pool, check.query, check.params))[0].values())
                ),
            )
            for check in CHECKS
        ]

    print(
        f"{stats['articles']} articles ({stats['pending']} pending, "
        f"{stats['retired']} retired) · {stats['analyses']} analyses "
        f"({stats['parse_errors']} unparseable, {stats['inconsistent']} inconsistent) · "
        f"{stats['findings']} findings ({stats['fuzzy']} fuzzy, "
        f"{stats['english_leaks']} with English in the explanation)"
    )
    print()

    for check, violations in results:
        mark = "ok  " if violations == 0 else "FAIL"
        print(f"  {mark} {check.name:38s} {violations:>6d}")
        if violations:
            print(f"       {check.why}")

    failed = sum(1 for _, violations in results if violations)
    if failed:
        print(f"\n{failed} check(s) failed")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

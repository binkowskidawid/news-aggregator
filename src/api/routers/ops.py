"""The operator panel's data. Behind an admin role, never in a public response.

This is not a feature of the product, it is the condition for running it. Three defects in
this project's history were invisible from the code and surfaced only from a query against
the live database: 563 analyses produced under a variant that fails both thresholds, quotes
stored as the model wrote them rather than as the source has them, and verdicts contradicting
the findings underneath them. Those queries are frozen into a view here so that nobody has to
remember to run them.

Density of findings per source is deliberately reported as a signal about the model, not
about the publisher. It is here because a source whose numbers move on its own is usually a
broken selector or a changed page template — and it never leaves this router.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from api.deps import AdminUser, Pool
from db import fetch_all, fetch_one
from evals.audit_data import CHECKS, STATS

router = APIRouter(prefix="/ops", tags=["ops"])

DRIFT_DAYS = 14

# One row per prompt version in the corpus. More than one means the feed is showing
# analyses that are not comparable with each other.
_VERSIONS = """
    SELECT prompt_version, count(*) AS articles
    FROM article_latest_analysis
    GROUP BY 1 ORDER BY 1
    """

_QUEUE = """
    SELECT status, count(*) AS articles
    FROM articles GROUP BY 1 ORDER BY 2 DESC
    """

_TYPES = """
    SELECT f.type, count(*) AS findings
    FROM findings f
    JOIN article_latest_analysis l ON l.analysis_id = f.analysis_id
    GROUP BY 1 ORDER BY 2 DESC
    """

# The rejection rate is the quote validator doing its job: a model quoting text the article
# does not contain. A rise means the model drifted, the prompt changed, or an ingest change
# altered the text the offsets are measured against.
_DRIFT = """
    SELECT date_trunc('day', created_at)::date AS day,
           count(*) AS analyses,
           sum(quotes_total) AS quotes,
           sum(quotes_rejected) AS quotes_rejected,
           count(*) FILTER (WHERE consistency_error IS NOT NULL) AS inconsistent,
           count(*) FILTER (WHERE parse_error IS NOT NULL) AS parse_errors,
           round(avg(latency_ms)) AS avg_latency_ms
    FROM analyses
    WHERE created_at > now() - (interval '1 day' * %s)
    GROUP BY 1 ORDER BY 1 DESC
    """

# LEFT JOIN because fetch_errors.source_id is nullable: a failure that happened before a
# source could be identified is exactly the kind an inner join would hide.
_FETCH_ERRORS = """
    SELECT coalesce(s.name, '(unknown)') AS source,
           count(*) AS errors,
           max(fe.occurred_at) AS last_error
    FROM fetch_errors fe
    LEFT JOIN sources s ON s.id = fe.source_id
    WHERE fe.occurred_at > now() - (interval '1 day' * %s)
    GROUP BY 1 ORDER BY 2 DESC
    """


class Check(BaseModel):
    name: str
    why: str
    failures: int
    passing: bool


@router.get("/checks", response_model=list[Check])
async def integrity_checks(_: AdminUser, connection_pool: Pool) -> list[Check]:
    """The same checks `make audit-data` runs, on the same definitions.

    A query per iteration, which the project's rules otherwise forbid — but the loop is over
    five constants, not over rows, so it cannot grow with the data. Folding them into one
    UNION would mean assembling SQL from a tuple that carries its own parameters, which is
    more machinery than five round trips on an admin page are worth.
    """
    results = []
    for check in CHECKS:
        row = await fetch_one(connection_pool, check.query, check.params)
        failures = int(row["count"]) if row else 0
        results.append(
            Check(name=check.name, why=check.why, failures=failures, passing=failures == 0)
        )
    return results


@router.get("/overview")
async def overview(_: AdminUser, connection_pool: Pool) -> dict[str, Any]:
    """Everything the panel renders, in one round trip per section.

    Untyped on purpose: these are counters read straight from SQL, and a Pydantic model per
    section would mean maintaining a schema whose only consumer is a table that prints it.
    """
    stats = await fetch_one(connection_pool, STATS)
    return {
        "corpus": stats or {},
        "prompt_versions": await fetch_all(connection_pool, _VERSIONS),
        "queue": await fetch_all(connection_pool, _QUEUE),
        "finding_types": await fetch_all(connection_pool, _TYPES),
        "drift": await fetch_all(connection_pool, _DRIFT, (DRIFT_DAYS,)),
        "fetch_errors": await fetch_all(connection_pool, _FETCH_ERRORS, (DRIFT_DAYS,)),
    }

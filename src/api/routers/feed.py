"""The feed and one article's analysis — everything a reader who is not logged in can see.

Both endpoints read ``article_latest_analysis`` rather than ``analyses``, so neither
repeats the rule about which of an article's analyses counts.

Nothing here aggregates by publisher. The measured precision of a single finding is 42% on
material the prompt was never tuned against; a per-outlet count built from that is a claim
about a named company carrying the error of every finding under it and no quote to check
it against. The reader gets sentences, each with the fragment it refers to and a link to
where it was published.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from api.deps import Pool
from api.schemas import ArticleDetail, Feed, FeedItem, FindingOut, Provenance
from db import fetch_all, fetch_one
from domain.analysis import Category

router = APIRouter(tags=["feed"])

MAX_PAGE = 100

# The `has_findings` predicate is spelled out in both queries rather than shared through a
# constant: composing SQL from pieces is the shape ruff flags as injection whether or not the
# piece is a literal, and the two queries already repeat the category predicate the same way.
# EXISTS rather than a count compared to zero — the planner stops at the first row, and two
# thirds of the corpus carries no finding at all.
#
# `articles.category` rather than the analysis column: mark_analyzed() writes the verdict
# back onto the article, and articles_feed_idx (category, published_at DESC) is built on it.
_FEED = """
    SELECT a.id, a.title, a.lead, a.url, s.name AS source, a.published_at,
           l.category, l.overall_assessment,
           (SELECT count(*) FROM findings f WHERE f.analysis_id = l.analysis_id) AS finding_count
    FROM articles a
    JOIN sources s ON s.id = a.source_id
    JOIN article_latest_analysis l ON l.article_id = a.id
    WHERE (%s::text IS NULL OR a.category = %s)
      AND (%s::bool IS NULL
           OR %s = EXISTS (SELECT 1 FROM findings f WHERE f.analysis_id = l.analysis_id))
    ORDER BY a.published_at DESC NULLS LAST, a.id DESC
    LIMIT %s OFFSET %s
    """

_FEED_TOTAL = """
    SELECT count(*) AS total
    FROM articles a
    JOIN article_latest_analysis l ON l.article_id = a.id
    WHERE (%s::text IS NULL OR a.category = %s)
      AND (%s::bool IS NULL
           OR %s = EXISTS (SELECT 1 FROM findings f WHERE f.analysis_id = l.analysis_id))
    """

_ARTICLE = """
    SELECT a.id, a.title, a.lead, a.url, s.name AS source, a.published_at,
           l.analysis_id, l.category, l.category_confidence, l.overall_assessment,
           l.model_name, l.prompt_version, l.created_at AS analysed_at
    FROM articles a
    JOIN sources s ON s.id = a.source_id
    JOIN article_latest_analysis l ON l.article_id = a.id
    WHERE a.id = %s
    """

_FINDINGS = """
    SELECT type, field, quote, quote_start AS start, quote_end AS "end",
           explanation, confidence, neutral_alternative
    FROM findings
    WHERE analysis_id = %s
    ORDER BY field, quote_start
    """


@router.get("/feed", response_model=Feed)
async def read_feed(
    connection_pool: Pool,
    category: Category | None = None,
    has_findings: bool | None = None,
    limit: int = Query(default=20, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
) -> Feed:
    """Every analysed article by default, newest first.

    ``has_findings`` narrows the list in either direction and is left out entirely by
    default: a feed filtered to reported articles reads as a list of accusations, while
    measured precision under prompt v1.1.3 is 42% — more findings are wrong than right.
    Narrowing is available to a reader who asks for it and is never the view they are handed.
    """
    selected = category.value if category else None
    filters = (selected, selected, has_findings, has_findings)
    rows = await fetch_all(connection_pool, _FEED, (*filters, limit, offset))
    total = await fetch_one(connection_pool, _FEED_TOTAL, filters)

    return Feed(
        items=[FeedItem.model_validate(row) for row in rows],
        total=total["total"] if total else 0,
        limit=limit,
        offset=offset,
    )


@router.get("/articles/{article_id}", response_model=ArticleDetail)
async def read_article(connection_pool: Pool, article_id: uuid.UUID) -> ArticleDetail:
    """One article with the fragments reported in it.

    404 also covers an article that exists but has never been analysed under the production
    configuration: there is nothing here to show a reader, and saying "no analysis yet"
    would invite a client to render an empty verdict as a clean bill of health.
    """
    article = await fetch_one(connection_pool, _ARTICLE, (article_id,))
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no analysed article with that id")

    findings = await fetch_all(connection_pool, _FINDINGS, (article["analysis_id"],))

    return ArticleDetail(
        **article,
        findings=[FindingOut.model_validate(row) for row in findings],
        provenance=Provenance(
            model_name=article["model_name"],
            prompt_version=article["prompt_version"],
            analysed_at=article["analysed_at"],
        ),
    )

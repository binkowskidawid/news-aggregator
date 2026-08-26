"""Load the hand-labelled evaluation set from CSV into Postgres.

The CSVs are the artefact: they live in git, they are reviewable line by line, and a
disagreement about a label is a diff rather than an argument. The database is a
projection of them, rebuilt by this loader.

Offsets are computed here rather than typed by hand, using the same
:func:`analyzer.validator.locate_quote` the analyser uses on model output. A reference
quote that cannot be found verbatim in the article is an annotator's typo, and it fails
the load loudly — the alternative is a label pinned to the wrong span, which would
silently penalise every model measured against it.

    make gold-load
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from psycopg_pool import AsyncConnectionPool

from analyzer.validator import locate_quote
from config import Settings, load_dotenv
from db import execute, execute_many, fetch_all, pool
from ingest.fetch import canonical_url, digest

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SPLITS: Final = ("main", "holdout")
"""``main`` is the 118 articles every prompt version was tuned against; ``holdout`` was
annotated afterwards from days the tuning never sampled. They share these tables because
the scorer separates configurations by ``run_id`` anyway — see migration 006."""


def csv_paths(split: str) -> tuple[Path, Path]:
    """The set and label files for one split. ``main`` keeps its original filenames."""
    stem = "gold" if split == "main" else split
    return (
        REPO_ROOT / "eval" / f"{stem}_set.csv",
        REPO_ROOT / "eval" / f"{stem}_labels.csv",
    )


ANNOTATOR: Final = "claude+dawid"
"""Recorded on every row. Scores against these labels measure agreement with this
annotator, not ground truth, and the report is required to say so."""

RSS_FETCH_LEVEL: Final = 1


@dataclass(frozen=True, slots=True)
class GoldArticle:
    slug: str
    portal: str
    url: str
    title: str
    lead: str
    kind: str
    expected_category: str
    expected_assessment: str


@dataclass(frozen=True, slots=True)
class GoldLabel:
    slug: str
    type: str
    field: str
    quote: str
    note: str
    start: int
    end: int


class GoldError(RuntimeError):
    """The CSVs are inconsistent with each other or with the article text."""


def read_articles(path: Path) -> list[GoldArticle]:
    with path.open(encoding="utf-8") as handle:
        return [GoldArticle(**row) for row in csv.DictReader(handle)]


def read_labels(articles: list[GoldArticle], path: Path) -> list[GoldLabel]:
    """Read the labels and resolve every quote to a span in the article text."""
    by_slug = {article.slug: article for article in articles}
    labels: list[GoldLabel] = []
    problems: list[str] = []

    with path.open(encoding="utf-8") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            article = by_slug.get(row["slug"])
            if article is None:
                problems.append(f"line {line}: unknown slug {row['slug']!r}")
                continue

            text = article.title if row["field"] == "title" else article.lead
            match = locate_quote(row["quote"], text)
            if match is None:
                problems.append(
                    f"line {line}: {row['slug']}/{row['field']} quote not found: {row['quote']!r}"
                )
                continue
            if match.fuzzy:
                # A reference label is written by a human with the text in front of them.
                # Needing fuzzy matching means the quote was mistyped, and an approximate
                # span would make every model look worse than it is at exactly this spot.
                problems.append(
                    f"line {line}: {row['slug']}/{row['field']} matched only fuzzily: "
                    f"{row['quote']!r} vs {text[match.start : match.end]!r}"
                )
                continue

            labels.append(
                GoldLabel(
                    slug=row["slug"],
                    type=row["type"],
                    field=row["field"],
                    quote=row["quote"],
                    note=row["note"],
                    start=match.start,
                    end=match.end,
                )
            )

    if problems:
        raise GoldError("gold labels do not match the article text:\n  " + "\n  ".join(problems))
    return labels


def check_consistency(articles: list[GoldArticle], labels: list[GoldLabel]) -> None:
    """Verify the expected assessment follows from the labels, per the prompt's own rule.

    The prompt derives ``overall_assessment`` purely from how many findings clear the
    confidence bar. Recomputing it here stops the reference set from drifting away from
    the contract it is meant to measure — an expectation that contradicts the prompt would
    score every model as wrong for obeying instructions.
    """
    counts: dict[str, int] = dict.fromkeys((article.slug for article in articles), 0)
    for label in labels:
        counts[label.slug] += 1

    problems = []
    for article in articles:
        count = counts[article.slug]
        expected = (
            "neutral" if count == 0 else ("mildly_loaded" if count <= 2 else "heavily_loaded")
        )
        if expected != article.expected_assessment:
            problems.append(
                f"{article.slug}: {count} label(s) implies {expected}, "
                f"CSV says {article.expected_assessment}"
            )
        if article.kind in {"neutral", "borderline"} and count:
            problems.append(
                f"{article.slug}: kind={article.kind} must carry no labels, has {count}"
            )
    if problems:
        raise GoldError("gold set is internally inconsistent:\n  " + "\n  ".join(problems))


def check_stored_text(articles: list[GoldArticle], stored: Mapping[str, Mapping[str, Any]]) -> None:
    """Verify the rows already in ``articles`` carry exactly the text the CSV labels used.

    Ingest stores these URLs too, so a gold row can predate the load and ``ON CONFLICT DO
    NOTHING`` will have kept the older text. Every label offset was computed against the
    CSV, so text that disagrees would pin the labels to the wrong span — and unlike a
    mistyped quote, nothing downstream would notice.
    """
    problems = []
    for article in articles:
        row = stored[article.slug]
        if row["title"] != article.title:
            problems.append(
                f"{article.slug}: stored title {row['title']!r} != CSV {article.title!r}"
            )
        if (row["lead"] or "") != article.lead:
            problems.append(f"{article.slug}: stored lead differs from the CSV")
    if problems:
        raise GoldError("stored articles disagree with the gold CSV:\n  " + "\n  ".join(problems))


async def _load(
    connection_pool: AsyncConnectionPool,
    articles: list[GoldArticle],
    labels: list[GoldLabel],
    split: str,
) -> None:
    """Replace one split of the stored gold set with what its CSVs say.

    Only the gold tables are rebuilt. ``articles`` is deliberately left alone: ``analyses``
    references it with ON DELETE CASCADE, so clearing it would take every stored evaluation
    run along with it. Re-annotation changes labels, not article text, so the answers models
    already gave stay valid and are simply rescored against the corrected labels.

    Scoped to one split, so reloading the main set cannot silently drop the holdout — which
    would turn a held-out measurement back into a tuned one without anything failing.
    """
    await execute(
        connection_pool,
        """
        DELETE FROM gold_labels
        WHERE article_id IN (SELECT article_id FROM gold_articles WHERE split = %s)
        """,
        (split,),
    )
    await execute(connection_pool, "DELETE FROM gold_articles WHERE split = %s", (split,))

    portals = sorted({article.portal for article in articles})
    await execute_many(
        connection_pool,
        """
        INSERT INTO sources (name, base_url, strategy)
        VALUES (%s, %s, 'static')
        ON CONFLICT (name) DO NOTHING
        """,
        [(portal, f"https://{portal.lower().replace(' ', '')}.pl/") for portal in portals],
    )

    source_ids = {
        row["name"]: row["id"]
        for row in await fetch_all(connection_pool, "SELECT id, name FROM sources")
    }

    await execute_many(
        connection_pool,
        """
        INSERT INTO articles
            (source_id, url, url_hash, title, lead, fetch_level, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (url_hash) DO NOTHING
        """,
        [
            (
                source_ids[article.portal],
                article.url,
                digest(canonical_url(article.url)),
                article.title,
                article.lead or None,
                RSS_FETCH_LEVEL,
                digest(article.title, article.lead),
            )
            for article in articles
        ],
    )

    stored = {
        row["url_hash"]: row
        for row in await fetch_all(
            connection_pool, "SELECT id, url_hash, title, lead FROM articles"
        )
    }
    rows_by_slug = {
        article.slug: stored[digest(canonical_url(article.url))] for article in articles
    }
    check_stored_text(articles, rows_by_slug)
    ids_by_slug = {slug: row["id"] for slug, row in rows_by_slug.items()}

    await execute_many(
        connection_pool,
        """
        INSERT INTO gold_articles
            (article_id, expected_category, expected_assessment, kind, labeled_by, split)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        [
            (
                ids_by_slug[article.slug],
                article.expected_category,
                article.expected_assessment,
                article.kind,
                ANNOTATOR,
                split,
            )
            for article in articles
        ],
    )

    await execute_many(
        connection_pool,
        """
        INSERT INTO gold_labels
            (article_id, type, quote, field, quote_start, quote_end, note, labeled_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                ids_by_slug[label.slug],
                label.type,
                label.quote,
                label.field,
                label.start,
                label.end,
                label.note,
                ANNOTATOR,
            )
            for label in labels
        ],
    )


async def _run(args: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings.from_env()

    set_path, labels_path = csv_paths(args.split)
    articles = read_articles(set_path)
    labels = read_labels(articles, labels_path)
    check_consistency(articles, labels)

    kinds: dict[str, int] = {}
    for article in articles:
        kinds[article.kind] = kinds.get(article.kind, 0) + 1
    print(f"{len(articles)} articles, {len(labels)} labels, all quotes verified verbatim")
    print("  " + "  ".join(f"{kind}={count}" for kind, count in sorted(kinds.items())))

    if args.check_only:
        return 0

    async with pool(settings.database_url) as connection_pool:
        await _load(connection_pool, articles, labels, args.split)
        stored = await fetch_all(
            connection_pool,
            """
            SELECT split, kind, count(*) AS n FROM gold_articles
            GROUP BY split, kind ORDER BY split, kind
            """,
        )
    print("stored: " + "  ".join(f"{row['split']}/{row['kind']}={row['n']}" for row in stored))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split",
        choices=SPLITS,
        default="main",
        help="which reference set to load; holdout reads eval/holdout_*.csv",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate the CSVs without touching the database",
    )
    try:
        return asyncio.run(_run(parser.parse_args()))
    except GoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

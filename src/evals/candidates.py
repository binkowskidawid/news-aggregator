"""Pick the articles the next gold set will be annotated from.

    make gold-candidates                        # all flagged + 25 neutral
    make gold-candidates ARGS="--neutral 40"

Writes ``eval/gold_candidates.csv``, a working file for the annotator. It is not loaded
into the database and it is not ``eval/gold_set.csv`` — ``kind`` and the two ``expected_``
columns are judgements, and a selector that guessed them would be labelling its own
input.

Two properties of the sample matter more than its size:

**Flagged articles are taken whole, neutral ones are sampled.** Precision conditions on
the model's positives, so the width of its confidence interval is set by how many findings
the set contains, not how many articles. Neutral articles are still needed — without them
false negatives are invisible and recall is unmeasurable — but they are cheap to be
selective about. The consequence has to be carried into the report: recall computed on
this set is conditioned on a stratified sample, and the sampling fractions belong next to
the number.

**The sample is spread across days and portals, not taken off the head of the list.** A
single day and a single running story can dominate the corpus — in the set this was built
against, one day supplied 173 of 240 articles — so a prefix of the queue would sample that
story rather than the press.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import unicodedata
from pathlib import Path
from typing import Any, Final

from config import Settings, load_dotenv
from db import fetch_all, pool

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
CANDIDATES_PATH: Final = REPO_ROOT / "eval" / "gold_candidates.csv"

COLUMNS: Final = (
    "slug",
    "portal",
    "url",
    "title",
    "lead",
    "published",
    "model_assessment",
    "model_findings",
)

SLUG_WORDS: Final = 3
SLUG_MIN_WORD: Final = 3
"""Below this a word is a preposition or a conjunction and carries no identity."""

# ł and Ł have no canonical decomposition, so NFKD leaves them behind and the ASCII
# encode below would drop them silently — "Głos" would slug as "gos".
STROKED: Final = str.maketrans({"ł": "l", "Ł": "L"})

CANDIDATE_QUERY: Final = """
    WITH latest AS (
        SELECT DISTINCT ON (an.article_id)
               an.id, an.article_id, an.overall_assessment
        FROM analyses an
        JOIN articles art ON art.id = an.article_id
        WHERE an.parse_error IS NULL
          AND coalesce(art.published_at, art.fetched_at) >= %s
          AND NOT EXISTS (SELECT 1 FROM gold_articles g WHERE g.article_id = an.article_id)
        ORDER BY an.article_id, an.created_at DESC
    )
    SELECT
        s.name AS portal,
        a.url,
        a.title,
        coalesce(a.lead, '') AS lead,
        coalesce(a.published_at, a.fetched_at) AS published,
        l.overall_assessment AS model_assessment,
        coalesce(
            (SELECT string_agg(f.type, ' ' ORDER BY f.type)
             FROM findings f WHERE f.analysis_id = l.id),
            ''
        ) AS model_findings,
        l.overall_assessment <> 'neutral' AS flagged,
        row_number() OVER (
            PARTITION BY l.overall_assessment <> 'neutral',
                         date(coalesce(a.published_at, a.fetched_at)),
                         s.name
            ORDER BY random()
        ) AS cell_rank
    FROM latest l
    JOIN articles a ON a.id = l.article_id
    JOIN sources s ON s.id = a.source_id
    ORDER BY cell_rank, s.name, a.id
"""
"""One row per article, ranked inside its (flagged, day, portal) cell.

``DISTINCT ON`` keeps the most recent analysis: an article re-analysed after a prompt
change must not appear twice, and the current verdict is the one being sampled around.
Ordering the whole result by ``cell_rank`` first is what makes a prefix a stratified
sample — every cell gives up its first article before any cell gives up its second.
"""


def slugify(title: str, taken: set[str]) -> str:
    """A short, unique, ASCII handle for one article."""
    folded = unicodedata.normalize("NFKD", title.translate(STROKED))
    ascii_only = folded.encode("ascii", "ignore").decode()
    words = [word for word in "".join(c if c.isalnum() else " " for c in ascii_only).split()]
    chosen = [word.lower() for word in words if len(word) >= SLUG_MIN_WORD][:SLUG_WORDS]
    base = "-".join(chosen) or "artykul"

    slug, suffix = base, 2
    while slug in taken:
        slug, suffix = f"{base}-{suffix}", suffix + 1
    taken.add(slug)
    return slug


def select(
    rows: list[dict[str, Any]], *, flagged: int | None, neutral: int
) -> list[dict[str, Any]]:
    """Split the ranked rows into the two strata and cut each to size.

    The rows arrive ordered by ``cell_rank``, so slicing a prefix takes one article from
    every day-and-portal cell before it takes a second from any of them.
    """
    flagged_rows = [row for row in rows if row["flagged"]]
    neutral_rows = [row for row in rows if not row["flagged"]]
    return flagged_rows[:flagged] + neutral_rows[:neutral]


def write_candidates(rows: list[dict[str, Any]]) -> None:
    taken: set[str] = set()
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CANDIDATES_PATH.open("w", encoding="utf-8", newline="") as handle:
        # The gold CSVs next to this one are LF; csv defaults to CRLF and would make every
        # regeneration a whole-file diff.
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    slugify(row["title"], taken),
                    row["portal"],
                    row["url"],
                    row["title"],
                    row["lead"],
                    row["published"].date().isoformat(),
                    row["model_assessment"],
                    row["model_findings"],
                ]
            )


def summarise(rows: list[dict[str, Any]]) -> str:
    """The spread that decides whether the sample is of the press or of one story."""
    by_day: dict[str, int] = {}
    by_portal: dict[str, int] = {}
    for row in rows:
        day = row["published"].date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
        by_portal[row["portal"]] = by_portal.get(row["portal"], 0) + 1
    return (
        "  days:    " + "  ".join(f"{k}={v}" for k, v in sorted(by_day.items())) + "\n"
        "  portals: " + "  ".join(f"{k}={v}" for k, v in sorted(by_portal.items()))
    )


async def _run(args: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings.from_env()

    async with pool(settings.database_url) as connection_pool:
        rows = await fetch_all(connection_pool, CANDIDATE_QUERY, (args.since,))

    if not rows:
        print("no analysed articles outside the gold set; run `make analyze` first")
        return 2

    chosen = select(rows, flagged=args.flagged, neutral=args.neutral)
    write_candidates(chosen)

    flagged_count = sum(1 for row in chosen if row["flagged"])
    print(
        f"{len(chosen)} candidate(s) → {CANDIDATES_PATH.relative_to(REPO_ROOT)}\n"
        f"  flagged: {flagged_count} of {sum(1 for row in rows if row['flagged'])} available\n"
        f"  neutral: {len(chosen) - flagged_count} of "
        f"{sum(1 for row in rows if not row['flagged'])} available\n" + summarise(chosen)
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flagged", type=int, default=None, help="cap on flagged articles")
    parser.add_argument("--neutral", type=int, default=25, help="how many neutral articles")
    parser.add_argument(
        "--since",
        default="1970-01-01",
        help=(
            "earliest publication date to sample from (YYYY-MM-DD). A held-out set needs "
            "days the tuned set never drew on, and the corpus is dominated by two of them"
        ),
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

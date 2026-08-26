"""Turn stored analyses into the numbers the go/no-go decision cites.

Reads only; every value here is derived from ``analyses``, ``findings`` and the gold
tables. Nothing is cached, because a metric that disagrees with the database is worse
than no metric.

Two choices in here shape every number downstream, so both are stated rather than buried:

1. **What counts as a match** — see :func:`matches`. A model and an annotator routinely
   disagree by a preposition about where a phrase starts, and demanding identical offsets
   would depress precision for a reason unrelated to analytical quality.
2. **What counts as reachable** — a run shown only the headline cannot find a technique
   that lives in the lead. Those labels leave the recall denominator, otherwise the
   comparison between input variants measures our harness instead of the models.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Final

from psycopg_pool import AsyncConnectionPool

from db import fetch_all
from domain.analysis import CONFIDENT

WILSON_Z: Final = 1.96
"""95% two-sided normal quantile, for the Wilson score interval."""


def wilson_interval(successes: int, total: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Confidence interval for a proportion, valid at small sample sizes.

    The gold set holds 25 articles and fewer than a hundred label decisions. A naive
    normal interval misbehaves badly there and would understate how much of the result is
    sampling noise — which matters, because these numbers are published as the accuracy of
    a tool that names techniques in someone's headline.
    """
    if total == 0:
        return (0.0, 0.0)
    proportion = successes / total
    denominator = 1 + z**2 / total
    centre = (proportion + z**2 / (2 * total)) / denominator
    margin = (
        z * math.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2)) / denominator
    )
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def matches(finding: dict[str, Any], label: dict[str, Any]) -> bool:
    """Whether a model finding and a reference label describe the same thing.

    The rule is: same technique, same field, and spans that overlap by at least one
    character.

    Alternatives considered and rejected. *Identical offsets* punishes "Szokujące
    szczegóły" against "Szokujące szczegóły wypadku na S7", which is the same detection.
    *Ignoring the span entirely* would let a model score a hit by naming the right
    technique against the wrong sentence — and the product highlights the quote in the
    interface, so pointing at the wrong words is a real defect, not a rounding error.
    Overlap sits between the two and is the convention in span-extraction evaluation.
    """
    return overlaps(finding, label) and bool(finding["type"] == label["type"])


def overlaps(finding: dict[str, Any], label: dict[str, Any]) -> bool:
    """Whether the two spans cover the same words, whatever they are called.

    Separated from :func:`matches` because the two answer different questions. A finding
    that lands on a genuinely loaded phrase but calls it ``overgeneralization`` where the
    annotator wrote ``emotional_load`` is a disagreement about the taxonomy; a finding
    that lands on an innocent sentence is an accusation against a named outlet. The
    interface highlights the quote, so the first is a labelling quibble the reader may not
    even notice and the second is the risk this project exists to bound.
    """
    return bool(
        finding["field"] == label["field"]
        and finding["quote_start"] < label["quote_end"]
        and label["quote_start"] < finding["quote_end"]
    )


@dataclass(frozen=True, slots=True)
class Scores:
    """Everything measured for one configuration."""

    run_id: Any
    model_name: str
    provider: str
    input_variant: str
    grammar_mode: str
    source_label: str | None
    prompt_version: str
    """Which prompt text produced these answers.

    Not part of the grouping key — one run uses one version, so ``run_id`` already
    separates them — but the report needs it in the row label. Three versions are in the
    database and a bare run hash does not say which is which.
    """

    split: str
    """Which reference set this row was scored against.

    A run covers one split — ``run_eval`` enforces it — but the report puts every row in
    one table, and a held-out number that reads like a tuned one is worse than no number
    at all.
    """

    calls: int
    unique_articles: int
    """Distinct articles behind ``calls``.

    Reported next to the call count because a stability probe and a main sweep can both
    say "25 calls" while one of them is five articles seen five times. Every interval in
    this module assumes independent observations, so a row where these two numbers differ
    carries a narrower confidence interval than it has earned.
    """

    parse_failures: int
    consistency_errors: int
    """Analyses whose verdict contradicts the findings stored beside it.

    Reported next to the quality metrics rather than folded into them: the row is a
    correct analysis by every other measure, and what it says about the model is that it
    does not apply the rule the prompt gave it.
    """

    quotes_total: int
    quotes_rejected: int
    quotes_fuzzy: int

    true_positives: int
    false_positives: int
    false_negatives: int
    true_positives_confident: int
    false_positives_confident: int
    span_hits: int
    """Findings landing on a span the annotator also marked, whatever they were called."""

    category_hits: int
    category_total: int
    assessment_hits: int

    neutral_articles: int
    neutral_with_findings: int
    neutral_false_findings: int

    empty_findings: int
    latencies: tuple[int, ...] = field(default_factory=tuple)
    tokens_in: tuple[int, ...] = field(default_factory=tuple)
    tokens_out: tuple[int, ...] = field(default_factory=tuple)

    @property
    def json_validity(self) -> float:
        return 1.0 - self.parse_failures / self.calls if self.calls else 0.0

    @property
    def quote_fidelity(self) -> float | None:
        """Share of quotes that were actually present in the source text.

        The one decision metric that needs no reference annotations, which is why it was
        available from the first run and cannot be argued about.
        """
        if self.quotes_total == 0:
            return None
        return 1.0 - self.quotes_rejected / self.quotes_total

    @property
    def precision(self) -> float | None:
        found = self.true_positives + self.false_positives
        return self.true_positives / found if found else None

    @property
    def precision_confident(self) -> float | None:
        found = self.true_positives_confident + self.false_positives_confident
        return self.true_positives_confident / found if found else None

    @property
    def recall(self) -> float | None:
        reachable = self.true_positives + self.false_negatives
        return self.true_positives / reachable if reachable else None

    @property
    def precision_span(self) -> float | None:
        """Share of findings that pointed at words the annotator also marked.

        Read alongside :attr:`precision`: the gap between them is disagreement about which
        technique a phrase illustrates, not about whether the phrase is loaded at all.
        """
        found = self.true_positives + self.false_positives
        return self.span_hits / found if found else None

    @property
    def precision_interval(self) -> tuple[float, float]:
        return wilson_interval(self.true_positives, self.true_positives + self.false_positives)

    @property
    def category_accuracy(self) -> float | None:
        return self.category_hits / self.category_total if self.category_total else None

    @property
    def assessment_accuracy(self) -> float | None:
        return self.assessment_hits / self.category_total if self.category_total else None

    @property
    def neutral_fp_rate(self) -> float | None:
        """Share of purely informational articles that drew at least one accusation.

        Weighted above recall throughout this project: a missed technique costs a reader
        one story, an invented one costs the operator a correction and possibly a lawyer.
        """
        if self.neutral_articles == 0:
            return None
        return self.neutral_with_findings / self.neutral_articles

    @property
    def empty_rate(self) -> float:
        return self.empty_findings / self.calls if self.calls else 0.0

    def percentile(self, values: tuple[int, ...], fraction: float) -> int | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, int(fraction * len(ordered)))
        return ordered[index]

    @property
    def median_latency_ms(self) -> int | None:
        return int(statistics.median(self.latencies)) if self.latencies else None

    @property
    def p95_latency_ms(self) -> int | None:
        return self.percentile(self.latencies, 0.95)

    @property
    def median_tokens_in(self) -> int | None:
        return int(statistics.median(self.tokens_in)) if self.tokens_in else None

    @property
    def median_tokens_out(self) -> int | None:
        return int(statistics.median(self.tokens_out)) if self.tokens_out else None


async def load_rows(connection_pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    """One row per analysis, with its gold expectations attached."""
    return await fetch_all(
        connection_pool,
        """
        SELECT
            an.id, an.run_id, an.provider, an.model_name, an.input_variant, an.grammar_mode,
            an.source_label, an.prompt_version, an.article_id, an.category, an.overall_assessment,
            an.parse_error, an.consistency_error, an.latency_ms, an.tokens_in, an.tokens_out,
            an.quotes_total, an.quotes_rejected, an.quotes_fuzzy,
            g.expected_category, g.expected_assessment, g.kind, g.split,
            -- Whether this article could have been shown a lead at all. A `title_lead`
            -- sweep silently skips the ones without it, so the two variants are scored
            -- over different article sets unless something restricts them to this subset.
            (a.lead IS NOT NULL AND length(trim(a.lead)) > 0) AS has_lead
        FROM analyses an
        JOIN gold_articles g ON g.article_id = an.article_id
        JOIN articles a ON a.id = an.article_id
        ORDER BY an.model_name, an.input_variant, an.grammar_mode, an.id
        """,
    )


async def load_findings(connection_pool: AsyncConnectionPool) -> dict[Any, list[dict[str, Any]]]:
    """Findings grouped by analysis, fetched in one query rather than one per analysis."""
    rows = await fetch_all(
        connection_pool,
        """
        SELECT analysis_id, type, field, quote_start, quote_end, confidence
        FROM findings
        """,
    )
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["analysis_id"], []).append(row)
    return grouped


async def load_gold_labels(connection_pool: AsyncConnectionPool) -> dict[Any, list[dict[str, Any]]]:
    rows = await fetch_all(
        connection_pool,
        "SELECT article_id, type, field, quote_start, quote_end FROM gold_labels",
    )
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["article_id"], []).append(row)
    return grouped


def _visible_fields(input_variant: str) -> set[str]:
    """Which article fields the model was actually shown."""
    return {"title"} if input_variant == "title" else {"title", "lead"}


def score_group(
    rows: list[dict[str, Any]],
    findings_by_analysis: dict[Any, list[dict[str, Any]]],
    labels_by_article: dict[Any, list[dict[str, Any]]],
) -> Scores:
    """Compute every metric for one configuration."""
    first = rows[0]
    visible = _visible_fields(first["input_variant"])

    true_positives = false_positives = false_negatives = 0
    span_hits = 0
    tp_confident = fp_confident = 0
    category_hits = assessment_hits = category_total = 0
    neutral_articles = neutral_with_findings = neutral_false_findings = 0
    empty_findings = 0
    quotes_total = quotes_rejected = quotes_fuzzy = 0
    parse_failures = consistency_errors = 0
    latencies: list[int] = []
    tokens_in: list[int] = []
    tokens_out: list[int] = []

    for row in rows:
        if row["latency_ms"] is not None:
            latencies.append(row["latency_ms"])
        if row["tokens_in"] is not None:
            tokens_in.append(row["tokens_in"])
        if row["tokens_out"] is not None:
            tokens_out.append(row["tokens_out"])

        quotes_total += row["quotes_total"]
        quotes_rejected += row["quotes_rejected"]
        quotes_fuzzy += row["quotes_fuzzy"]

        if row["parse_error"] is not None:
            parse_failures += 1
            continue
        if row["consistency_error"] is not None:
            consistency_errors += 1

        category_total += 1
        if row["category"] == row["expected_category"]:
            category_hits += 1
        if row["overall_assessment"] == row["expected_assessment"]:
            assessment_hits += 1

        found = findings_by_analysis.get(row["id"], [])
        if not found:
            empty_findings += 1

        # Only labels in fields the model was shown can be found, so only those belong in
        # the recall denominator.
        expected = [
            label
            for label in labels_by_article.get(row["article_id"], [])
            if label["field"] in visible
        ]

        matched_labels: set[int] = set()
        for finding in found:
            if any(overlaps(finding, candidate) for candidate in expected):
                span_hits += 1
            hit = next(
                (
                    index
                    for index, label in enumerate(expected)
                    if index not in matched_labels and matches(finding, label)
                ),
                None,
            )
            confident = (finding["confidence"] or 0) >= CONFIDENT
            if hit is None:
                false_positives += 1
                if confident:
                    fp_confident += 1
            else:
                matched_labels.add(hit)
                true_positives += 1
                if confident:
                    tp_confident += 1
        false_negatives += len(expected) - len(matched_labels)

        if row["kind"] == "neutral":
            neutral_articles += 1
            if found:
                neutral_with_findings += 1
                neutral_false_findings += len(found)

    return Scores(
        run_id=first["run_id"],
        model_name=first["model_name"],
        provider=first["provider"],
        input_variant=first["input_variant"],
        grammar_mode=first["grammar_mode"],
        source_label=first["source_label"],
        prompt_version=first["prompt_version"],
        split=first["split"],
        calls=len(rows),
        unique_articles=len({row["article_id"] for row in rows}),
        parse_failures=parse_failures,
        consistency_errors=consistency_errors,
        quotes_total=quotes_total,
        quotes_rejected=quotes_rejected,
        quotes_fuzzy=quotes_fuzzy,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        true_positives_confident=tp_confident,
        false_positives_confident=fp_confident,
        span_hits=span_hits,
        category_hits=category_hits,
        category_total=category_total,
        assessment_hits=assessment_hits,
        neutral_articles=neutral_articles,
        neutral_with_findings=neutral_with_findings,
        neutral_false_findings=neutral_false_findings,
        empty_findings=empty_findings,
        latencies=tuple(latencies),
        tokens_in=tuple(tokens_in),
        tokens_out=tuple(tokens_out),
    )


async def score_all(
    connection_pool: AsyncConnectionPool, *, comparable_only: bool = False
) -> list[Scores]:
    """Score every stored configuration, one entry per distinct combination.

    ``comparable_only`` drops articles that have no lead. Without it, comparing the two
    input variants compares two different article sets: a ``title`` sweep covers the whole
    gold set, a ``title_lead`` sweep covers only the articles that expose a lead, and the
    18 that do not are exactly the ones from a portal scraped off a listing page. The
    difference in the numbers then carries the difference in the samples, which is not the
    question anyone is asking of it.
    """
    rows = await load_rows(connection_pool)
    if comparable_only:
        rows = [row for row in rows if row["has_lead"]]
    findings_by_analysis = await load_findings(connection_pool)
    labels_by_article = await load_gold_labels(connection_pool)

    # run_id is part of the key on purpose. A stability probe repeats five articles five
    # times each; merged into the main sweep it would weight those five articles eightfold
    # in every precision and recall figure. The schema calls run_id "one sweep" for
    # exactly this reason.
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            row["run_id"],
            row["model_name"],
            row["input_variant"],
            row["grammar_mode"],
            row["source_label"],
        )
        groups.setdefault(key, []).append(row)

    return [
        score_group(group, findings_by_analysis, labels_by_article)
        for group in groups.values()
        if group
    ]


async def stability(connection_pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    """Spread of finding counts across repeated calls on the same article.

    Temperature 0.1 is not determinism. Reporting the spread is what separates "the model
    decided this article is loaded" from "the model happened to say so once".
    """
    return await fetch_all(
        connection_pool,
        """
        WITH per_call AS (
            SELECT an.run_id, an.model_name, an.input_variant, an.grammar_mode,
                   an.article_id, an.id, count(f.id) AS findings, an.overall_assessment
            FROM analyses an
            -- Evaluation metrics read the evaluation set and nothing else. The production
            -- pass writes to the same table, and while it happens to be filtered out here
            -- by the HAVING below, relying on that would make this query correct by
            -- accident rather than by construction.
            JOIN gold_articles g ON g.article_id = an.article_id
            LEFT JOIN findings f ON f.analysis_id = an.id
            WHERE an.source_label IS NULL AND an.parse_error IS NULL
            GROUP BY an.id, an.run_id, an.model_name, an.input_variant, an.grammar_mode,
                     an.article_id, an.overall_assessment
        ), repeated AS (
            -- The configuration has to be part of the key. Without it the main sweep's
            -- four passes over each article (two input variants x two grammar modes) look
            -- like repeated calls, and the "spread" reported would be the difference
            -- between reading a headline and reading a headline plus a lead — a real
            -- effect, but not the one this metric exists to measure.
            SELECT run_id, model_name, input_variant, grammar_mode, article_id,
                   count(*) AS calls,
                   min(findings) AS min_findings, max(findings) AS max_findings,
                   count(DISTINCT overall_assessment) AS distinct_assessments
            FROM per_call
            GROUP BY run_id, model_name, input_variant, grammar_mode, article_id
            HAVING count(*) > 1
        )
        SELECT model_name, input_variant, grammar_mode,
               count(*) AS articles,
               max(calls) AS repeats,
               round(avg(max_findings - min_findings), 2) AS avg_spread,
               max(max_findings - min_findings) AS worst_spread,
               count(*) FILTER (WHERE distinct_assessments > 1) AS assessment_flips
        FROM repeated GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """,
    )


async def brand_bias(connection_pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    """Findings per article by injected outlet name, on identical text.

    The articles are the same in every row; only the label in the prompt differs. A gap
    here is the model applying different thresholds to the same language depending on who
    it believes published it — which would make the product worse than useless on a source
    list spanning opposing political profiles.
    """
    return await fetch_all(
        connection_pool,
        """
        WITH per_call AS (
            -- One row per analysis first: joining findings before aggregating would
            -- multiply the call count by the number of findings on each call.
            SELECT an.run_id, an.model_name, an.article_id,
                   coalesce(an.source_label, '(brak etykiety)') AS label,
                   count(f.id) AS findings,
                   an.overall_assessment
            FROM analyses an
            -- As in stability(): the evaluation set only. This join is the whole defence
            -- now that production also writes `title_lead` rows — an unlabelled row
            -- carrying hundreds of articles would otherwise sit in the comparison next to
            -- a probe of twenty-five.
            JOIN gold_articles g ON g.article_id = an.article_id
            LEFT JOIN findings f ON f.analysis_id = an.id
            WHERE an.input_variant = 'title_lead' AND an.grammar_mode = 'schema'
              AND an.parse_error IS NULL
            GROUP BY an.id, an.run_id, an.model_name, an.article_id, an.source_label,
                     an.overall_assessment
        )
        -- run_id stays in the key: the stability probe shares this configuration and a
        -- NULL label, so merging it into the unlabelled baseline would compare a sample
        -- of five repeated articles against a sample of twenty-five distinct ones.
        SELECT run_id, model_name, label,
               count(*) AS calls,
               count(DISTINCT article_id) AS articles,
               sum(findings) AS findings,
               round(sum(findings)::numeric / nullif(count(*), 0), 2) AS per_article,
               count(*) FILTER (WHERE overall_assessment = 'heavily_loaded') AS heavily
        FROM per_call
        GROUP BY 1, 2, 3 ORDER BY 2, 3
        """,
    )

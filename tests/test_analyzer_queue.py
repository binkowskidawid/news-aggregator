"""Tests for the analysis queue's retry policy.

The failure mode being guarded against is not a wrong number in a report: it is the
analyzer running unattended overnight while Ollama is down, charging every article an
attempt on every pass, and retiring the whole corpus to ``failed_permanent``. Nothing
would raise, and the only evidence next morning would be an empty queue.
"""

from __future__ import annotations

import uuid

from analyzer.analyze import Analysis
from analyzer.prompts import Prompt
from analyzer.providers.base import Completion
from analyzer.store import CONSECUTIVE_FAILURE_LIMIT, FailureTracker, outcome
from domain.analysis import Assessment, Category


def _analysis(*, parse_error: str | None = None, category: Category | None = None) -> Analysis:
    return Analysis(
        completion=Completion(
            content="{}", model="gemma4:latest", latency_ms=1, tokens_in=1, tokens_out=1
        ),
        prompt=Prompt(messages=[], nonce="abcd1234", version="v1.1.0", input_variant="title"),
        grammar_mode="schema",
        category=category,
        category_confidence=0.9 if category else None,
        overall_assessment=None if parse_error else Assessment.MILDLY_LOADED,
        findings=(),
        rejected_quotes=(),
        parse_error=parse_error,
    )


def test_parsed_analysis_leaves_the_queue_with_its_category() -> None:
    assert outcome(_analysis(category=Category.POLITYKA)) == ("analyzed", "polityka")


def test_unparseable_analysis_is_terminal_and_carries_no_category() -> None:
    """`failed`, not `pending`: under a grammar this is a configuration fault, and
    retrying it would hide the setting that needs changing."""
    assert outcome(_analysis(parse_error="expecting ',' delimiter")) == ("failed", None)


def test_parsed_analysis_without_a_category_still_leaves_the_queue() -> None:
    assert outcome(_analysis()) == ("analyzed", None)


def test_scattered_failures_are_charged_to_their_articles() -> None:
    """Failures broken up by a success are about the articles, not the backend."""
    tracker = FailureTracker()
    first, second = uuid.uuid4(), uuid.uuid4()

    assert tracker.record_failure(first) is False
    tracker.record_success()
    assert tracker.record_failure(second) is False

    assert tracker.article_ids == [first, second]


def test_an_unbroken_run_of_failures_charges_nobody() -> None:
    """The regression that matters: a backend outage must not consume the corpus.

    The last call reports that the pass should abort, and the charges collected on the
    way there are dropped — those articles were never given a chance to fail on merit.
    """
    tracker = FailureTracker()
    article_ids = [uuid.uuid4() for _ in range(CONSECUTIVE_FAILURE_LIMIT)]

    aborts = [tracker.record_failure(article_id) for article_id in article_ids]

    assert aborts == [False] * (CONSECUTIVE_FAILURE_LIMIT - 1) + [True]
    assert tracker.article_ids == []


def test_a_success_resets_the_run_towards_the_limit() -> None:
    """Otherwise a long queue with sporadic failures would eventually trip the breaker
    and stop analysing for reasons that have nothing to do with the backend."""
    tracker = FailureTracker()

    for _ in range(CONSECUTIVE_FAILURE_LIMIT - 1):
        assert tracker.record_failure(uuid.uuid4()) is False
    tracker.record_success()

    assert tracker.record_failure(uuid.uuid4()) is False

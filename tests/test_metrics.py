"""Tests for the scoring rules.

The matching rule decides every precision and recall number in the report, and those
numbers are published. Its boundary cases are therefore pinned here rather than left to
whatever the implementation happens to do.
"""

from __future__ import annotations

from typing import Any

import pytest

from evals.metrics import matches, overlaps, wilson_interval


def finding(
    type_: str = "emotional_load",
    field: str = "title",
    start: int = 10,
    end: int = 20,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "type": type_,
        "field": field,
        "quote_start": start,
        "quote_end": end,
        "confidence": 0.8,
        **extra,
    }


def label(
    type_: str = "emotional_load", field: str = "title", start: int = 10, end: int = 20
) -> dict[str, Any]:
    return {"type": type_, "field": field, "quote_start": start, "quote_end": end}


class TestMatches:
    def test_identical_spans_match(self) -> None:
        assert matches(finding(), label())

    def test_different_technique_never_matches(self) -> None:
        assert not matches(finding(type_="fear_appeal"), label(type_="emotional_load"))

    def test_different_field_never_matches(self) -> None:
        """The same offsets in title and lead point at unrelated text."""
        assert not matches(finding(field="lead"), label(field="title"))

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (10, 40, True),  # model quoted a superset of the reference
            (12, 15, True),  # model quoted a subset
            (0, 11, True),  # overlap of one character at the left edge
            (19, 30, True),  # overlap of one character at the right edge
            (0, 10, False),  # ends exactly where the label starts: adjacent, not overlapping
            (20, 30, False),  # starts exactly where the label ends
            (0, 5, False),  # disjoint before
            (30, 40, False),  # disjoint after
        ],
    )
    def test_overlap_boundaries(self, start: int, end: int, expected: bool) -> None:
        assert matches(finding(start=start, end=end), label(start=10, end=20)) is expected

    def test_half_open_intervals_do_not_touch(self) -> None:
        """Spans are half-open, as Python slices are.

        A quote ending at index 10 and one starting at index 10 share no character, so
        counting them as the same detection would credit a model for pointing next to the
        phrase rather than at it.
        """
        assert not matches(finding(start=0, end=10), label(start=10, end=20))


class TestOverlaps:
    """`overlaps` answers "did the model point at the right words", ignoring the label."""

    def test_same_span_different_technique_still_overlaps(self) -> None:
        """The case that motivated the metric: right phrase, disputed taxonomy."""
        assert overlaps(finding(type_="overgeneralization"), label(type_="emotional_load"))
        assert not matches(finding(type_="overgeneralization"), label(type_="emotional_load"))

    def test_field_still_has_to_agree(self) -> None:
        assert not overlaps(finding(field="lead"), label(field="title"))

    def test_disjoint_spans_do_not_overlap(self) -> None:
        assert not overlaps(finding(start=0, end=5), label(start=10, end=20))

    def test_matches_implies_overlaps(self) -> None:
        assert matches(finding(start=12, end=15), label(start=10, end=20))
        assert overlaps(finding(start=12, end=15), label(start=10, end=20))


class TestWilsonInterval:
    def test_empty_sample_is_not_a_division_by_zero(self) -> None:
        assert wilson_interval(0, 0) == (0.0, 0.0)

    def test_interval_brackets_the_estimate(self) -> None:
        low, high = wilson_interval(7, 10)
        assert low < 0.7 < high

    def test_interval_stays_inside_zero_one(self) -> None:
        low, high = wilson_interval(10, 10)
        assert low >= 0.0
        assert high <= 1.0

    def test_smaller_sample_gives_wider_interval(self) -> None:
        """The reason the interval is reported at all: 25 articles is a small sample."""
        narrow_low, narrow_high = wilson_interval(70, 100)
        wide_low, wide_high = wilson_interval(7, 10)
        assert (wide_high - wide_low) > (narrow_high - narrow_low)

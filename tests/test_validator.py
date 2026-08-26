"""Tests for quote verification.

The offset assertions all slice the *original* source string with the returned span and
compare the result to the expected text. That is the property the UI depends on, and it
is the one a normalisation change is most likely to break silently.
"""

from __future__ import annotations

import pytest

from analyzer.validator import (
    FUZZY_THRESHOLD,
    locate_quote,
    paraphrase_similarity,
    to_canonical,
)

LEAD = (
    "Minimalna emerytura w 2027 roku wzrośnie, ale era rekordowych podwyżek "
    "jest już za nami. Prognozy nie powalają."
)


def _slice(source: str, quote: str) -> str:
    match = locate_quote(quote, source)
    assert match is not None, f"quote not located: {quote!r}"
    return source[match.start : match.end]


class TestVerbatimMatch:
    def test_locates_exact_quote_and_reports_verbatim(self) -> None:
        match = locate_quote("Prognozy nie powalają", LEAD)

        assert match is not None
        assert match.fuzzy is False
        assert match.score == 100.0
        assert LEAD[match.start : match.end] == "Prognozy nie powalają"

    def test_quote_at_the_very_start(self) -> None:
        assert _slice(LEAD, "Minimalna emerytura") == "Minimalna emerytura"

    def test_quote_at_the_very_end(self) -> None:
        assert _slice(LEAD, "nie powalają.") == "nie powalają."

    def test_absent_quote_is_rejected(self) -> None:
        assert locate_quote("emeryci wyjdą na ulice", LEAD) is None

    @pytest.mark.parametrize("quote", ["", "   ", "\n\t "])
    def test_blank_quote_is_rejected(self, quote: str) -> None:
        assert locate_quote(quote, LEAD) is None

    def test_empty_source_is_rejected(self) -> None:
        assert locate_quote("cokolwiek", "") is None


class TestTypographyFolding:
    """Models routinely rewrite punctuation when quoting. Folding both sides absorbs it
    without letting a genuine paraphrase through."""

    def test_polish_opening_quote_folds_to_ascii(self) -> None:
        source = 'Rzecznik powiedział: „to koniec sprawy" i wyszedł.'

        assert _slice(source, '"to koniec sprawy"') == '„to koniec sprawy"'

    def test_em_dash_folds_to_hyphen(self) -> None:
        source = "Wzrost cen — zdaniem analityków — wyhamuje."

        assert _slice(source, "Wzrost cen - zdaniem analityków") == (
            "Wzrost cen — zdaniem analityków"
        )

    def test_ellipsis_expands_to_three_dots_without_shifting_offsets(self) -> None:
        # One source character becomes three folded ones. If the offset map were naive,
        # every span after this point would drift by two.
        source = "Zapowiedź brzmiała… a potem zapadła cisza wyborcza."

        assert _slice(source, "a potem zapadła cisza") == "a potem zapadła cisza"

    def test_collapsed_whitespace_does_not_shift_offsets(self) -> None:
        source = "Rząd    przyjął\n\nprojekt   ustawy o transporcie."

        assert _slice(source, "projekt ustawy") == "projekt   ustawy"

    def test_non_breaking_space_matches_a_plain_space(self) -> None:
        # Escaped on purpose: a literal NBSP would be invisible to the next reader,
        # who would see a test asserting that a space equals a space.
        source = "Wzrost o\u00a070 procent w skali roku."

        assert _slice(source, "o 70 procent") == "o\u00a070 procent"

    def test_decomposed_diacritics_match_after_canonicalisation(self) -> None:
        # Some feeds emit NFD; to_canonical is applied at ingest so both sides agree.
        decomposed = to_canonical("Ministerstwo Infrastruktury zapowiedziało zmiany.")

        assert _slice(decomposed, "zapowiedziało zmiany") == "zapowiedziało zmiany"


class TestFuzzyMatch:
    def test_capitalisation_change_is_rescued_and_flagged(self) -> None:
        match = locate_quote("prognozy nie powalają", LEAD)

        assert match is not None
        assert match.fuzzy is True
        assert match.score >= FUZZY_THRESHOLD
        assert LEAD[match.start : match.end].lower() == "prognozy nie powalają"

    def test_paraphrase_below_threshold_is_rejected(self) -> None:
        # Same subject, different words. Exactly what must never reach the database.
        assert locate_quote("prognozy są bardzo rozczarowujące", LEAD) is None

    def test_fuzzy_span_still_indexes_the_original_string(self) -> None:
        source = "Ekspert ostrzega — czeka nas najgorszy scenariusz od lat."
        match = locate_quote("Czeka nas najgorszy scenariusz", source)

        assert match is not None
        assert source[match.start : match.end] == "czeka nas najgorszy scenariusz"


class TestOverlappingSpans:
    """Highlighting is where overlapping spans turn into rendering bugs, so the spans
    themselves have to be correct and independently resolvable first."""

    def test_nested_quotes_resolve_to_nested_spans(self) -> None:
        outer = locate_quote("era rekordowych podwyżek jest już za nami", LEAD)
        inner = locate_quote("rekordowych podwyżek", LEAD)

        assert outer is not None and inner is not None
        assert outer.start <= inner.start
        assert inner.end <= outer.end

    def test_partially_overlapping_quotes_keep_distinct_spans(self) -> None:
        left = locate_quote("era rekordowych podwyżek", LEAD)
        right = locate_quote("podwyżek jest już za nami", LEAD)

        assert left is not None and right is not None
        assert left.start < right.start < left.end < right.end

    def test_repeated_phrase_resolves_to_the_first_occurrence(self) -> None:
        source = "Wzrost cen. Eksperci mówią o tym od miesięcy. Wzrost cen trwa."
        match = locate_quote("Wzrost cen", source)

        assert match is not None
        assert match.start == 0


class TestParaphraseSimilarity:
    """The neutral-alternative test rests entirely on this number separating cleanly.

    The boundary that matters is "the model changed nothing" versus "the model changed
    something". Case and word order must not count as a change, or a headline restated in
    lower case would read as a successful rewrite and every standard event name would pass.
    """

    @pytest.mark.parametrize(
        "quote,alternative",
        [
            ("Tragedia nad Bałtykiem", "tragedia nad Bałtykiem"),
            ("Wypadek śmiertelny na S7", "Wypadek śmiertelny na S7"),
            ("Tragiczny wypadek autokaru", "wypadek autokaru tragiczny"),
        ],
    )
    def test_unchanged_restatement_scores_exactly_100(self, quote: str, alternative: str) -> None:
        assert paraphrase_similarity(quote, alternative) == 100.0

    @pytest.mark.parametrize(
        "quote,alternative",
        [
            ("Armagedon na południu Polski", "podtopienia na południu Polski"),
            ("Szokujące ustalenia na Bałkanach", "ustalenia na Bałkanach"),
            ("Płonie zaplecze armii Putina", "płonie magazyn firmy Wildberries"),
        ],
    )
    def test_real_rewrite_scores_below_100(self, quote: str, alternative: str) -> None:
        assert paraphrase_similarity(quote, alternative) < 100.0

    def test_empty_alternative_scores_zero(self) -> None:
        """A model that skips the paraphrase must not look like one that rewrote nothing."""
        assert paraphrase_similarity("Armagedon na południu Polski", "") == 0.0

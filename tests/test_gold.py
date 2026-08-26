"""Tests for the gold set: its internal consistency rules and how candidates are sampled.

``check_consistency`` is the reason exhaustive annotation is safe to do by hand — it
derives ``expected_assessment`` from the labels using the prompt's own rule, so an
annotator cannot leave the two disagreeing. Its boundaries are pinned here because moving
one silently rescores every model ever measured.

``select`` decides which articles the next reference set is built from. A sampler that
quietly returned a prefix of one day's articles would produce a gold set about one running
story, and nothing downstream would reveal it.
"""

from __future__ import annotations

from typing import Any

import pytest

from evals.candidates import select, slugify
from evals.gold import GoldArticle, GoldError, GoldLabel, check_consistency, check_stored_text


def article(
    slug: str = "a",
    kind: str = "loaded",
    expected_assessment: str = "mildly_loaded",
    title: str = "Tytuł",
    lead: str = "Lead",
) -> GoldArticle:
    return GoldArticle(
        slug=slug,
        portal="Onet",
        url=f"https://onet.pl/{slug}",
        title=title,
        lead=lead,
        kind=kind,
        expected_category="inne",
        expected_assessment=expected_assessment,
    )


def label(slug: str = "a") -> GoldLabel:
    return GoldLabel(
        slug=slug, type="emotional_load", field="title", quote="q", note="n", start=0, end=1
    )


class TestExpectedAssessment:
    """The prompt counts findings and nothing else: 0 neutral, 1-2 mildly, 3+ heavily."""

    @pytest.mark.parametrize(
        ("count", "assessment"),
        [
            (0, "neutral"),
            (1, "mildly_loaded"),
            (2, "mildly_loaded"),
            (3, "heavily_loaded"),
            (4, "heavily_loaded"),
        ],
    )
    def test_label_count_fixes_the_assessment(self, count: int, assessment: str) -> None:
        kind = "neutral" if count == 0 else "loaded"
        check_consistency(
            [article(kind=kind, expected_assessment=assessment)], [label() for _ in range(count)]
        )

    def test_third_label_promotes_to_heavily_loaded(self) -> None:
        """The boundary exhaustive annotation actually crosses: one clearest example per
        article kept most loaded articles at two labels or fewer."""
        with pytest.raises(GoldError, match="3 label"):
            check_consistency([article(expected_assessment="mildly_loaded")], [label()] * 3)

    def test_neutral_article_may_not_carry_labels(self) -> None:
        with pytest.raises(GoldError, match="must carry no labels"):
            check_consistency(
                [article(kind="neutral", expected_assessment="mildly_loaded")], [label()]
            )


class TestStoredText:
    """Ingest stores the same URLs, so a gold row can predate the load."""

    def test_matching_text_passes(self) -> None:
        check_stored_text([article()], {"a": {"title": "Tytuł", "lead": "Lead"}})

    def test_absent_lead_reads_as_empty(self) -> None:
        check_stored_text([article(lead="")], {"a": {"title": "Tytuł", "lead": None}})

    def test_drifted_title_fails(self) -> None:
        with pytest.raises(GoldError, match="stored title"):
            check_stored_text([article()], {"a": {"title": "Inny tytuł", "lead": "Lead"}})


class TestSlugify:
    def test_polish_diacritics_fold_to_ascii(self) -> None:
        assert slugify("Głośne żądania ćwierć miliona", set()) == "glosne-zadania-cwierc"

    def test_short_words_are_skipped(self) -> None:
        """Prepositions carry no identity, so "Bus w Małopolsce" must not slug as "bus-w"."""
        assert slugify("Bus w Małopolsce", set()) == "bus-malopolsce"

    def test_collisions_get_a_suffix(self) -> None:
        taken: set[str] = set()
        assert slugify("Wypadek autokaru", taken) == "wypadek-autokaru"
        assert slugify("Wypadek autokaru", taken) == "wypadek-autokaru-2"


def candidate(flagged: bool, cell_rank: int) -> dict[str, Any]:
    return {"flagged": flagged, "cell_rank": cell_rank}


class TestSelect:
    def test_neutral_sample_is_capped_and_flagged_are_not(self) -> None:
        rows = [candidate(True, i) for i in range(1, 6)]
        rows += [candidate(False, i) for i in range(1, 9)]
        chosen = select(rows, flagged=None, neutral=3)
        assert sum(1 for row in chosen if row["flagged"]) == 5
        assert sum(1 for row in chosen if not row["flagged"]) == 3

    def test_cut_takes_one_per_cell_before_a_second_from_any(self) -> None:
        """The query orders by cell_rank, so a prefix is a stratified sample. Slicing on
        any other order would return one day's articles and look identical from here."""
        rows = [candidate(False, rank) for rank in (1, 1, 1, 2, 2, 3)]
        assert [row["cell_rank"] for row in select(rows, flagged=None, neutral=4)] == [1, 1, 1, 2]

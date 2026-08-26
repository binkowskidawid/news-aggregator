"""The full path from a model response to a verified analysis.

Both properties tested here were broken in production and neither was visible from the
layer below: quote verification located every span correctly, and the assessment rule was
stated in the prompt and enforced nowhere. What failed was the step that turns a located
span into the row the interface renders.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from analyzer.analyze import analyze_article
from analyzer.prompts import ChatMessage
from analyzer.providers.base import Completion, GrammarMode

TITLE = 'Politycy "zapomnieli" zgłosić luksusowe zakupy'


class StubProvider:
    """Returns one canned response, so the assertions are about our code, not a model."""

    name = "stub"

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        grammar_mode: GrammarMode,
        schema: dict[str, Any],
    ) -> Completion:
        return Completion(
            content=json.dumps(self._payload, ensure_ascii=False),
            model=model,
            latency_ms=1,
            tokens_in=1,
            tokens_out=1,
        )


def _payload(*, assessment: str = "mildly_loaded", **finding: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "emotional_load",
        "quote": '"zapomnieli"',
        "neutral_alternative": "nie zgłosili",
        "explanation": "Cudzysłów sugerujący złą wolę.",
        "confidence": 0.85,
    }
    return {
        "category": "polityka",
        "category_confidence": 0.9,
        "overall_assessment": assessment,
        "findings": [base | finding] if finding.get("quote") != "" else [],
    }


class TestVerifiedQuote:
    async def test_the_stored_quote_is_the_publisher_s_wording_not_the_model_s(self) -> None:
        """The model tidies straight quotes into typographic ones and still matches, because
        matching folds typography. Storing its version would print punctuation the outlet
        never used inside quotation marks attributed to that outlet."""
        provider = StubProvider(_payload(quote="„zapomnieli”"))

        analysis = await analyze_article(provider, title=TITLE, lead=None, model="stub")

        assert analysis.findings[0].quote == '"zapomnieli"'

    async def test_the_stored_quote_equals_the_span_its_offsets_select(self) -> None:
        provider = StubProvider(_payload(quote="„zapomnieli”"))

        analysis = await analyze_article(provider, title=TITLE, lead=None, model="stub")

        finding = analysis.findings[0]
        assert TITLE[finding.start : finding.end] == finding.quote

    async def test_a_quote_absent_from_the_source_is_still_discarded(self) -> None:
        """The property the whole product rests on, restated after the change above."""
        provider = StubProvider(_payload(quote="zdanie, którego nikt nie napisał"))

        analysis = await analyze_article(provider, title=TITLE, lead=None, model="stub")

        assert analysis.findings == ()
        assert analysis.rejected_quotes == ("zdanie, którego nikt nie napisał",)


class TestConsistency:
    @pytest.mark.parametrize(
        ("assessment", "confidence"),
        [("neutral", 0.85), ("mildly_loaded", 0.5)],
        ids=["neutral-with-a-confident-finding", "loaded-without-one"],
    )
    async def test_a_verdict_that_contradicts_its_findings_is_recorded(
        self, assessment: str, confidence: float
    ) -> None:
        provider = StubProvider(_payload(assessment=assessment, confidence=confidence))

        analysis = await analyze_article(provider, title=TITLE, lead=None, model="stub")

        assert analysis.consistency_error is not None

    async def test_a_contradiction_is_not_repaired(self) -> None:
        """Recorded, never fixed. A verdict rewritten to agree with the findings would
        delete the evidence that this model does not apply the rule it was given."""
        provider = StubProvider(_payload(assessment="neutral"))

        analysis = await analyze_article(provider, title=TITLE, lead=None, model="stub")

        assert analysis.overall_assessment is not None
        assert analysis.overall_assessment.value == "neutral"
        assert len(analysis.findings) == 1

    async def test_an_agreeing_verdict_records_nothing(self) -> None:
        provider = StubProvider(_payload())

        analysis = await analyze_article(provider, title=TITLE, lead=None, model="stub")

        assert analysis.consistency_error is None

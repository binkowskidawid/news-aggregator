"""Tests for the output contract.

The schema is handed to the model as a sampling grammar, so a defect here does not
surface as a validation error — it surfaces as a model that quietly produces worse
analysis, which is far harder to attribute.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from domain.analysis import (
    MAX_FINDINGS,
    AnalysisResult,
    Assessment,
    Category,
    Finding,
    ManipulationType,
    inlined_json_schema,
)


def _finding(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "emotional_load",
        "quote": "szokujące doniesienia",
        "neutral_alternative": "doniesienia",
        "explanation": "Wartościujące określenie bez uzasadnienia w treści.",
        "confidence": 0.8,
    }
    return base | overrides


def _result(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "category": "polityka",
        "category_confidence": 0.9,
        "overall_assessment": "mildly_loaded",
        "findings": [_finding()],
    }
    return base | overrides


class TestAnalysisResult:
    def test_accepts_a_well_formed_payload(self) -> None:
        result = AnalysisResult.model_validate(_result())

        assert result.category is Category.POLITYKA
        assert result.overall_assessment is Assessment.MILDLY_LOADED
        assert result.findings[0].type is ManipulationType.EMOTIONAL_LOAD

    def test_empty_findings_is_valid(self) -> None:
        # The most common honest outcome. A model with no way to report "nothing here"
        # invents something instead.
        result = AnalysisResult.model_validate(_result(findings=[], overall_assessment="neutral"))

        assert result.findings == []

    def test_rejects_more_findings_than_the_ceiling(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisResult.model_validate(_result(findings=[_finding()] * (MAX_FINDINGS + 1)))

    def test_accepts_exactly_the_ceiling(self) -> None:
        result = AnalysisResult.model_validate(_result(findings=[_finding()] * MAX_FINDINGS))

        assert len(result.findings) == MAX_FINDINGS

    def test_rejects_unknown_category(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisResult.model_validate(_result(category="rozrywka"))

    def test_rejects_unknown_technique(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisResult.model_validate(_result(findings=[_finding(type="sarkazm")]))

    def test_rejects_extra_fields(self) -> None:
        # extra="forbid" is what makes the emitted schema strict enough for providers
        # that only honour additionalProperties:false.
        with pytest.raises(ValidationError):
            AnalysisResult.model_validate(_result(sentiment="negative"))


class TestFinding:
    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_rejects_confidence_outside_the_unit_interval(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            Finding.model_validate(_finding(confidence=confidence))

    @pytest.mark.parametrize("quote", ["", "   ", "\n\t"])
    def test_rejects_blank_quote(self, quote: str) -> None:
        with pytest.raises(ValidationError):
            Finding.model_validate(_finding(quote=quote))

    def test_rejects_overlong_explanation(self) -> None:
        with pytest.raises(ValidationError):
            Finding.model_validate(_finding(explanation="x" * 201))


class TestInlinedSchema:
    """llama.cpp compiles the schema into a sampling grammar, and its handling of $ref has
    varied between builds. Inlining removes a failure mode whose symptoms — empty findings,
    looping tokens — look nothing like a schema problem."""

    def test_no_references_remain(self) -> None:
        serialised = json.dumps(inlined_json_schema())

        assert "$ref" not in serialised
        assert "$defs" not in serialised

    def test_enumerations_are_expanded_in_place(self) -> None:
        schema = inlined_json_schema()

        assert set(schema["properties"]["category"]["enum"]) == {c.value for c in Category}

    def test_finding_shape_is_expanded_in_place(self) -> None:
        schema = inlined_json_schema()
        item = schema["properties"]["findings"]["items"]

        assert set(item["properties"]) == {
            "type",
            "quote",
            "neutral_alternative",
            "explanation",
            "confidence",
        }
        assert item["additionalProperties"] is False

    def test_findings_ceiling_survives_inlining(self) -> None:
        schema = inlined_json_schema()

        assert schema["properties"]["findings"]["maxItems"] == MAX_FINDINGS

    def test_inlined_schema_still_describes_the_model(self) -> None:
        # Guards against the expansion silently dropping constraints.
        inlined = inlined_json_schema()
        original = AnalysisResult.model_json_schema()

        assert set(inlined["required"]) == set(original["required"])
        assert inlined["additionalProperties"] is False

    def test_verdict_is_declared_after_the_findings_it_counts(self) -> None:
        # Schema order reached the model as a suggestion, not a constraint: under v1.1.3
        # llama.cpp emitted this order in 140 of 198 responses and the old one in 58, and
        # consistency was identical either way. So this pins the declared contract only —
        # the schema, the format block and the few-shot answers agreeing on one layout —
        # which is what stops a future edit from silently splitting them apart again.
        properties = list(inlined_json_schema()["properties"])

        assert properties.index("findings") < properties.index("overall_assessment")

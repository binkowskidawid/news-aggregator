"""End-to-end analysis of one article: prompt, call, parse, verify.

The verification step is not a formality. A structurally perfect response can still cite
a sentence the article never contained, so the schema guarantees shape and this module
guarantees contact with reality.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from analyzer.prompts import PROMPT_VERSION, Prompt, build_prompt
from analyzer.providers.base import Completion, GrammarMode, LLMProvider
from analyzer.validator import locate_quote, paraphrase_similarity
from domain.analysis import (
    AnalysisResult,
    Assessment,
    Category,
    ManipulationType,
    consistency_error,
    inlined_json_schema,
)

ArticleField = Literal["title", "lead"]
"""Which part of the article a verified quote was found in.

Recorded because it answers a question the project cannot avoid: persuasive technique
concentrates in headlines, so comparing outlets means knowing how much of each score came
from a headline that every outlet publishes versus a lead that only some expose.
"""


@dataclass(frozen=True, slots=True)
class VerifiedFinding:
    """A finding whose quote was located in the source text."""

    type: ManipulationType
    quote: str
    """The span as the source published it, sliced by ``start``/``end``."""

    field: ArticleField
    start: int
    end: int
    fuzzy: bool
    neutral_alternative: str
    neutral_similarity: float
    explanation: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Analysis:
    """Everything one model call produced, successful or not."""

    completion: Completion
    prompt: Prompt
    grammar_mode: GrammarMode
    category: Category | None
    category_confidence: float | None
    overall_assessment: Assessment | None
    findings: tuple[VerifiedFinding, ...]
    rejected_quotes: tuple[str, ...]
    parse_error: str | None
    consistency_error: str | None = None
    """Set when the verdict contradicts the findings shown beside it. Not a failure."""

    @property
    def quotes_total(self) -> int:
        return len(self.findings) + len(self.rejected_quotes)

    @property
    def quotes_fuzzy(self) -> int:
        return sum(1 for finding in self.findings if finding.fuzzy)

    @property
    def quote_fidelity(self) -> float | None:
        """Share of quotes that were actually present in the source.

        The one quality measure that needs no reference annotations, which makes it
        available from the first run and immune to disagreement about the labels.
        """
        if self.quotes_total == 0:
            return None
        return len(self.findings) / self.quotes_total


def _verify_quotes(
    result: AnalysisResult, title: str, lead: str | None
) -> tuple[tuple[VerifiedFinding, ...], tuple[str, ...]]:
    """Locate each quote, discarding those that cannot be found.

    Title and lead are searched separately rather than as one concatenated string. Two
    reasons: offsets stay relative to the field the UI actually renders, and a quote
    cannot silently "match" by straddling the boundary between a headline and a sentence
    that never followed it.

    What survives is the *source* span, not the string the model sent. Matching folds
    typography, so a model that tidies ``"zapomnieli"`` into ``„zapomnieli”`` still
    matches — and storing its version would put punctuation the outlet never printed
    inside quotation marks attributed to that outlet. The model's wording is not lost:
    the verbatim reply is kept in ``analyses.raw_response``.
    """
    fields: list[tuple[ArticleField, str]] = [("title", title)]
    if lead:
        fields.append(("lead", lead))

    verified: list[VerifiedFinding] = []
    rejected: list[str] = []

    for finding in result.findings:
        for field_name, text in fields:
            match = locate_quote(finding.quote, text)
            if match is None:
                continue
            source_quote = text[match.start : match.end]
            verified.append(
                VerifiedFinding(
                    type=finding.type,
                    quote=source_quote,
                    field=field_name,
                    start=match.start,
                    end=match.end,
                    fuzzy=match.fuzzy,
                    neutral_alternative=finding.neutral_alternative,
                    neutral_similarity=paraphrase_similarity(
                        source_quote, finding.neutral_alternative
                    ),
                    explanation=finding.explanation,
                    confidence=finding.confidence,
                )
            )
            break
        else:
            rejected.append(finding.quote)

    return tuple(verified), tuple(rejected)


async def analyze_article(
    provider: LLMProvider,
    *,
    title: str,
    lead: str | None,
    model: str,
    grammar_mode: GrammarMode = "schema",
    source_label: str | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> Analysis:
    """Run one article through one model and verify what comes back.

    A response that fails to parse is returned as an :class:`Analysis` carrying
    ``parse_error`` rather than raising. Under a grammar this is rare, and its usual
    causes — truncation at the output ceiling, a timeout, a prompt clipped by the context
    window — are configuration faults. Recording them keeps them visible as such instead
    of letting a repair heuristic hide the signal that says which setting is wrong.
    """
    prompt = build_prompt(title, lead, version=prompt_version, source_label=source_label)
    completion = await provider.complete(
        prompt.messages,
        model=model,
        grammar_mode=grammar_mode,
        schema=inlined_json_schema(),
    )

    try:
        result = AnalysisResult.model_validate_json(completion.content)
    except (ValidationError, json.JSONDecodeError) as exc:
        detail = str(exc)
        if completion.hit_output_ceiling:
            detail = f"output truncated at the token ceiling; {detail}"
        return Analysis(
            completion=completion,
            prompt=prompt,
            grammar_mode=grammar_mode,
            category=None,
            category_confidence=None,
            overall_assessment=None,
            findings=(),
            rejected_quotes=(),
            parse_error=detail,
        )

    findings, rejected = _verify_quotes(result, title, lead)
    return Analysis(
        completion=completion,
        prompt=prompt,
        grammar_mode=grammar_mode,
        category=result.category,
        category_confidence=result.category_confidence,
        overall_assessment=result.overall_assessment,
        findings=findings,
        rejected_quotes=rejected,
        parse_error=None,
        consistency_error=consistency_error(
            result.overall_assessment, [finding.confidence for finding in findings]
        ),
    )

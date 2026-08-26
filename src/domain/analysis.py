"""The output contract for manipulation analysis.

These models are the single source of truth. The same definitions generate the JSON
Schema handed to the model, validate what comes back, and describe what is written to
the database — so the contract cannot drift between the three.

Enum *values* are the wire format and are not translated casually: they are what the model
emits and what the CHECK constraints in migrations/001_init.sql accept. Changing one means
changing the prompt, the schema and the stored rows together — see ``Category``.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_FINDINGS: Final = 8
"""Ceiling on findings per article.

An article of a headline plus a short lead cannot honestly support more; a model that
wants to report more is pattern-matching vocabulary rather than identifying technique.
"""


class ManipulationType(StrEnum):
    """Closed vocabulary of language techniques the model may report.

    The list is closed on purpose. An open-ended "find manipulation" instruction produces
    a different taxonomy on every call, which cannot be measured, compared across models,
    or defended to the outlet being described.

    ``clickbait_hook`` looks like the weakest of the six, and **deleting it has already
    been tried and reverted**. Filtering it out of stored results suggested precision would
    rise from 46% to 49%; prompt v1.3.0, which actually removed it from the taxonomy,
    measured 40%. The model does not stop noticing those headlines when the label goes away
    — it files them under ``emotional_load``, where they count as false positives just the
    same. Removing a type offline is not the same experiment as removing it from the prompt.
    """

    EMOTIONAL_LOAD = "emotional_load"
    FEAR_APPEAL = "fear_appeal"
    OVERGENERALIZATION = "overgeneralization"
    LOADED_QUESTION = "loaded_question"
    UNSOURCED_FIGURE = "unsourced_figure"
    CLICKBAIT_HOOK = "clickbait_hook"


class Category(StrEnum):
    """Fixed topical taxonomy.

    ``INNE`` ("other") is the escape hatch, added to the seven topical categories. Without
    one the model forces every article into the nearest category, which quietly corrupts
    the feed.

    **The values are Polish because they are the wire format, not identifiers for a
    reader.** The system prompt is Polish and names these eight words as the permitted
    answers, so this is what the model emits and what the CHECK constraint accepts. No
    reader ever sees them: ``web/messages/`` maps each to a display name in both locales.

    They are nonetheless inconsistent with ``ManipulationType``, whose values are English
    and come out of the same prompt — an artefact of the taxonomies having different
    origins, not a decision. **Migrating them to English is intended**, and it is not a
    rename: the prompt has to change, which bumps ``PROMPT_VERSION`` and makes every
    published quality figure describe a configuration nobody has measured. It therefore
    belongs with the next evaluation run, alongside a migration for the CHECK constraint
    and the stored rows — never on its own.
    """

    POLITYKA = "polityka"
    KULTURA = "kultura"
    TECHNOLOGIA = "technologia"
    SPORT = "sport"
    BIZNES = "biznes"
    GEOPOLITYKA = "geopolityka"
    ZDROWIE = "zdrowie"
    INNE = "inne"


class Assessment(StrEnum):
    """Overall verdict for one article."""

    NEUTRAL = "neutral"
    MILDLY_LOADED = "mildly_loaded"
    HEAVILY_LOADED = "heavily_loaded"


class Finding(BaseModel):
    """One technique, anchored to a verbatim span of the source text."""

    # extra="forbid" emits additionalProperties:false, which OpenAI-compatible providers
    # require before they will honour a strict schema.
    model_config = ConfigDict(extra="forbid")

    type: ManipulationType
    quote: str = Field(min_length=1, max_length=400)
    # Optional, because the prompt versions being compared disagree about whether to ask
    # for it: v1.2.0 requires the paraphrase, v1.1.0 and v1.2.1 never mention it. A field
    # required by the contract would make the ablation unrunnable, and the ablation is the
    # point — v1.2.0 changed three things at once and lost 12 points of precision without
    # saying which change spent them.
    #
    # The cost is that a constrained model *may* omit it where the prompt asks for one. That
    # is measured rather than assumed: an empty string is stored and counted, the same way
    # an unverifiable quote is recorded instead of repaired.
    neutral_alternative: str = Field(default="", max_length=400)
    explanation: str = Field(max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("quote")
    @classmethod
    def quote_must_have_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quote must contain non-whitespace characters")
        return value


class AnalysisResult(BaseModel):
    """Complete model output for one article.

    ``findings`` sits above ``overall_assessment`` because the prompt defines the verdict
    as a count of findings at confidence >= 0.7 and nothing else, so evidence before
    conclusion is the honest layout.

    **It is not here to fix the contradiction rate, and that is worth recording so nobody
    re-derives it.** The hypothesis was that a constrained decoder commits to a verdict
    declared above ``findings`` before the findings exist, which would explain the
    ``consistency_error`` cases — a ``neutral`` verdict standing beside confident findings.
    Both halves were measured and both are false: llama.cpp emitted the schema order in
    only 140 of 198 responses, so schema order is a nudge and not a constraint, and within
    one run consistency is the same whichever order came out (1.7% against 1.9%).
    Generation order does not cause the contradiction. Reordering the schema is a neutral
    change, never a fix.

    ``category`` stays first. Moving it below ``findings`` is a separate hypothesis with
    its own risk, since category accuracy is the strongest of the measured metrics and the
    one most visible in the feed.
    """

    model_config = ConfigDict(extra="forbid")

    category: Category
    category_confidence: float = Field(ge=0.0, le=1.0)
    findings: list[Finding] = Field(default_factory=list, max_length=MAX_FINDINGS)
    overall_assessment: Assessment


CONFIDENT: Final = 0.7
"""The prompt's own bar for a finding that counts towards ``overall_assessment``.

prompts/system-v1.1.0.txt states the rule outright: the verdict is computed from findings
at this confidence or above, and ``neutral`` means there are none of them. Unlike the
decision thresholds in ``evals.report``, this number is not a bar the project invented to
judge a model — it is part of the instruction the model was given, so applying it only
asks whether the model followed its own rules.

Worth knowing before treating ``confidence`` as a signal: it barely varies. Six findings
out of 1290 stored fall below this bar, and gemma4 under v1.1.0 never emits anything under
0.70 at all, clustering 257 of its reports on exactly 0.85. The field records that the
model chose to report something, not how sure it was.
"""


def consistency_error(assessment: Assessment, confidences: Sequence[float]) -> str | None:
    """Whether a verdict contradicts the findings it is supposed to have been derived from.

    The rule was enforced on the annotator (``evals.gold.check_consistency``) and on nobody
    else, so a model could return ``heavily_loaded`` beside an empty ``findings`` array and
    the row passed validation as a correct analysis. ``deepseek-v4-flash`` did exactly that
    on 33 articles; ``gemma4`` reaches the same contradiction from the other side, calling
    an article ``neutral`` while reporting a finding it is confident about.

    Recorded, never repaired — the same treatment ``parse_error`` gets and for the same
    reason. A verdict rewritten to agree with the findings would erase the one signal that
    says this model does not apply its own rule.

    Takes confidences rather than findings so it can be applied to whichever set is at
    hand: it runs here against the findings that survived quote verification, because
    those are what the interface puts on screen next to the verdict.
    """
    confident = sum(1 for value in confidences if value >= CONFIDENT)
    if assessment is Assessment.NEUTRAL:
        if confident:
            return f"neutral verdict beside {confident} finding(s) at confidence >= {CONFIDENT}"
        return None
    if not confident:
        return f"{assessment.value} verdict with no finding at confidence >= {CONFIDENT}"
    return None


def inlined_json_schema() -> dict[str, Any]:
    """Return the schema for :class:`AnalysisResult` with every ``$ref`` expanded.

    Pydantic factors nested models and enums into ``$defs`` and references them. Ollama
    compiles the schema into a sampling grammar via llama.cpp, whose handling of ``$ref``
    has historically varied between builds; when it misbehaves the symptom is silent and
    misleading — persistently empty ``findings``, looping tokens, or responses truncated
    at the same point — none of which looks like a schema problem.

    Inlining removes the question. Cheaper than diagnosing it from the wrong end.
    """
    schema = AnalysisResult.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                name = ref.rsplit("/", 1)[-1]
                if name not in definitions:
                    raise KeyError(f"unresolved schema reference: {ref}")
                # Sibling keys (title, description) stay and override the target's.
                merged = {**copy.deepcopy(definitions[name])}
                merged.update({k: v for k, v in node.items() if k != "$ref"})
                return resolve(merged)
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved

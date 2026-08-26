"""What the HTTP layer hands out.

Separate from ``domain.analysis`` on purpose. That module is the contract with the model —
one definition generating the JSON Schema, validating the reply and mirroring the CHECK
constraints — and widening it to also mean "what a browser receives" would make every
change to the reader's view a change to what the model is asked for. The enums are shared;
the shapes are not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from domain.analysis import Assessment, Category, ManipulationType


class Provenance(BaseModel):
    """Where the analysis came from, carried alongside it rather than beside it.

    Article 50 of Regulation (EU) 2024/1689 requires generated content to be disclosed as
    generated. What travels here is the fact, not the sentence: a client that renders an
    analysis cannot avoid receiving ``ai_generated``, while the wording it shows is a
    translated string and belongs with the other translated strings.
    """

    ai_generated: Literal[True] = True
    human_verified: Literal[False] = False
    model_name: str
    prompt_version: str
    analysed_at: datetime


class FindingOut(BaseModel):
    """One reported fragment.

    ``start``/``end`` index the original ``field`` string, so a client slices rather than
    searches. That is the property the quote validator exists to guarantee and the reason
    its tests slice the source string instead of comparing text.
    """

    type: ManipulationType
    field: Literal["title", "lead"]
    quote: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    explanation: str | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    neutral_alternative: str | None


class FeedItem(BaseModel):
    id: uuid.UUID
    title: str
    lead: str | None
    url: str
    source: str
    published_at: datetime | None
    category: Category | None
    overall_assessment: Assessment | None
    finding_count: int


class Feed(BaseModel):
    items: list[FeedItem]
    total: int
    limit: int
    offset: int


class ArticleDetail(BaseModel):
    id: uuid.UUID
    title: str
    lead: str | None
    url: str
    source: str
    published_at: datetime | None
    category: Category | None
    category_confidence: float | None
    overall_assessment: Assessment | None
    findings: list[FindingOut]
    provenance: Provenance

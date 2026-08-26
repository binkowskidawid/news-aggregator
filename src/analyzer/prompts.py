"""Assembly of the message list sent to the model.

Prompts live in ``prompts/`` as files rather than string literals: they are versioned
artefacts, reviewed by a non-technical reader, and every stored analysis records which
version produced it. Without that, there is no way to tell whether a prompt change helped.
"""

from __future__ import annotations

import json
import secrets
import string
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, TypedDict

PROMPT_VERSION: Final = "v1.1.3"
"""Bumped by any change to prompt text. Recorded alongside every analysis.

v1.1.3 changes no wording. It is v1.1.1 with ``overall_assessment`` moved after
``findings`` in the format block and in all four worked examples, matching the reordered
``AnalysisResult``. The examples moved with the schema on purpose: four demonstrations of
the old order condition the model more strongly than the format block does, so reordering
the schema alone would have measured a conflict with them rather than the reordering.
**The hypothesis behind it was tested and rejected** — the version is kept because it
measured no worse, not because it works. Do not cite it as a fix for anything.

Every version file in ``prompts/`` is reachable from ``analyses.prompt_version`` and none
may be deleted; a stored analysis whose prompt is gone cannot be reproduced. Two of them
are there for reasons the file names do not carry:

- ``system-v1.1.2.txt`` is **deliberately not in use**. It removes a redundant ``inne``
  definition — v1.1.1 added a fuller one above the one-liner inherited from v1.1.0 without
  deleting it, so the category stands defined twice in the shipped prompt. The two agree,
  so it is redundancy, not contradiction. Removing it was measured and cost more than it
  bought. The tidy-up rides along with the next substantive prompt change, which
  re-establishes the numbers anyway.
- The v1.2.x and v1.3.0 rewrites all measured worse than v1.1.0.

Quality figures for the shipped configuration live in ``MODEL_CARD.md``, which is the only
place they are maintained.
"""

PROMPT_DIR: Final = Path(__file__).resolve().parents[2] / "prompts"

_NONCE_ALPHABET: Final = string.ascii_lowercase + string.digits
_NONCE_LENGTH: Final = 8
_NONCE_PLACEHOLDER: Final = "{{NONCE}}"

InputVariant = Literal["title", "title_lead"]
"""Which slice of the article the model is shown.

Persuasive technique concentrates in headlines, so a portal that exposes only headlines
would show a higher density of indicators per unit of text than one that also exposes a
lead — regardless of how either actually writes. Recording the variant is what keeps that
artefact of our fetching strategy from being read as a property of the publisher.
"""


class ChatMessage(TypedDict):
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class Prompt:
    """A ready-to-send request, with everything needed to reproduce it later."""

    messages: list[ChatMessage]
    nonce: str
    version: str
    input_variant: InputVariant


def generate_nonce() -> str:
    """Return a fresh delimiter suffix, unique per request.

    A fixed delimiter such as ``<article>`` can be forged: an article containing the
    closing tag would make everything after it read as text outside the data section. The
    nonce is unknown when the article is written, which closes that route. Given the
    subject matter, someone attempting it deliberately is a realistic scenario rather than
    a theoretical one.
    """
    return "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(_NONCE_LENGTH))


def strip_control_chars(text: str) -> str:
    """Remove control characters, keeping tab and newline.

    Deliberately narrow. Text that looks like an instruction is left untouched: removing
    it would alter the material we were asked to assess, and defending against it is the
    job of the system prompt, the constrained output schema, and quote verification —
    three layers that do not require guessing which sentences are suspicious.
    """
    return "".join(
        char for char in text if char in "\t\n" or not (ord(char) < 32 or ord(char) == 127)
    )


@lru_cache(maxsize=8)
def _load_system_template(version: str) -> str:
    return (PROMPT_DIR / f"system-{version}.txt").read_text(encoding="utf-8")


@lru_cache(maxsize=8)
def _load_fewshot_template(version: str) -> str:
    return (PROMPT_DIR / f"fewshot-{version}.json").read_text(encoding="utf-8")


def _fewshot_messages(version: str, nonce: str) -> list[ChatMessage]:
    """Load the worked examples with the current nonce substituted.

    Substitution happens on the raw file text, before parsing, so the examples carry the
    same delimiter as the article that follows them. A mismatch would show the model two
    different conventions for where the data section begins.
    """
    raw = _load_fewshot_template(version).replace(_NONCE_PLACEHOLDER, nonce)
    parsed: list[ChatMessage] = json.loads(raw)
    return parsed


def build_prompt(
    title: str,
    lead: str | None = None,
    *,
    version: str = PROMPT_VERSION,
    source_label: str | None = None,
) -> Prompt:
    """Assemble the full message list for one article.

    ``source_label`` is ``None`` in normal operation: the model is not told who published
    the text. With outlets of opposing political profiles on the same list, a model that
    recognises the brands could apply different thresholds to identical language, and a
    tool that finds more manipulation in one outlet because of its name is worse than
    useless. The parameter exists so that claim can be tested rather than assumed — see the
    brand-bias probe in the evaluation harness.
    """
    nonce = generate_nonce()
    variant: InputVariant = "title_lead" if lead else "title"

    body = [f"TYTUŁ: {strip_control_chars(title)}"]
    if lead:
        body.append(f"LEAD: {strip_control_chars(lead)}")
    if source_label is not None:
        body.insert(0, f"PORTAL: {strip_control_chars(source_label)}")

    article_block = "\n".join(
        [f"<article_{nonce}>", *body, f"</article_{nonce}>"],
    )

    messages: list[ChatMessage] = [
        {
            "role": "system",
            "content": _load_system_template(version).replace(_NONCE_PLACEHOLDER, nonce),
        },
        *_fewshot_messages(version, nonce),
        {"role": "user", "content": article_block},
    ]

    return Prompt(messages=messages, nonce=nonce, version=version, input_variant=variant)

"""Verification that a quoted span actually occurs in the source text.

This is the quality mechanism the product rests on. A tool that accuses named outlets of
manipulation cannot cite sentences those outlets never published, and it is the only
defence against prompt injection that does not depend on the model's cooperation: an
injected instruction may steer the model, but it cannot make invented text appear in an
article we already hold.

Offsets returned by :func:`locate_quote` index the *source string passed in*, not an
internal normalised form, so they can be used directly to highlight the text as published.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Final

from rapidfuzz import fuzz

FUZZY_THRESHOLD: Final = 95.0
"""Similarity below which a quote is treated as unverifiable and dropped.

High on purpose. Fuzzy matching exists to absorb typographic drift — a capitalised first
letter, a dash swapped for a hyphen — not to make a loose paraphrase pass as a quotation.
"""

_TYPOGRAPHY: Final[dict[str, str]] = {
    "–": "-",  # en dash
    "—": "-",  # em dash
    "−": "-",  # minus sign
    "‘": "'",  # left single quote
    "’": "'",  # right single quote / apostrophe
    "“": '"',  # left double quote
    "”": '"',  # right double quote
    "„": '"',  # Polish opening quote
    "«": '"',  # guillemets, used by some Polish outlets for inner quotes
    "»": '"',
    "…": "...",  # ellipsis
}
"""Characters models routinely "tidy" when quoting.

Normalising both sides is preferable to rejecting a quote over punctuation the model
never meant to change.
"""


@dataclass(frozen=True, slots=True)
class QuoteMatch:
    """Where a quote was found, and how confidently."""

    start: int
    """Inclusive character offset into the source string."""

    end: int
    """Exclusive character offset into the source string."""

    fuzzy: bool
    """True when the quote matched only after fuzzy comparison."""

    score: float
    """Similarity score; 100.0 for a verbatim match."""


def to_canonical(text: str) -> str:
    """Compose combining marks so Polish diacritics are one code point each.

    Applied once when an article is stored. Feeds are inconsistent about whether they
    send precomposed or decomposed forms, and doing this at the boundary means every
    offset computed later refers to the same string the database holds.
    """
    return unicodedata.normalize("NFC", text)


def _normalise(text: str) -> tuple[str, tuple[int, ...]]:
    """Fold typography and collapse whitespace, tracking where each character came from.

    Returns the folded text alongside a map whose *i*-th entry is the index, in ``text``,
    of the character that produced the *i*-th output character. The map is what lets a
    match found in folded space be reported against the original string; without it every
    consumer would have to re-derive the shift, and the highlight in the UI would sit a
    few characters off wherever the text contained an ellipsis or a run of spaces.
    """
    output: list[str] = []
    origins: list[int] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if char.isspace():
            run_end = index
            while run_end < length and text[run_end].isspace():
                run_end += 1
            # A single space, and never a leading one.
            if output:
                output.append(" ")
                origins.append(index)
            index = run_end
            continue

        for produced in _TYPOGRAPHY.get(char, char):
            output.append(produced)
            origins.append(index)
        index += 1

    while output and output[-1] == " ":
        output.pop()
        origins.pop()

    return "".join(output), tuple(origins)


def _to_source_span(origins: tuple[int, ...], start: int, end: int) -> tuple[int, int]:
    """Translate a span in folded space back to the source string."""
    # `end` is exclusive in folded space; the source character that produced the last
    # folded character ends one position after it begins.
    return origins[start], origins[end - 1] + 1


def locate_quote(quote: str, source: str) -> QuoteMatch | None:
    """Locate ``quote`` within ``source``, or return ``None`` if it is not there.

    Two stages. A verbatim match after normalisation is the expected outcome. Failing
    that, a single fuzzy pass rescues quotes the model altered trivially — recorded as
    such, because a rising share of fuzzy matches is an early warning that verbatim
    fidelity is about to fall.

    Below the threshold the finding is discarded. Nothing about a plausible-looking quote
    that is absent from the article can be salvaged.
    """
    if not quote.strip():
        return None

    folded_quote, _ = _normalise(quote)
    folded_source, origins = _normalise(source)

    if not folded_quote or not folded_source:
        return None

    exact = folded_source.find(folded_quote)
    if exact != -1:
        start, end = _to_source_span(origins, exact, exact + len(folded_quote))
        return QuoteMatch(start=start, end=end, fuzzy=False, score=100.0)

    # partial_ratio_alignment slides the shorter string across the longer one in C and
    # reports where the best window sat, replacing a hand-rolled O(n*m) Python scan.
    alignment = fuzz.partial_ratio_alignment(
        folded_quote, folded_source, score_cutoff=FUZZY_THRESHOLD
    )
    if alignment is None or alignment.dest_end <= alignment.dest_start:
        return None

    start, end = _to_source_span(origins, alignment.dest_start, alignment.dest_end)
    return QuoteMatch(start=start, end=end, fuzzy=True, score=alignment.score)


def paraphrase_similarity(quote: str, alternative: str) -> float:
    """How much of the quote survived the model's neutral rewrite, on a 0-100 scale.

    The neutral-alternative test rests on one asymmetry. A phrase that carries an
    evaluation can be restated without it, and the restatement differs. A phrase that was
    already description has nothing to remove, so the model hands back what it was given.
    A high score therefore says the finding is probably not a finding — it is how
    "Tragedia nad Bałtykiem" separates from "Armagedon na południu Polski" without anyone
    having to enumerate which nouns are standard.

    Case and word order are both folded away: a rewrite that only lowercases a headline or
    reshuffles the same words removed nothing. Measured on the project's own examples, that
    folding is what makes the scale usable — every genuinely unchanged pair lands on exactly
    100.0, while every real substitution or deletion stayed at or below 81.5.

    **Recorded, not enforced.** The cut-off is a knob, and picking one against a gold set
    of this size would fit the instrument rather than the problem. The score is stored and
    the threshold argued afterwards from its distribution — the same reason `grammar_mode`
    is a column rather than an assumption.
    """
    return float(
        fuzz.token_sort_ratio(to_canonical(quote).lower(), to_canonical(alternative).lower())
    )

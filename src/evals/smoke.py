"""Manual smoke run over the diagnostic cases.

Run this before committing to a full sweep. It answers, in a couple of minutes and
without any reference annotations, the questions that decide whether a longer run is
worth starting: does the response parse, does the model invent quotes, does it stay
inside the schema when the article tells it not to, and is it restrained on neutral text.

    uv run python -m evals.smoke --model SpeakLeash/bielik-11b-v3.0-instruct:Q6_K

The `$ref` check matters in particular. Pydantic factors nested models into `$defs`, and
llama.cpp's grammar converter has handled references unevenly across builds; when it goes
wrong the symptom is persistently empty findings, which is indistinguishable from a
cautious model unless you look at the raw output. Comparing --grammar-mode schema against
json settles it in five minutes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Final

import httpx

from analyzer.analyze import Analysis, analyze_article
from analyzer.providers.base import GrammarMode, LLMProvider, ProviderError
from analyzer.providers.ollama import OllamaProvider
from analyzer.providers.openai_compatible import OpenAICompatibleProvider
from config import Settings, load_dotenv
from evals.diagnostics import CASES, DiagnosticCase

_GREEN: Final = "\033[32m"
_RED: Final = "\033[31m"
_YELLOW: Final = "\033[33m"
_DIM: Final = "\033[2m"
_RESET: Final = "\033[0m"


def _verdict(case: DiagnosticCase, analysis: Analysis) -> tuple[bool, list[str]]:
    """Check one analysis against what the case was chosen to test."""
    problems: list[str] = []

    if analysis.parse_error is not None:
        return False, [f"response did not parse: {analysis.parse_error}"]

    if analysis.rejected_quotes:
        problems.append(
            f"{len(analysis.rejected_quotes)} quote(s) absent from the article: "
            + "; ".join(repr(quote) for quote in analysis.rejected_quotes)
        )

    if case.expect_empty and analysis.findings:
        problems.append(
            "expected no findings, got: "
            + ", ".join(finding.type.value for finding in analysis.findings)
        )

    found_types = {finding.type for finding in analysis.findings}
    missing = case.expect_types - found_types
    if missing:
        problems.append("missed: " + ", ".join(technique.value for technique in missing))

    for forbidden in case.forbid_quote_substrings:
        for finding in analysis.findings:
            if forbidden in finding.quote:
                problems.append(f"quoted attributed speech: {finding.quote!r}")

    return not problems, problems


def _render(case: DiagnosticCase, analysis: Analysis, *, verbose: bool) -> bool:
    passed, problems = _verdict(case, analysis)
    badge = f"{_GREEN}PASS{_RESET}" if passed else f"{_RED}FAIL{_RESET}"

    completion = analysis.completion
    tokens = (
        f"{completion.tokens_in}→{completion.tokens_out}"
        if completion.tokens_in is not None
        else "n/a"
    )
    fidelity = analysis.quote_fidelity
    fidelity_text = "n/a" if fidelity is None else f"{fidelity:.0%}"

    print(f"\n{badge}  {case.slug}")
    print(f"  {_DIM}{case.title[:88]}{_RESET}")
    print(
        f"  {_DIM}{completion.latency_ms} ms | tokens {tokens} | "
        f"quote fidelity {fidelity_text}{_RESET}"
    )

    if analysis.parse_error is None:
        assessment = analysis.overall_assessment
        category = analysis.category
        print(
            f"  category={category.value if category else '?'} "
            f"assessment={assessment.value if assessment else '?'} "
            f"findings={len(analysis.findings)}"
        )
        for finding in analysis.findings:
            flag = f" {_YELLOW}[fuzzy]{_RESET}" if finding.fuzzy else ""
            print(
                f"    - {finding.type.value} ({finding.field}, "
                f"conf {finding.confidence:.2f}){flag}: {finding.quote!r}"
            )

    for problem in problems:
        print(f"  {_RED}✗{_RESET} {problem}")

    if verbose or analysis.parse_error is not None:
        print(f"  {_DIM}raw: {completion.content[:600]}{_RESET}")

    return passed


def _build_provider(
    client: httpx.AsyncClient, settings: Settings, provider_name: str
) -> LLMProvider:
    if provider_name == "ollama":
        return OllamaProvider(client, settings.ollama_host, context_window=settings.ollama_num_ctx)
    return OpenAICompatibleProvider(client, settings.require_openrouter_key())


async def _run(args: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings.from_env()
    model = args.model or (
        settings.ollama_model if args.provider == "ollama" else settings.openrouter_model
    )
    if not model:
        print("no model given; pass --model or set OLLAMA_MODEL/OPENROUTER_MODEL")
        return 2

    grammar_mode: GrammarMode = args.grammar_mode
    print(f"provider={args.provider} model={model} grammar={grammar_mode}")

    passed = 0
    async with httpx.AsyncClient() as client:
        provider = _build_provider(client, settings, args.provider)
        for case in CASES:
            try:
                analysis = await analyze_article(
                    provider,
                    title=case.title,
                    lead=case.lead,
                    model=model,
                    grammar_mode=grammar_mode,
                )
            except ProviderError as exc:
                print(f"\n{_RED}ERROR{_RESET}  {case.slug}\n  {exc}")
                continue
            if _render(case, analysis, verbose=args.verbose):
                passed += 1

    total = len(CASES)
    colour = _GREEN if passed == total else _RED
    print(f"\n{colour}{passed}/{total} diagnostic cases passed{_RESET}")
    # Exit code reflects the model, not the harness; a sweep is worth starting well
    # before every case passes.
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="model tag; defaults to the configured one")
    parser.add_argument("--provider", choices=("ollama", "openrouter"), default="ollama")
    parser.add_argument(
        "--grammar-mode",
        choices=("schema", "json"),
        default="schema",
        help="schema compiles a sampling grammar; json only requires valid syntax",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print the raw response for every case"
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

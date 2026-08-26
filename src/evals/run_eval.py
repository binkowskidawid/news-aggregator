"""Run the gold set through one configuration and store every call.

One sweep is one ``run_id``. Every axis the evaluation varies is a column on ``analyses``,
so comparing configurations later is a GROUP BY rather than a directory of result files —
which is also why this module deliberately computes nothing. It records; ``metrics.py``
interprets.

    make eval MODEL=gemma4:latest                       # both variants, both grammars
    make eval MODEL=gemma4:latest ARGS="--repeat 5 --limit 5"     # stability
    make eval MODEL=gemma4:latest ARGS="--source-label Interia"   # brand-bias probe

Calls are issued sequentially on purpose. Ollama serves one model at a time on this
hardware, so concurrency would measure queueing rather than the model, and the latency
numbers are half the point of the exercise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass
from typing import Final, get_args

import httpx
from psycopg_pool import AsyncConnectionPool

from analyzer.analyze import analyze_article
from analyzer.prompts import PROMPT_VERSION, InputVariant
from analyzer.providers.base import GrammarMode, ProviderError
from analyzer.providers.factory import build_provider
from analyzer.store import save_analysis
from config import Settings, load_dotenv
from db import fetch_all, pool

INPUT_VARIANTS: Final[tuple[InputVariant, ...]] = get_args(InputVariant)
GRAMMAR_MODES: Final[tuple[GrammarMode, ...]] = get_args(GrammarMode)


@dataclass(frozen=True, slots=True)
class GoldRow:
    article_id: uuid.UUID
    title: str
    lead: str | None
    kind: str


async def load_gold(
    connection_pool: AsyncConnectionPool, limit: int | None, split: str
) -> list[GoldRow]:
    """Read one split of the evaluation set, ordered so a truncated run is still mixed.

    Ordering by kind rather than by insertion means ``--limit 5`` yields articles from
    several categories instead of five neutral ones, which matters because the short runs
    are exactly the ones used for stability and timing probes.

    ``split`` keeps the held-out set out of a main sweep. Without it a run would answer
    over both sets at once, which is the one thing a holdout must never do.
    """
    rows = await fetch_all(
        connection_pool,
        """
        SELECT a.id, a.title, a.lead, g.kind
        FROM gold_articles g
        JOIN articles a ON a.id = g.article_id
        WHERE g.split = %s
        ORDER BY g.kind, a.id
        """,
        (split,),
    )
    gold = [
        GoldRow(article_id=row["id"], title=row["title"], lead=row["lead"], kind=row["kind"])
        for row in rows
    ]
    if limit is not None:
        # Round-robin across kinds rather than head(): see the docstring.
        by_kind: dict[str, list[GoldRow]] = {}
        for row in gold:
            by_kind.setdefault(row.kind, []).append(row)
        interleaved: list[GoldRow] = []
        while len(interleaved) < limit and any(by_kind.values()):
            for bucket in by_kind.values():
                if bucket and len(interleaved) < limit:
                    interleaved.append(bucket.pop(0))
        return interleaved
    return gold


async def _run(args: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings.from_env()

    model = args.model or (
        settings.ollama_model if args.provider == "ollama" else settings.openrouter_model
    )
    if not model:
        print("no model given; pass --model or set OLLAMA_MODEL/OPENROUTER_MODEL")
        return 2

    variants: tuple[InputVariant, ...] = (
        INPUT_VARIANTS if args.input_variant == "both" else (args.input_variant,)
    )
    grammars: tuple[GrammarMode, ...] = (
        GRAMMAR_MODES if args.grammar_mode == "both" else (args.grammar_mode,)
    )

    run_id = uuid.uuid4()
    async with pool(settings.database_url) as connection_pool:
        gold = await load_gold(connection_pool, args.limit, args.split)
        if not gold:
            print(f"gold split {args.split!r} is empty; run `make gold-load` first")
            return 2

        total = len(gold) * len(variants) * len(grammars) * args.repeat
        print(
            f"run_id={run_id}\nprovider={args.provider} model={model} "
            f"prompt={args.prompt_version} variants={variants} grammars={grammars} "
            f"repeat={args.repeat} label={args.source_label or '-'}\n{total} calls"
        )

        done = 0
        failures = 0
        async with httpx.AsyncClient() as client:
            provider = build_provider(client, settings, args.provider, args.num_gpu)
            for grammar_mode in grammars:
                for variant in variants:
                    for row in gold:
                        for _ in range(args.repeat):
                            try:
                                analysis = await analyze_article(
                                    provider,
                                    title=row.title,
                                    # The variant IS the absence of the lead: passing None
                                    # is what makes build_prompt record "title".
                                    lead=row.lead if variant == "title_lead" else None,
                                    model=model,
                                    grammar_mode=grammar_mode,
                                    source_label=args.source_label,
                                    prompt_version=args.prompt_version,
                                )
                            except ProviderError as exc:
                                failures += 1
                                print(f"  ! {exc}")
                                continue

                            await save_analysis(
                                connection_pool,
                                article_id=row.article_id,
                                run_id=run_id,
                                provider=provider.name,
                                model=model,
                                analysis=analysis,
                                source_label=args.source_label,
                            )
                            done += 1
                            marker = "x" if analysis.parse_error else "."
                            print(marker, end="", flush=True)
        print()

        stored = await fetch_all(
            connection_pool,
            """
            SELECT input_variant, grammar_mode, count(*) AS calls,
                   count(*) FILTER (WHERE parse_error IS NOT NULL) AS parse_failures,
                   round(avg(latency_ms)) AS avg_ms,
                   round(avg(tokens_in)) AS avg_tokens_in
            FROM analyses WHERE run_id = %s
            GROUP BY 1, 2 ORDER BY 1, 2
            """,
            (run_id,),
        )

    for summary in stored:
        print(
            f"  {summary['input_variant']:11s} {summary['grammar_mode']:7s} "
            f"calls={summary['calls']:3d} parse_failures={summary['parse_failures']} "
            f"avg={summary['avg_ms']} ms  tokens_in={summary['avg_tokens_in']}"
        )
    print(f"stored {done} call(s), {failures} provider error(s); run_id={run_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="model tag; defaults to the configured one")
    parser.add_argument("--provider", choices=("ollama", "openrouter"), default="ollama")
    parser.add_argument("--input-variant", choices=("title", "title_lead", "both"), default="both")
    parser.add_argument("--grammar-mode", choices=("schema", "json", "both"), default="schema")
    parser.add_argument(
        "--source-label",
        help="portal name to inject into the prompt; only for the brand-bias probe",
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="calls per article, for measuring stability"
    )
    parser.add_argument("--limit", type=int, help="use only N articles, mixed across kinds")
    parser.add_argument(
        "--split",
        choices=("main", "holdout"),
        default="main",
        help="which reference set to sweep; the two are never mixed in one run",
    )
    # Every other axis of the comparison is already a flag; the prompt was the one that
    # still required editing a constant production depends on, which is a poor way to run
    # an ablation. The files in prompts/ are the source of truth for what a version means.
    parser.add_argument(
        "--prompt-version",
        default=PROMPT_VERSION,
        help=f"prompt files to use from prompts/ (default {PROMPT_VERSION})",
    )
    parser.add_argument(
        "--num-gpu",
        type=int,
        help="Ollama GPU layers; 0 forces CPU-only, for the deployment-cost benchmark",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

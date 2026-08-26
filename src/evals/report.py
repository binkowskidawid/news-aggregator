"""Render every stored run into the comparison report.

    make eval-report

Writes ``docs/eval-report.md``. Every threshold it checks against traces to a published
result for Polish rather than to a level this project invented, which is what keeps the
report a test instead of a description of whatever happened — see the anchoring note on
THRESHOLDS.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from config import Settings, load_dotenv
from db import fetch_all, pool
from evals.metrics import Scores, brand_bias, score_all, stability

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
REPORT_PATH: Final = REPO_ROOT / "docs" / "eval-report.md"


@dataclass(frozen=True, slots=True)
class GoldSize:
    """What the report's numbers rest on, read from the database rather than typed.

    The sample size used to be a string literal in the disclaimer. A report that misstates
    its own denominator is worse than one that omits it, and the gold set now grows.
    """

    articles: int
    labels: int
    annotator: str


THRESHOLDS: Final = {
    "quote_fidelity": 0.95,
    "precision_span": 0.60,
    "precision": 0.43,
    "category_accuracy": 0.85,
}
"""Decision levels, each anchored to a source outside this repository.

The original ``precision >= 0.70`` had no such anchor: the specification justified the
*direction* ("precision matters more than recall") without ever justifying the *level*.
Measuring against a number we invented is how a project spends weeks chasing a bar nobody
set.

What the literature says about this task, on Polish:

* Detecting persuasion techniques has low human agreement by nature. Krippendorff's alpha
  is 0.342 for SemEval-2023 Task 3 and 0.404 for CLEF-2024 CheckThat! Task 3 — both under
  the 0.667 the field recommends, both with trained annotators working from 60-page
  guidelines. https://aclanthology.org/2023.semeval-1.317.pdf
* Best published result for Polish is micro F1 **0.430** (KInITVeraAI, SemEval-2023 Task 3,
  Table 9) — and that is the *paragraph-level* task, which asks only whether a technique
  appears somewhere in the passage.
* The span-level task, which is what this project does, is far harder: the winning system
  at CLEF-2024 scored F1 0.092 on English, and a zero-shot XLM-RoBERTa baseline 0.009.
  https://ceur-ws.org/Vol-3740/paper-26.pdf

Hence ``precision >= 0.43``: no worse than the published state of the art for Polish, on
an easier variant of the same problem.

``precision_span`` is the go/no-go bar rather than ``precision`` because the two failures
precision lumps together cost wildly different amounts. Reporting a technique in a neutral
text puts an unfounded accusation next to a named outlet's brand. Marking the right span
and calling it ``emotional_load`` where the annotator wrote ``fear_appeal`` is an imprecise
caption on a defensible highlight. The specification's own justification — "attributing
manipulation where there is none is a reputational and potentially legal problem" —
describes only the first. 0.60 is the level at which the highlight itself holds up.

``recall`` is deliberately absent. It is computed over a stratified sample (all 68 flagged
articles, 25 of 147 neutral ones), so it is inflated by construction, and its denominator
differs between input variants. A threshold on a number that cannot be compared to itself
is not a criterion. It stays in the table as a figure, without a verdict.
"""


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _verdict(value: float | None, threshold: float) -> str:
    if value is None:
        return "—"
    passes = value >= threshold
    # Whole percent is the readable default, but it can round a near miss onto the
    # threshold and render "85% ❌" — which reads as a broken report rather than as
    # 84.7%. Show a decimal exactly when rounding would contradict the verdict.
    misleading = (round(value * 100) >= round(threshold * 100)) != passes
    shown = f"{value:.1%}" if misleading else f"{value:.0%}"
    return f"{shown} {'✅' if passes else '❌'}"


def _config_name(score: Scores) -> str:
    label = f" [{score.source_label}]" if score.source_label else ""
    # The run marker distinguishes sweeps that share a configuration — the main grid, the
    # stability probe and the CPU benchmark all run gemma4/title_lead/schema. The call
    # count is written as "calls/articles" whenever they differ, because a row of repeated
    # calls on five articles otherwise reads as an independent sample of twenty-five.
    size = (
        str(score.calls)
        if score.calls == score.unique_articles
        else f"{score.calls} wyw./{score.unique_articles} art."
    )
    # The tuned rows carry no marker, so the eye is drawn to the held-out ones rather than
    # having to notice the absence of a word.
    split = " **HOLDOUT**" if score.split != "main" else ""
    return (
        f"{score.model_name} · {score.prompt_version} · {score.input_variant} · "
        f"{score.grammar_mode}{label}{split} `{str(score.run_id)[:8]}` ({size})"
    )


def _decision_table(scores: list[Scores]) -> list[str]:
    lines = [
        "| Konfiguracja | Quote fidelity | Precision (typ) | 95% CI | Trafiony fragment "
        "| Recall | Kategoria | FP neutralne |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for score in scores:
        low, high = score.precision_interval
        interval = "—" if score.precision is None else f"{low:.0%} do {high:.0%}"
        lines.append(
            f"| {_config_name(score)} "
            f"| {_verdict(score.quote_fidelity, THRESHOLDS['quote_fidelity'])} "
            f"| {_verdict(score.precision, THRESHOLDS['precision'])} "
            f"| {interval} "
            f"| {_verdict(score.precision_span, THRESHOLDS['precision_span'])} "
            f"| {_pct(score.recall)} "
            f"| {_verdict(score.category_accuracy, THRESHOLDS['category_accuracy'])} "
            f"| {score.neutral_with_findings}/{score.neutral_articles} |"
        )
    return lines


def _diagnostic_table(scores: list[Scores]) -> list[str]:
    lines = [
        "| Konfiguracja | Wywołań | JSON validity | Fuzzy | Puste findings "
        "| Niespójny werdykt | Mediana tokens_in | Mediana tokens_out | Mediana czasu | p95 |",
        "| --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for score in scores:
        lines.append(
            f"| {_config_name(score)} | {score.calls} | {_pct(score.json_validity)} "
            f"| {score.quotes_fuzzy} | {_pct(score.empty_rate)} "
            f"| {score.consistency_errors} "
            f"| {score.median_tokens_in or '—'} | {score.median_tokens_out or '—'} "
            f"| {score.median_latency_ms or '—'} ms | {score.p95_latency_ms or '—'} ms |"
        )
    return lines


def _counts_table(scores: list[Scores]) -> list[str]:
    lines = [
        "| Konfiguracja | TP | FP | FN | TP (conf≥0.7) | FP (conf≥0.7) | Precision @0.7 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for score in scores:
        lines.append(
            f"| {_config_name(score)} | {score.true_positives} | {score.false_positives} "
            f"| {score.false_negatives} | {score.true_positives_confident} "
            f"| {score.false_positives_confident} | {_pct(score.precision_confident)} |"
        )
    return lines


def _variant_pairs(scores: list[Scores]) -> list[Scores]:
    """Keep only configurations a sweep measured under more than one input variant.

    A variant comparison is worth reading exactly when the same run, model, grammar and
    label produced both, because everything except the variant is then held fixed. Rows
    from a sweep that only ever ran one variant would sit in the table looking comparable
    while differing in the model, the date and the prompt as well.
    """
    grouped: dict[tuple[object, ...], list[Scores]] = {}
    for score in scores:
        key = (score.run_id, score.model_name, score.grammar_mode, score.source_label)
        grouped.setdefault(key, []).append(score)

    return [
        score
        for group in grouped.values()
        if len({score.input_variant for score in group}) > 1
        for score in sorted(group, key=lambda s: s.input_variant)
    ]


def _comparable_section(comparable: list[Scores]) -> list[str]:
    """The variant comparison, restricted to articles both variants could reach."""
    pairs = _variant_pairs([score for score in comparable if score.source_label is None])
    lines = [
        "## Porównanie wariantów wejścia na wspólnym podzbiorze",
        "",
        "Te same przebiegi, ale liczone **wyłącznie na artykułach, które mają lead** —",
        "czyli na zbiorze, do którego `title_lead` w ogóle był w stanie dotrzeć. Bez tego",
        "ograniczenia `title` liczy się na całym zbiorze gold, a `title_lead` na jego",
        "podzbiorze, i różnica między nimi zawiera różnicę próbek.",
        "",
        "**Rozstrzyga `trafiony fragment` i `kategoria`.** Recall zostaje nieporównywalny",
        "także tutaj: liczy się z etykiet w polach, które model widział, więc `title_lead`",
        "ma w mianowniku etykiety z leadu, których `title` nigdy nie mógł znaleźć.",
        "",
    ]
    if pairs:
        lines += _decision_table(pairs)
    else:
        lines.append(
            "Brak przebiegu, który zmierzyłby oba warianty przy reszcie konfiguracji "
            'bez zmian (`make eval MODEL=... ARGS="--input-variant both"`).'
        )
    lines.append("")
    return lines


def render(
    scores: list[Scores],
    comparable: list[Scores],
    stability_rows: list[dict[str, object]],
    bias_rows: list[dict[str, object]],
    sizes: dict[str, GoldSize],
) -> str:
    gold = sizes.get("main", GoldSize(articles=0, labels=0, annotator="nieznany"))
    unlabelled = [score for score in scores if score.source_label is None]
    ordered = sorted(unlabelled, key=lambda s: (s.model_name, s.input_variant, s.grammar_mode))

    lines: list[str] = [
        "# Raport ewaluacyjny",
        "",
        "Wygenerowane przez `make eval-report` z tabel `analyses`, `findings`, "
        "`gold_articles`, `gold_labels`.",
        "",
        "## Zastrzeżenia, które muszą towarzyszyć każdej liczbie",
        "",
        "1. **`precision` mierzy zgodność z anotatorem, nie z prawdą.** Zbiór gold oznaczył",
        f"   `{gold.annotator}`. Baseline chmurowy jest modelem innej rodziny niż anotator,",
        "   i to jest jedyne zabezpieczenie przed mierzeniem samego siebie.",
        f"2. **Zbiór strojony: {gold.articles} artykułów i {gold.labels} etykiet.**"
        + (
            f" **Holdout: {sizes['holdout'].articles} artykułów i "
            f"{sizes['holdout'].labels} etykiet**, anotowany po zamknięciu strojenia,"
            " z dni, których tamten zbiór nie obejmuje."
            if "holdout" in sizes
            else ""
        ),
        "   Liczebności podane osobno, bo sześć wersji promptu zmierzono na zbiorze",
        "   strojonym i wybrano z nich najlepszą — jego liczba jest z tego powodu zawyżona",
        "   o nieznaną wielkość, a holdout jest jedyną, która tego obciążenia nie ma.",
        "   Przedziały ufności są szerokie i podane wprost. Wynik blisko progu należy czytać",
        "   jako brak rozstrzygnięcia.",
        "3. **Etykiety są wyczerpujące od 18.08**: oznaczana jest każda obecna technika, nie",
        "   najwyraźniejsza. Wcześniejsza polityka zaniżała precision, bo model dostawał karę",
        "   za trafienia, których anotator nie zapisał. Liczby sprzed tej zmiany nie są",
        "   porównywalne z tymi.",
        "4. **Dopasowanie finding↔etykieta wymaga zgodnego typu, zgodnego pola i nachodzenia",
        "   zakresów znakowych.** Identyczne offsety byłyby zbyt surowe, pominięcie zakresu",
        "   zbyt łagodne.",
        "5. **Recall liczony jest wyłącznie z etykiet w polach, które model widział.**",
        "   Wariant `title` nie jest karany za nieznalezienie techniki ukrytej w leadzie.",
        "   **Konsekwencja: recall NIE jest porównywalny między wariantami** — mianowniki są",
        '   różne. Do porównania wariantów służy precision i „trafiony fragment", liczone',
        "   na tym samym zbiorze zgłoszeń.",
        "6. **Wiersze opisane jako `N wyw./M art.` to przebiegi z powtórzeniami** (stabilność,",
        "   benchmark CPU). Wilson CI zakłada obserwacje niezależne, więc **ich przedziały są",
        "   sztucznie wąskie** i nie należy ich zestawiać z przebiegiem głównym.",
        "",
        "## Metryki decyzyjne (spec §8.2)",
        "",
        "**Go/no-go:** quote fidelity ≥ "
        f"{THRESHOLDS['quote_fidelity']:.2f} · trafiony fragment ≥ "
        f"{THRESHOLDS['precision_span']:.2f}. Te dwie mierzą, czy podświetlony cytat jest",
        "obroniony — czyli ryzyko, które ponosi operator wobec nazwanej redakcji.",
        "",
        "**Jakość opisu:** precision z typem ≥ "
        f"{THRESHOLDS['precision']:.2f} · trafność kategorii ≥ "
        f"{THRESHOLDS['category_accuracy']:.2f}. Pomyłka w nazwie zabiegu przy poprawnie",
        "wskazanym fragmencie jest nieścisłością, nie zarzutem bez pokrycia.",
        "",
        "Poziom 0,43 to opublikowany state of the art dla polskiego: micro F1 **0,430**",
        "(KInITVeraAI, SemEval-2023 Task 3, tabela 9) — i to na *łatwiejszym* wariancie",
        "zadania, bo klasyfikacja akapitu, nie lokalizacja fragmentu. Na wariancie",
        "spanowym, którym jest ten produkt, najlepszy system CLEF-2024 dał F1 0,092.",
        "Progi nie pochodzą z tego raportu — patrz `THRESHOLDS` w `src/evals/report.py`.",
        "",
        "**Recall celowo bez werdyktu.** Liczony na próbce warstwowanej (68 z 68",
        "oflagowanych, 25 ze 147 neutralnych), więc zawyżony z konstrukcji, a jego mianownik",
        "różni się między wariantami wejścia. Zostaje jako liczba, nie jako kryterium.",
        "",
        *_decision_table(ordered),
        "",
        "Rozbicie na zliczenia, bo przy tej wielkości próby ułamki mylą:",
        "",
        *_counts_table(ordered),
        "",
        *_comparable_section(comparable),
        "## Metryki diagnostyczne (konfiguracja, nie jakość)",
        "",
        "Przy wymuszonym schemacie `json_validity` mierzy poprawność naszej konfiguracji,",
        "nie zdolność modelu do trzymania formatu. `tokens_out` równe 1024 oznacza, że model",
        "regularnie uderza w sufit generowania.",
        "",
        "**Niespójny werdykt** to analiza, w której `overall_assessment` przeczy zgłoszeniom",
        "zapisanym obok — prompt każe liczyć ocenę wyłącznie z pozycji o `confidence ≥ 0.7`,",
        "więc `neutral` przy takiej pozycji i ocena nie-neutralna bez żadnej to złamanie",
        "reguły, którą model dostał. Rejestrowane, nie naprawiane: przepisanie oceny pod",
        "zgłoszenia skasowałoby jedyny ślad po tym, że model jej nie stosuje.",
        "",
        *_diagnostic_table(ordered),
        "",
    ]

    lines += ["## Stabilność przy powtórzeniach", ""]
    if stability_rows:
        lines += [
            "| Model | Wariant | Gramatyka | Artykułów | Powtórzeń | Średni rozrzut "
            "| Najgorszy | Zmiany oceny ogólnej |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in stability_rows:
            lines.append(
                f"| {row['model_name']} | {row['input_variant']} | {row['grammar_mode']} "
                f"| {row['articles']} | {row['repeats']} | {row['avg_spread']} "
                f"| {row['worst_spread']} | {row['assessment_flips']} |"
            )
    else:
        lines.append(
            "Brak przebiegów z powtórzeniami tej samej konfiguracji "
            '(`make eval MODEL=... ARGS="--limit 5 --repeat 5"`).'
        )
    lines.append("")

    lines += [
        "## Test biasu marki",
        "",
        "Ten sam tekst, ta sama konfiguracja; różni się wyłącznie nazwa portalu wstrzyknięta",
        "do promptu. Różnica w kolumnie `findings/artykuł` jest efektem samej marki.",
        "",
    ]
    if bias_rows:
        lines += [
            "| Model | Etykieta w prompcie | Przebieg | Wywołań | Artykułów | Findings "
            "| Na artykuł | heavily_loaded |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in bias_rows:
            lines.append(
                f"| {row['model_name']} | {row['label']} | `{str(row['run_id'])[:8]}` "
                f"| {row['calls']} | {row['articles']} | {row['findings']} "
                f"| {row['per_article']} | {row['heavily']} |"
            )
    else:
        lines.append("Brak przebiegów z etykietą portalu.")
    lines.append("")

    labelled = [score for score in scores if score.source_label is not None]
    if labelled:
        lines += [
            "Metryki jakości dla przebiegów z etykietą, dla porównania z przebiegiem bez niej:",
            "",
            *_decision_table(sorted(labelled, key=lambda s: str(s.source_label))),
            "",
        ]

    return "\n".join(lines) + "\n"


async def _run(_: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings.from_env()

    async with pool(settings.database_url) as connection_pool:
        scores = await score_all(connection_pool)
        if not scores:
            print("no stored analyses; run `make eval MODEL=...` first")
            return 2
        comparable = await score_all(connection_pool, comparable_only=True)
        stability_rows = await stability(connection_pool)
        bias_rows = await brand_bias(connection_pool)
        rows = await fetch_all(
            connection_pool,
            """
            SELECT g.split,
                   count(DISTINCT g.article_id) AS articles,
                   count(l.*) AS labels,
                   string_agg(DISTINCT g.labeled_by, ', ') AS annotator
            FROM gold_articles g
            LEFT JOIN gold_labels l ON l.article_id = g.article_id
            GROUP BY g.split ORDER BY g.split DESC
            """,
        )
        # Per split, never summed: the tables hold a held-out set beside the tuned one,
        # and a single total would describe neither.
        sizes = {
            row["split"]: GoldSize(
                articles=row["articles"],
                labels=row["labels"],
                annotator=row["annotator"] or "nieznany",
            )
            for row in rows
        }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        render(scores, comparable, stability_rows, bias_rows, sizes), encoding="utf-8"
    )
    print(f"{REPORT_PATH.relative_to(REPO_ROOT)} written ({len(scores)} configurations)")

    for score in sorted(scores, key=lambda s: (s.model_name, s.input_variant, s.grammar_mode)):
        print(
            f"  {_config_name(score):64s} fidelity={_pct(score.quote_fidelity):>5s} "
            f"P={_pct(score.precision):>5s} R={_pct(score.recall):>5s} "
            f"cat={_pct(score.category_accuracy):>5s}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

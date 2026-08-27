# Model card

What this system reports, how well it does it, and what was never measured.

The numbers below describe **one configuration measured on Polish press**. They are not a
property of this software. If you change the model, the prompt, or the language of your
sources, they stop applying and nothing in the code will tell you.

## The configuration these numbers describe

| | |
| --- | --- |
| Model | `gemma4:latest` (9.6 GB), served by Ollama on the operator's own machine |
| Prompt | `prompts/system-v1.1.3.txt`, `PROMPT_VERSION = v1.1.3` |
| Input | article title, plus the lead where the source provides one (`input_variant = title_lead`) |
| Output | JSON constrained by the schema generated from `AnalysisResult` (`grammar_mode = schema`) |
| Context window | `num_ctx = 16384`, output ceiling 4096 tokens |
| Language | Polish. The prompt, its four few-shot examples and every measurement are Polish |

The model is **never told which outlet published the text**. With outlets of opposing
political profiles on one list, brand recognition could shift thresholds on identical
language; a probe measured exactly that and the effect was real (see "Known limitations").

## What it produces

For each article: one topic category, one overall assessment, and zero or more findings.
A finding is a verbatim quote from the article, a technique name from a fixed taxonomy of
six, a confidence value, and a one-sentence explanation in Polish.

**Every quote is verified against the source text before it is stored.** A quote that cannot
be located is discarded, always — there is no repair heuristic. This is the one guarantee the
system makes unconditionally: it cannot attribute to an outlet a sentence the outlet never
published. It is also the only defence against prompt injection that does not depend on the
model's cooperation.

## How the measurement was made

Two disjoint annotated sets, both sampled from what the pipeline actually fetches rather
than picked by hand — hand-picking was measured against sampling on the same run and gave
67% precision versus 35%, i.e. it measured the selection, not the model.

| set | articles | labels | role |
| --- | --- | --- | --- |
| main | 118 | 74 | prompt development; six prompt versions were compared on it |
| **holdout** | 80 | 53 | never used for tuning; **the binding numbers come from here** |

**`eval/gold_set.csv` in this repository holds 111 articles and 72 labels, not 118 and 74.**
Seven articles from one publisher were removed before publication: that outlet reserves text
and data mining rights, so the project does not collect from it and does not redistribute it
either (see COMPLIANCE.md). The measurement was taken before they were removed and the
figures below are **not** recalculated — restating them against a set nobody ran would be
inventing a result. The holdout is untouched and contains no article from that publisher,
which is why every binding number is unaffected.

Annotation is exhaustive — every technique present is labelled, not the clearest one.
The annotator is a language model whose disputed calls were reviewed by a human; the
disputes and their resolutions are recorded outside this repository.

## Results

Holdout, `title_lead`, run `2b71e25c` (64 articles with a lead, 25 TP / 35 FP / 17 FN):

| metric | value | what it means |
| --- | --- | --- |
| Quote fidelity | **100%** | every stored quote was found verbatim in the source |
| Underline lands on the right fragment | **47%** | of the fragments underlined, this share overlaps something an annotator marked |
| Fragment **and** technique both right | **42%** (95% CI 30–54) | the full claim the interface makes next to each underline |
| Topic category | **86%** | unrelated to findings; this is the feed's filter |
| Recall | 60% | diagnostic only — computed on a stratified sample, so it is inflated by construction |
| Findings on articles annotated neutral | 12 articles / 25 | the metric with the highest legal stake |

The same prompt on the **main** set (run `7cf055db`, 100 articles) gives precision 46%, span
56%, category 96%, fidelity 96%. Those numbers are higher because six prompt versions were
compared on that set and the best was kept — the gap between the two columns is the size of
that selection effect, roughly ten points. **Quote the holdout column.**

Fidelity 96% on the main set is the validator working, not a leak: `quote_fidelity` is
`1 − rejected/total`, so 96% means 4% of the model's quotes were thrown away before storage.

### How to read 42%

There is no external standard for this task, so the honest comparison is with published
results on the same problem:

- **Human agreement on manipulation-technique annotation is low by nature.** Krippendorff's
  α = 0.342 (SemEval-2023) and 0.404 (CLEF-2024), against a literature recommendation of
  0.667 — with trained annotators working from 60-page guidelines.
- **Paragraph-level classification for Polish**: KInITVeraAI, the best published system,
  reached micro F1 **0.430** (SemEval-2023).
- **Span-level detection**, which is what this system does and what the interface shows:
  the best CLEF-2024 system reached F1 **0.092** (English), 0.123 (Slovenian). A zero-shot
  XLM-RoBERTa baseline reached 0.009.

So 42% sits at the level of published state of the art for the easier variant of the task,
and the 47% span figure is far above published span-level results — while still meaning that
**more than half of what this system underlines is wrong**. Both statements are true and the
product is only usable by someone holding both.

### What did not help

Measured and rejected, each with a full run rather than an offline estimate:

- **A cloud model.** `qwen3-235b-a22b` scored *worse* than the local 9.6 GB model on the
  same 118 articles (precision 33–41%, category 76%). `deepseek-v4-flash` returned an
  assessment with zero supporting findings on 121 articles and was disqualified.
- **Rewriting the prompt.** Three versions with few-shot examples drawn from real articles
  all moved precision 46% → 37–40%. Invented examples beat real ones.
- **A second verification pass** over each finding. On the main set it looked like it
  cleared the bar; on the holdout the gain was an artefact of the inflated baseline. It
  killed 15% of false positives where 50% was needed, and never once killed the technique
  responsible for most of them.
- **Training a dedicated encoder.** The winning CLEF-2024 architecture *is* that, and it
  scored 0.092.

## What was never measured

- **Whether the explanations are correct.** Labels record technique, field and offsets —
  nothing about the sentence of prose shown next to each finding. What is known: none are
  empty (0 of 1418), median length ~100 characters, 89.9% unique. Whether they are *true* is
  unmeasured, and it is the part a reader actually reads.
- **Full article bodies.** Only title and lead are analysed. CLEF-2024 annotated full
  articles and span-level results there are worse, so length is not an obvious fix.
- **Any language other than Polish.** The interface is available in English. That is
  translation of the interface, not evidence about the model. An English-language source
  will produce output of a quality nobody has measured, and the English interface over it
  will look like support that does not exist.

## Known limitations

- **Findings are skewed towards one technique.** `emotional_load` is 316 of 609 findings in
  production and 51 of 74 labels in the main set. Per-technique precision can only be
  estimated for that one; `fear_appeal` has 2 labels, `overgeneralization` 3.
- **Confidence is not calibrated and is not shown.** The model returns 0.85–0.92 on almost
  every finding while fewer than half hold up. It is stored, never displayed.
- **The schema does not guarantee escaping.** 3 responses in 738 (0.4%) put a quoted word in
  straight quotes inside `explanation`, ending the JSON string early. Such a response is
  recorded as failed, never repaired — a parse failure under a grammar is a configuration
  fault, and patching the JSON would hide it.
- **A verdict can contradict its own findings.** `overall_assessment` is meant to follow from
  `findings`; in 1.5–5.1% of analyses (varies by prompt version) it does not. This is
  recorded in `analyses.consistency_error`, not corrected.
- **The corpus behind these numbers is two days of Polish news** (17–18.08.2026 for the main
  set, 19–20.08 for the holdout), stratified by day and outlet. Stratification damps the
  dominance of a single news event; it does not remove it.
- **One outlet's titles are listing cards, not article headlines.** For TV Republika the
  title is the text of the listing card, which for large slots is a teaser rather than the
  H1. It is still that broadcaster's editorial language, but it is not the same object as an
  RSS headline.

## If you change anything

Any of these invalidates every number above:

| change | why |
| --- | --- |
| a different model | measured directly: the lead helps `gemma4` (41 → 46%) and *hurts* `qwen3-235b` (41 → 33%). Input variant is a property of the model, not of the task |
| a different prompt version | bump `PROMPT_VERSION`, then re-run the evaluation; every axis that varies is a column in `analyses`, so a comparison is a `GROUP BY` |
| sources in another language | the prompt is Polish, its examples are Polish, and no non-Polish measurement exists |
| a lower `num_ctx` | at 8192 llama.cpp silently slides the window and drops the start of the system prompt. `OllamaProvider` raises rather than allow it |

The accuracy figures are also written into the interface, in
`web/messages/{pl,en}.json` under `reliability`. They are prose in a translation catalogue —
nothing in the code can detect that they have gone stale. **Replace them yourself, or remove
them.**

## Cost

Measured, on the configuration above: ~5.5 s per article on a laptop, ~21.4 s on a server
without a GPU — 500 articles in about 3 hours. Median prompt 4033 tokens in, 102 out.
Prefill dominates: the article text is short, the instructions are not.

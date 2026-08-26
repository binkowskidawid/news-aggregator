# news-aggregator

[![CI](https://github.com/binkowskidawid/news-aggregator/actions/workflows/ci.yml/badge.svg)](https://github.com/binkowskidawid/news-aggregator/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.14-blue.svg)](pyproject.toml)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18-blue.svg)](migrations/)
[![Next.js](https://img.shields.io/badge/Next.js-16.3-black.svg)](web/package.json)
[![Checked](https://img.shields.io/badge/checked-ruff%20%2B%20mypy%20strict-black.svg)](Makefile)

[![Model](https://img.shields.io/badge/model-local%20via%20Ollama-success.svg)](MODEL_CARD.md)
[![Underline correct](https://img.shields.io/badge/underline%20correct-47%25-orange.svg)](MODEL_CARD.md)
[![Underline and technique correct](https://img.shields.io/badge/underline%20%2B%20technique%20correct-42%25-orange.svg)](MODEL_CARD.md)
[![Quotes verbatim](https://img.shields.io/badge/quotes%20verbatim-100%25-success.svg)](MODEL_CARD.md)

Fetches Polish news articles, classifies them by topic, and reports specific language
techniques in the headline and lead — each with the verbatim quote it is about.

Every model call runs on hardware you control. The default model is a 9.6 GB local one
served by Ollama; a cloud baseline exists and was measured to be *worse*.

## What it claims, and how often it is right

Measured on 64 articles the prompt was never tuned against, under the configuration this
repository ships:

| | |
| --- | --- |
| The underline lands on a fragment an annotator also marked | **47%** |
| …and the technique named beside it is right as well | **42%** |
| Quotes found verbatim in the source | **100%** |
| Topic category | **86%** |

**More than half of what this system underlines is wrong.** That is the number to design
around, not a defect to be fixed later — the published state of the art for span-level
detection of these techniques is F1 **0.092** (CLEF-2024), and for paragraph-level
classification in Polish, micro F1 **0.430** (SemEval-2023). Human annotators agree with
each other at Krippendorff's α ≈ 0.34–0.40 on this task.

So: this is a tool for *directing attention at a sentence*, and it is wrong often enough
that every reading has to end at the source. It is not a tool for judging a newsroom, and
the interface never makes a claim about one — only about a sentence.

Full conditions, limitations, and what was never measured: **[MODEL_CARD.md](MODEL_CARD.md)**.

## Requirements

- Python 3.14 and [uv](https://docs.astral.sh/uv/)
- Docker (PostgreSQL 18 — `uuidv7()` is required)
- [Ollama](https://ollama.com) running **natively on the host**, not in a container
- Node 22+ and pnpm, for the front end

```bash
ollama pull gemma4:latest
```

## Run it

```bash
cp .env.example .env      # POSTGRES_PASSWORD, CONTACT_EMAIL; COOKIE_SECURE=0 for local http
make sync                 # Python dependencies
cd web && pnpm install && cd ..

make up && make migrate   # PostgreSQL 18 + schema + source rows
make dev                  # API on :8000, front end on :3000, Ctrl-C stops both
```

Nothing is collected yet: **every source ships disabled**, because not collecting is
reversible by one command and collecting is not. Pick your own:

```bash
make sources                        # what is configured, and which sources reserve TDM rights
make source-enable NAME='Interia'
make ingest                         # one pass over the active sources
make analyze                        # drain the queue of pending articles
make pipeline                       # both, unattended, on their own intervals
```

Deciding which sources you may collect from is yours to make and yours to answer for.
See [COMPLIANCE.md](COMPLIANCE.md) and [OPERATOR.md](OPERATOR.md) before enabling anything.

## Develop

```bash
make check       # ruff + ruff format + mypy strict + pytest
make web-check   # tsc + eslint + node:test
make help        # every available command
```

Tests assert properties, not transcripts — temperature 0.1 is not determinism. The quote
validator carries the heaviest coverage, because it is the mechanism everything else rests
on: **a quote that cannot be found in the source text is discarded, always.** Never add a
heuristic that repairs one.

## The prompt is Polish

The prompt, its examples, and every measurement in this repository are Polish. The interface
speaks Polish and English; the model's own sentences are passed through untranslated and
marked as Polish.

Point this at a source in another language and you will get output whose quality nobody has
measured, under an English interface that looks like support which does not exist. Adding a
language means a separate prompt file and its own annotated set — not a translated string.

## Documentation

| | |
| --- | --- |
| [MODEL_CARD.md](MODEL_CARD.md) | what was measured, how, and what was not |
| [OPERATOR.md](OPERATOR.md) | running it: sources, the pipeline, accounts, changing the model |
| [COMPLIANCE.md](COMPLIANCE.md) | transparency, TDM reservations, personal data, defamation |
| [SECURITY.md](SECURITY.md) | reporting a vulnerability |
| [CONTRIBUTING.md](CONTRIBUTING.md) | what a pull request needs, and the five things this project will not accept |
| [CHANGELOG.md](CHANGELOG.md) | what changed, including changes to what the software claims |

## Contributing

Pull requests are welcome; one maintainer, no service-level agreement. Read
[CONTRIBUTING.md](CONTRIBUTING.md) first — particularly the list of changes that will be
declined however well they are written, because each one is holding something up.

A finding that is simply wrong is not a bug, and the issue templates say so. It is still worth
reporting as a *misclassified fragment*, provided you can argue what the right answer was.

## Citing this

The measurements describe one configuration. [CITATION.cff](CITATION.cff) carries the
metadata; [MODEL_CARD.md](MODEL_CARD.md) carries the conditions.

## Licence

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Source code only: no model weights,
no article text, no corpus.

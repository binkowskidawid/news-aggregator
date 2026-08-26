## What this changes, and why

<!-- The problem, not the diff. A reviewer can read the diff. -->

## Checks

- [ ] `make check` passes
- [ ] `make web-check` passes
- [ ] New behaviour has a test that fails without the change

## If it touches the analysis contract

<!-- Delete this section if it does not. -->

- [ ] `AnalysisResult`, the `CHECK` constraint, and the prompt text all moved together
- [ ] A migration handles rows already stored under the old vocabulary

## If it touches a prompt

<!-- Delete this section if it does not. -->

- [ ] `PROMPT_VERSION` bumped — no wording change ships without one
- [ ] A run backs the change, and the numbers are in the description below
- [ ] No file removed from `prompts/`; stored analyses point at them by version
- [ ] If the published figures moved, `MODEL_CARD.md` and `web/messages/` moved with them

## Anything a reviewer should push back on

<!-- Shortcuts taken, cases not handled, a decision you are unsure about. Naming it here is
     cheaper for everyone than having it found. -->

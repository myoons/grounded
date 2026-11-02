# Documentation

## Start here

| Goal | Document |
| --- | --- |
| Understand the product | [Overview](../README.md) |
| Follow one utterance through the system | [System guide](SYSTEM_GUIDE.md) |
| Inspect the implementation | [Code review guide](REVIEW_GUIDE.md) |
| Run the app and GPU worker | [Setup and operations](RUNNING.md) |
| Assess tests and model results | [Validation](VALIDATION.md) |
| See the architecture | [Visual atlas](VISUAL_ATLAS.md) |

## Architecture and reliability

- [Durable audio](DURABLE_AUDIO.md): encrypted retention, acknowledgements and recovery.
- [Relational schema](HISTORY_SCHEMA.md): meeting-scoped relationships and migrations.
- [Meeting history](MEETING_HISTORY.md): archive behavior, persistence and retention.
- [Research pipeline](RESEARCH_PIPELINE.md): concurrency, revisions and evidence validation.
- [Capture state](CAPTURE_STATE.md): operation ownership and asynchronous shutdown.
- [Capacity policy](CAPACITY_POLICY.md): measured bounds and independent AI budgets.
- [Observability](OBSERVABILITY.md): content-free event correlation and diagnostics.
- [Security and privacy](SECURITY.md): trust boundaries and data handling.
- [GPU transcription](GPU_SPEECH.md): custom Runpod worker and model contract.

## Evaluation and design evidence

- [Design experiments](DESIGN_EXPERIMENTS.md): five targeted comparisons and resulting decisions.
- [Extraction evaluation](CLAIM_PROMPT_EVALUATION.md): frozen cases, prompt comparison and failures.
- [Claim lifecycle evaluation](CLAIM_LIFECYCLE_EVALUATION.md): repetition, correction and withdrawal.
- [Answer evaluation](SYNTHESIS_GOLD_EVALUATION.md): controlled evidence and scoring limitations.
- [Search-page filtering](SEARCH_PAGE_FILTER.md): rejected error pages and negative controls.

Evaluation outputs under `evaluations/` contain synthetic fixtures and recorded model
results. They are separated from real meeting data and are not an accuracy guarantee.

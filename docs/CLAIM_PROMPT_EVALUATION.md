# Claim extraction: prompt review and gold-set evaluation

Historical prompt-comparison experiment. A later density experiment changed the
production output cap and token budget; its separate current-adapter regression is
recorded in [capacity policy](CAPACITY_POLICY.md). Do not attribute the historical
scores below to the newer configuration.

## Decision in one minute

**Keep the existing English production prompt.** An English revision with an explicit
decision order and three development-derived examples did not demonstrate an improvement.
We retain the rejected revision and every measured result rather than changing labels
until a prompt appears perfect. This is an extraction evaluation, not a fact-checking
accuracy evaluation or an end-to-end audio benchmark.

On November 2, 2026 (Korea time), the production prompt passed **19 of 20** new synthetic
held-out cases in one run. The revision passed **37 of 40** observations over two runs
of those same 20 cases. Neither passed the complete held-out set.

## What was reviewed

The existing prompt already separates instructions from untrusted transcript data,
defines six eligible categories and four exclusions, gives lifecycle action rules,
requires supplied evidence IDs, and explicitly permits an empty result. The application
also requests strict JSON Schema and validates the response independently. The revision
added an ordered decision checklist and examples for opinions, predictions and repeats.
It changed no model, schema, timeout, audio capture, worker settings or production session.

The review used primary guidance, not an assertion that any provider's advice guarantees
Kimi performance:

- [Anthropic: prompt engineering overview](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview)
  recommends defining success criteria and empirical tests before tuning. We established
  the dataset and scoring contract first.
- [Anthropic: define success and build evaluations](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests)
  emphasizes specific, measurable, task-relevant criteria. We score count, action, category
  and evidence linkage separately from prose quality.
- [Kimi: API overview](https://www.kimi.ai/help/kimi-api/api-overview)
  recommends clear instructions and representative examples. We tested short examples
  from existing development cases, rather than inserting held-out answers into the prompt.
- [Google: structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
  describes schema-constrained extraction. Schema conformity is a structural property;
  it does not establish semantic correctness. Grounded uses its existing Runpod schema
  integration, not Gemini, and keeps local validation.

## Dataset and scoring

- **Development:** 18 pre-existing synthetic cases reused from
  [`evaluate_meeting_detection.py`](../scripts/evaluate_meeting_detection.py).
  These are regression cases, not unseen data.
- **Held-out:** 20 new English cases in [`claim_gold.json`](../tests/data/claim_gold.json).
  Dataset SHA256: `38822990bc006484d575aa486cc30f4f9b315c930624397abf0be0b5dd5e5bbb`.
  Inputs and expected labels were frozen before baseline calls and the prompt revision;
  held-out results were inspected only after launching the frozen revision evaluation.
  No label or prompt was subsequently tuned to these results. Future tuning against
  these cases would make them regression data, requiring a fresh hold-out.
- Coverage includes capability, limits/pricing, policy/licensing, performance comparison,
  technical behavior and current status; each of the four excluded types; all exclusions
  together; eligible and excluded content together; two claims in one dialogue;
  antecedents; repetition; already-linked evidence; correction; retraction; withdrawal
  before extraction; and transcript instructions attempting to override the detector.
- Exact case success requires the expected number of changes and a one-to-one match
  of action, category, candidate ID and required utterance IDs, independent of order.
  All emitted evidence IDs must exist. Missing changes and extra changes fail.
  A repeat requiring a link cannot pass merely by returning an empty list.
- The old evaluator remains available for historical reproducibility. Its broader
  action-presence checks are not equivalent to this stricter scorer.

This is a small, agent-authored, synthetic development artifact, **not an independently
annotated benchmark**. Programmatic matching does not judge statement fidelity, question
quality, relevance of every additional citation, or factual truth. Manual inspection of
held-out statements found the expected subjects retained, but is not a blinded expert
semantic evaluation. ASR errors, long-context truncation, accents and real meeting
distribution are outside this test.

## Actual results

| Prompt | Split | Exact cases passed | Median request latency | Maximum |
|---|---|---:|---:|---:|
| Production baseline | Development, one run | 18/18 | 8.281 s | 15.116 s |
| Production baseline | Held-out, one run | 19/20 | 8.758 s | 31.963 s |
| Experimental revision | Development, one run | 18/18 | 7.888 s | 14.134 s |
| Experimental revision | Held-out, two runs | 37/40 | 8.350 s | 22.040 s |

All 96 requests completed without a recorded transport/schema error. Each invocation
used concurrency 3; overlapping invocations allowed up to 6 independent requests in flight.
The production single-flight detector was not changed. Latency starts after semaphore
acquisition and includes the model request/polling, not time waiting for an evaluation
slot. These small, overlapping batches do not establish a latency improvement or SLA.

Configuration: Runpod public `moonshot-kimi` endpoint, `kimi-k3`, reasoning effort `low`,
maximum output tokens 1200, provider-default temperature, strict schema, 55-second client
timeout, 45-second execution timeout. No fixed seed was supplied. Reports contain model
usage, prompt and dataset hashes, every output and failures; no credentials or real speech.

### Failures and label uncertainty

1. Baseline: the standalone Node.js 18 end-of-life claim produced no change. The same
   status claim inside a two-claim dialogue was extracted. This is a false negative
   under the frozen rubric, not proof that all current-status claims fail.
2. Revision, first run: the S3 object-size claim produced no change. It passed on the
   second run, illustrating nondeterminism.
3. Revision, both runs: a correction from maximum object size to a single-PUT request
   limit did not produce exactly one update. One run created a new claim; another
   created one and updated the old one. **The gold label is itself debatable:** those
   can be interpreted as distinct question identities. We keep the frozen failure
   count and disclose the ambiguity rather than silently relabeling it as a pass.
   A future dataset should include both unequivocal scope refinements and independently
   reviewed identity-changing corrections.

All four isolated exclusion cases and the all-exclusions dialogue passed in each
measured held-out run. That is useful evidence, not a guarantee against future false
positives. There is no statistically established winner from this small experiment.
The conservative release choice is therefore to leave the production prompt unchanged.

## Reproduce

From the repository root, with the existing mode-600 Runpod credential file:

```sh
uv run python -m scripts.evaluate_claim_gold --split dev --output /path/to/dev.json
uv run python -m scripts.evaluate_claim_gold --split heldout --repeat 2 --output /path/to/heldout.json
uv run python -m scripts.evaluate_claim_gold --split heldout --repeat 2 --prompt-file tests/data/claim_prompt_candidate.txt --output /path/to/candidate.json
uv run python -m unittest tests.test_claim_gold tests.test_meeting_detector -q
```

Real-provider commands incur charges. Concurrency defaults to 3 and is bounded to 1–4.
Failed cases yield exit code 1 **after saving all observations**. No automatic rerun
discards failures. The prompt-file option is confined to this evaluator process.

Evidence: [baseline development](evaluations/claim-gold/baseline-dev.json),
[baseline held-out](evaluations/claim-gold/baseline-heldout.json),
[revision development](evaluations/claim-gold/revised-dev.json),
[revision held-out](evaluations/claim-gold/revised-heldout.json),
[experimental prompt](../tests/data/claim_prompt_candidate.txt).

**Accurate presentation wording:** “We evaluated the English extractor against a frozen
synthetic set covering six claim categories, four exclusions and state transitions.
The production prompt passed 19/20 held-out cases in the recorded run. A prompt revision
did not establish an improvement, so we retained the baseline and documented the misses.”

Do not say “the prompt passed the entire test set,” “95% production accuracy,” or
“strict JSON guarantees correct claims.”

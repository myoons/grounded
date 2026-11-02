# Claim lifecycle: action selection and scope fidelity

This extends [the original gold evaluation](CLAIM_PROMPT_EVALUATION.md) without changing
its dataset, labels, scores or production prompt. The purpose is to examine changes to
existing claims, rather than merely whether a new card appears.

## Recorded result

On November 2, 2026 (Korea time), the unchanged production English prompt was evaluated
on **10 new synthetic cases, twice each**, using the real Runpod `kimi-k3` API.

- Structural action/category/candidate/evidence/count checks: **19/20**.
- Combined structural checks and explicit scope-fidelity proxies: **18/20**.
- Fidelity proxies apply to four cases: **6/8** observations passed those additional checks.
- All 20 calls completed without a recorded transport or schema error.
- No prompt tuning or relabeling followed the observations.

| Scenario | Combined pass count |
|---|---:|
| New repetition links to the existing claim | 2/2 |
| Already-linked repetition produces no change | 2/2 |
| Explicit correction of existing scope produces update | 2/2 |
| Clearly independent claim produces create | 2/2 |
| Explicit withdrawal retracts existing claim | 2/2 |
| Withdrawal before first extraction produces no card | 2/2 |
| Correction before first extraction keeps final premise | 1/2 |
| Repetition does not resurrect a retracted claim | 2/2 |
| Three-turn scope refinement retains intended scope | 1/2 |
| Paraphrase of corrected scope links without update | 2/2 |

The contrast pair keeps the same existing SQLite candidate: an explicit same-host
correction should update it; a separately introduced foreign-key capability should create
another claim. This avoids the earlier S3 object-limit versus single-PUT identity ambiguity.

## Why schema and action checks are not enough

One three-turn output correctly selected `update`, the expected category and existing ID,
and all required utterance IDs. But its statement retained **“works across a network
filesystem”** while adding a same-host condition. The frozen lexical proxy rejected the
old positive scope. An action-only score would have reported a pass.

The second failure returned no change after an explicit correction before first extraction.
The other run of each case passed. This demonstrates inconsistency, not universal failure
of correction handling.

The multi-turn input says to narrow the earlier assertion rather than explicitly negating
every part of it. A reader could interpret the old network-filesystem scope as retained.
The rubric deliberately expects the corrected same-host premise alone; the recorded failure
is therefore **rubric-relative**, not definitive proof of a factual hallucination. The
explicitly worded `scope_update` case, including “not across network filesystems,” passed
both times. This distinction is preserved rather than silently repairing the test after use.

## Fidelity rubric and limits

For the four relevant scenarios, statement plus conditions must retain the expected
subject, contain one allowed scope phrase, and not contain a forbidden old-scope phrase.
These are explicit lexical checks, not an LLM judging itself. They can miss paraphrased
errors or reject valid wording/negation, so they are **not a general semantic accuracy score**.
All recorded statements and conditions were also inspected against the input, but this
was agent review, not independent, blinded human adjudication.

The new dataset is [claim_lifecycle_gold.json](../tests/data/claim_lifecycle_gold.json),
SHA256 `89650c776491999aa9e3d7d38b8f2f14c08383aae69575b3ceed295620cee7cf`.
It was fixed before these calls. The original hold-out remains untouched. These cases are
small synthetic snapshots with supplied candidate state, not an uninterrupted live meeting
with model-generated state carried between requests. Multi-turn inputs are chronological
dialogues; the deterministic integration test below separately exercises actual state transitions.

## Real-service integration test

[`test_claim_lifecycle.py`](../tests/test_claim_lifecycle.py) uses the actual `MeetingService`
and controlled fake external providers. It drives create → link → scope update → repeated
identical update → retract, verifying:

1. The same candidate ID is retained through link, update and retraction.
2. Link adds evidence without another research call or revision bump.
3. A changed statement/condition bumps the research revision and starts fresh research.
4. An old research request that ignores cancellation and returns later cannot overwrite
   the current revision's result.
5. Reapplying an identical update does not bump the revision or rerun research.
6. Retraction changes state and increments the revision.

The test verifies the implementation's content-signature behavior, not semantic equivalence
of every possible paraphrase. It does not claim the model always chooses the right action.
The providers are fake in this integration test; the separate evaluation uses real Kimi.
No production service, capture session, persistence implementation or worker configuration
was changed by this work.

## Reproduce and inspect

```sh
uv run python -m scripts.evaluate_claim_lifecycle --repeat 2 --output /path/to/lifecycle.json
uv run python -m unittest tests.test_claim_lifecycle -q
```

The real-model command incurs charges, uses concurrency 3, the existing private credential
file, reasoning effort `low`, 1200 output tokens and provider-default temperature. It writes
every observation and exits 1 if any case fails; no failures are discarded or automatically
rerun until success. Prompt/dataset hashes and usage are recorded in
[the full synthetic results](evaluations/claim-lifecycle/results.json).

**Accurate presentation statement:** “We test both lifecycle actions and selected scope
fidelity constraints. The unchanged prompt passed 18 of 20 observations in the additional
lifecycle evaluation; a separate real-service test verifies identity, revision handling,
no rerun for links and rejection of stale completions. Semantic consistency is still a
known limitation.”

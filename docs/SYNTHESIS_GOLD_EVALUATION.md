# Controlled-evidence synthesis evaluation

## Final selector-fix regression

On November 2, 2026, a new real-provider run repeated the unchanged 15 fixtures twice
at concurrency three after the quote-selector fix. **28/30 passed both the frozen v1
checks and the separately defined v2 scope checks.** This is a regression on already
inspected fixtures, not a new held-out test or a production accuracy estimate.

- [Complete synthetic outputs and model usage](evaluations/synthesis-selector-regression/results.json)
- [Separate v2 scores](evaluations/synthesis-selector-regression/score-v2.json)
- 28 real Kimi completions; two empty-source runs terminated before inference.
- Both numeric-benchmark cases now reached Kimi and passed (previously 0/2).
- Both `capability_supported` runs returned conditional, retaining the paid-plan
  prerequisite. The frozen label accepts only supported; both remain recorded failures.
  No prompt tuning, relabeling, or selective reruns were performed.
- All original citation-integrity predicates passed, including vacuous empty-citation
  cases. This does not mean every answer supplied a citation or was semantically correct.

Single-agent review of all 30 final summaries and caveats found a remaining limitation:
the second historical-support answer adds an unnecessary caveat about current status in
November 2026 even though the question asks about January 2026. The main historical answer
is correct; the irrelevant qualification is not detected by the lexical scorer. Several
answers also omit the requested initial answer phrase. These are not perfect instruction
adherence or general entailment results. The benchmark fix has narrow regression evidence;
the one-case net score difference does not establish a general model improvement.

The baseline development/extraction and lifecycle reports remain historical measurements,
not new runs. Their dataset hashes, denominators, and structural/fidelity scoring are
checked again by deterministic tests. A scorer regression also verifies that transport
failures remain failed observations when rescored, rather than crashing or disappearing.

Run provenance: source HEAD `9516385c004703ea844615c56e5c52b02b9c43cd` with concurrent
unrelated integration work; synthesis prompt and fixture hashes match the original run.
The evaluated research module SHA256 is
`b0002ef7ca1c838ac1c6bd8e540d9e9fe1e1387423860ceda060efc78dd1d5d6`;
model module SHA256 is
`dc2c4f62651788b31923c9bebf32427ec1f8efb38d41f4c57e64434f4552ed85`.
These hashes identify the actual inference components rather than claiming the entire
working tree was pristine. The result file SHA256 is
`7e419cff1fc1f8c0904b2ea16eb53063b10c9d3ff40e95a79b3ecb1ef28be5d9`.

## Independent audit and post-hoc v2 regression

The original fixtures, completions and 27/30 result below remain unchanged. An audit
recomputed the counts and demonstrated a false pass: a genuine citation and the correct
verdict paired with "Free allows one million exports per day" passes v1. Its OR keyword
check does not establish the correct numerical limit or billing period.

The separately named `score_v2` adds summary-level constraints for five cases: paid-plan
qualification, the Free plan's exact ten-per-month limit, notice retention in every copy,
enabled snapshot mode, and the support end date. Mutation tests keep genuine citations
while replacing quantities/units or relegating the answer to caveats. These target scope
loss and citation-valid but incorrect answers, not general semantic entailment. Valid
paraphrases can still be rejected; ten cases retain only their v1 checks.

Rescoring the same preserved outputs yields **27/30 under v2**, recorded in the
[separate post-hoc report](evaluations/synthesis-v2/summary-v2.json). This is not a new
model run, unseen evaluation, or demonstrated quality improvement. The stricter rubric
was designed after inspecting the outputs. No historical labels or outputs were edited.

The production quote selector now recognizes measured past-tense predicates with an
explicit quantity/unit and preceding subject. Tests cover several verbs, headings,
questions and incomplete clauses. A controlled pipeline test confirms that the formerly
excluded benchmark reaches a fake model and keeps exact citations. The final real-provider
regression above separately verifies this fix. The two historical benchmark failures below
remain failures. Live evaluation and offline rescoring exit nonzero after saving
all outcomes if any check fails.

```sh
.venv/bin/python -m scripts.evaluate_synthesis_gold --rescore docs/evaluations/synthesis-gold-2026-11-02.json --output /path/to/rescore
```

This offline command makes no provider calls and intentionally exits 1 for 27/30.

## What this test establishes

On November 2, 2026, the unchanged production `MeetingResearcher` was exercised with 15 frozen fictional claim/evidence fixtures, twice each, at concurrency three. The real Runpod Kimi K3 endpoint produced 26 completions. Four runs terminated deterministically before inference: two empty-source runs and two benchmark passages rejected by the quote selector. No Firecrawl search was performed in this evaluation.

**Result: 27/30 passed the predeclared automated checks, not 100%.** The outputs also received a single-agent qualitative review against the supplied fixtures; that is not an independent human adjudication or a validated entailment metric.

The production prompt was not tuned on these results. The dataset and expectations were written before the first call and were not relaxed after observing failures. This is an authored regression set, not an independently curated held-out benchmark. Fixture source URLs use `example.com` and a synthetic Reddit path; the injected search adapter never fetches them. The title explicitly identifies fictional content, which may encourage caution differently from ordinary retrieved pages.

## Coverage and measurements

| Case | Expected family | Repeat 1 | Repeat 2 |
| --- | --- | --- | --- |
| Version 4 capability | Supported | Conditional: label failure | Supported |
| Missing version 3 evidence | Insufficient | Pass | Pass |
| Unlimited exports generalized across plans | Contradicted or conditional | Pass | Pass |
| Enterprise evidence used for an unmentioned Free plan | Insufficient | Pass | Pass |
| Commercial use prohibited despite express permission | Contradicted | Pass | Pass |
| Redistribution requires preserving a notice | Conditional or supported with notice | Pass | Pass |
| Matched numeric benchmark | Supported | Insufficient: pipeline failure | Insufficient: pipeline failure |
| Marketing without matched measurements | Insufficient | Pass | Pass |
| Concurrent reads require snapshot mode | Conditional | Pass | Pass |
| Conflicting undated technical specifications | Insufficient | Pass | Pass |
| Dated end-of-support announcement | Supported | Pass | Pass |
| Undated current-support claim | Insufficient | Pass | Pass |
| Community discussion only | Insufficient | Pass | Pass |
| Instructions embedded in source evidence | Contradicted; no injected marker | Pass | Pass |
| No source documents | Insufficient, not contradicted | Pass | Pass |

All 30 returned a final finding without a transport/schema exception. Exact-source citation integrity and factual-verdict citation presence checks passed in all 30 runs, including runs where no citation is required because the verdict is insufficient. **This does not mean all 30 supplied citations:** empty citations satisfy the integrity predicate vacuously. Both injection runs ignored the supplied attack marker; two examples do not establish general prompt-injection resistance.

Raw model verdicts matched final verdicts on all 26 completed calls. The performance guard replaced the qualitative-performance answer text in both repeats; the discussion guard replaced the discussion-only answer text in repeat two. These are production transformations, not additional model calls. Raw completions are retained alongside final findings so model output is not confused with application behavior.

## Failures and review findings

1. **Gold-label ambiguity, not clearly a model error:** `capability_supported` asks whether version 4 supports CSV export, but its evidence adds a paid-plan prerequisite. Repeat one reasonably answered conditional and repeat two answered supported while retaining the prerequisite. The frozen gold permits only supported, so the recorded score remains a failure. A future dataset version should explicitly scope the claim to paid plans or accept both scoped answers; it must not retroactively replace this result.
2. **Quote-selector false negative:** `matched_performance` contains a same-machine, fixed-dataset comparison with two-second versus four-second execution times. The heuristic `evidence_span` recognizes a limited verb list, but not the fixture's `completed` / `required`. No quote options survive, so Kimi is never called. This is an evidence preprocessing recall problem, not a Kimi failure. It must be fixed and rerun separately before claiming the numeric-performance case passes. The fixture should also specify measurement methodology more fully in a future version.
3. **Semantic checks remain partial:** the scorer requires allowed verdicts, selected condition keywords, forbidden-marker absence, exact known-source quotes and citations for factual verdicts. Keywords do not prove entailment. Manual review found the first conflicting-specification caveat says version scope is absent even though both excerpts name version 4. The main answer correctly abstains, but that extra caveat is imprecise and the automated score does not catch it.
4. **Formatting is not scored as correctness:** several answers omit the requested initial `Yes`, `No`, `Only if` or `Not established`. They remain understandable, but instruction adherence is not uniformly perfect. The evaluation does not award a perfect prompt-compliance claim.

## Relation to existing live-search evidence

[Validation](VALIDATION.md) distinguishes real-provider integration from controlled
evidence evaluation. This fixed-evidence test isolates synthesis and source
processing; it does not measure search recall, named-speaker attribution or ASR
quality. Repeating the same 15 fixtures twice measures limited repeatability, not
30 independent examples.

## Reproduce and inspect

```sh
.venv/bin/python -m scripts.evaluate_synthesis_gold --repeats 2
.venv/bin/python -m unittest tests.test_synthesis_gold tests.test_meeting_research -q
```

The live command uses the permission-checked Runpod credential file privately. It writes raw completions and final outputs under the application-support artifacts directory. It intentionally records failed cases instead of aborting remaining work. The six scorer tests plus 24 existing production research tests passed: **30 deterministic tests**. Existing tests exercise unknown quote IDs, fabricated quotes, heading rejection, discussion downgrading and performance-evidence guards.

- [Frozen fixtures](../tests/data/synthesis_gold.json)
- [Runner and deterministic scorer](../scripts/evaluate_synthesis_gold.py)
- [Scorer tests](../tests/test_synthesis_gold.py)
- [Complete synthetic run evidence](evaluations/synthesis-gold-2026-11-02.json)

Recorded source commit: `340b9c7d63405cc72416c8ec30800a592955d482` (the enclosing repository also had unrelated work in progress).

Fixture SHA256: `d9e282164d320e3e7ef6ef950bfe04dbeae1c84bc0501e6e2587f7b4f1af6f45`.

Production synthesis prompt SHA256: `c7ee9b095a46824d24c47343a74629a4c98000fa445e3ea15f4fee71fa4e941f`.

Production research module SHA256: `31f42676d577e8f66a23d4b306a87f949394430456c2e598f7741a385f76af10`.

Production model module SHA256: `dc2c4f62651788b31923c9bebf32427ec1f8efb38d41f4c57e64434f4552ed85`.

Presentation-safe statement: **"We tested synthesis and grounding with 15 frozen synthetic evidence cases, repeated twice. Twenty-seven of thirty passed strict predeclared regression checks; review identified one ambiguous label and a reproducible evidence-extraction limitation. Citation integrity is tested separately from semantic correctness."**

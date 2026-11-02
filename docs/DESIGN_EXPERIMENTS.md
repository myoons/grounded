# Design experiments and decisions

These small experiments investigate specific design choices. They are not general
accuracy benchmarks, independent human annotations or proof that current settings
are optimal. Frozen inputs and raw results were retained before interpretation.
The five lanes ran concurrently; provider timings are not isolated comparisons.
Production changes require separate regression tests after the experimental result.

## 1. Audio boundaries: latency versus context

Experiment implementation: `scripts/experiments/audio_boundaries.py`.
Four synthetic English fixtures across seven configurations produced 28 chunking
observations. Ten selected fixture/configuration rows required 14 distinct serial
GPU requests, all successful, using the pinned Whisper model.

- With an 800 ms mid-clause pause, 200 ms and 600 ms silence thresholds split two
  chunks; 1000 ms kept one. First emissions occurred at 2.0, 2.4 and 6.0 seconds.
- All selected transcriptions retained the critical lexical markers “only”,
  “enterprise” and “if”. That does not establish semantic completeness.
- Overlap increased submitted audio and duplicated words in raw concatenation.
  This was **not** the production transcript merger's error rate; zero overlap
  cannot be declared better from that metric.
- First request took 15.004 seconds round trip. The next 13 had a 2.509-second
  median, versus 1.267-second median inference time. Warmup and transport matter.

**Decision:** retain 600 ms / six seconds / 1.2-second overlap. The experiment
illustrates the trade-off, not optimality. Natural accents and quiet/noisy rooms
need broader audio evaluation before tuning.

## 2. Context: recency alone can forget the subject

Experiment implementation: `scripts/experiments/context_memory.py`.
Six synthetic probes, two strategies and 12 real Kimi calls compared the recent
context baseline with a same-budget lexical-relatedness variant.

On four intentionally difficult old-context probes, designated antecedent retention
was 0/4 versus 4/4; candidate retention was 0/4 versus 3/4. Exact expected lifecycle
actions were 0/4 versus 2/4. Both exclusion controls passed in both strategies.
One correction label was ambiguous; the lexical approach also created a duplicate
when a pronoun returned to an older topic. All outcomes were retained.

**Decision:** do not deploy the experimental selector unchanged. It demonstrates a
real recency failure, but improved retrieval is not sufficient for correct updates.
Keep related-context retrieval as a measured follow-up, with separate retention and
action-fidelity metrics.

## 3. Change density: the output cap can hide unfinished work

Experiment implementation: `scripts/experiments/change_density.py`.
Four dialogues contained 1, 3, 5 or 8 claims plus corrections/repetition. Two runs
of each strategy produced 16 actual Kimi calls with no transport errors.

| Distinct claims | Baseline max 3, two runs | Experimental max 8, two runs |
| --- | --- | --- |
| 1 | 1, 1 | 1, 1 |
| 3 | 3, 3 | 3, 3 |
| 5 | 3, 3 | 5, 5 |
| 8 | 0, 3 | 8, 8 |

The baseline submitted the pending utterances yet advanced its cursor after missed
claims. The intervention increased both the change cap and output budget
(1200 to 3200 tokens), so improvement cannot be attributed to one variable alone.
Observed total tokens were 13,776 versus 15,923. Dollar cost was not measured.
Scoring checked category, action, evidence and lexical subject/predicate fidelity;
these counts do not establish arbitrary-dialogue semantic recall.

**Decision:** raise the bounded output capacity and process pending speech in smaller
chronological batches, with atomic cursor tests. See [capacity policy](CAPACITY_POLICY.md)
for the integrated implementation, saturation handling and new regression evidence.
Extraction remains selective; a single dense utterance is not guaranteed exhaustive.

## 4. Retrieval: more results do not automatically recover the right passage

Experiment implementation: `scripts/experiments/retrieval_recall.py`.
Four frozen claims used independently checked primary pages. One five-result search
response per claim was reused to compare prefixes, avoiding within-response drift.
This does not measure separate limit-three and limit-five requests.

- Canonical URL recall was 3/4 at both prefixes. The version-specific PostgreSQL
  query returned current documentation rather than the requested version.
- On four verified primary pages, decisive passage plus condition retention was
  3/4 at 3000 characters and 4/4 at both 6000 and 10000.
- On synthetic stress documents, the production selector scored 0/3, 2/3 and 3/3.
  Its fixed three-window selection means a larger nominal budget often adds no text.
- A short SQLite forum HTTP 503 page passed the old minimum-length check. This was
  an error-page ingestion defect, not a reason to build a source-authority classifier.

**Decision:** retain the search count and passage budget pending broader evidence;
reject fetched error/challenge pages conservatively. See [the page filter](SEARCH_PAGE_FILTER.md)
for the cached-response replay, ordinary-error-document controls and limitations.

## 5. Scheduling: model latency can dominate the interval

Experiment implementation: `scripts/experiments/scheduling.py`.
Thirty-six virtual-clock configurations exercised production detection/research
loops with controlled provider delays and finalized transcript arrivals. They did
not simulate PCM capture capacity or validate actual cloud concurrency throughput.

All configurations preserved the expected 12 claims and processed cursor. Reducing
the extraction interval helped simulated fast models at the cost of more calls;
with eight-second model latency, interval policies behaved identically. Three
research workers reduced simulated backlog versus two, but their real throughput,
provider contention and cost were not established by the simulation.

Six separate identical Kimi requests used 8213 tokens; one returned no claim while
five returned one. Observed latency median was 6.77 seconds; a p95 from six requests
is descriptive only, not a reliable tail-latency estimate or model ranking.

**Decision:** retain single-flight extraction and two research workers as a conservative
bounded-concurrency policy, not a performance optimum. Queue/scheduling wait and
provider time must be measured separately before claiming an improvement.

## Reproduction and evidence boundaries

The recorded observations precede some production changes. Current scripts reproduce
the experimental procedure, not a guarantee of identical model outputs. The density
comparison includes a frozen baseline prompt and schema so its old arm does not
silently become the new production configuration. Run records identify inputs,
hashes, settings and model usage; compare those before comparing results. Full
fetched pages and generated audio are not included in the source repository.

The decisions intentionally differ: fix a demonstrated correctness defect, retain a
reasonable setting when superiority is unproven, and reject an experimental strategy
that still fails important cases. None of the experiments justifies a blanket claim
that all meeting facts are found or all answers are correct.

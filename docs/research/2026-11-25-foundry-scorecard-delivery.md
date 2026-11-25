# Faithful scorecard delivery alongside Foundry: which channels can carry the confirmed contract?

Research for issue [#17](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/17) (parent map [#1](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/1)).
Date of research: **2026-11-25**. Research branch: `research/scorecard-delivery`.

**Method and honesty statement.** This is a documentation-and-existing-evidence review. **No live Foundry test was run for this ticket.** "Observed" below means observed in the earlier live prototypes for #12 (2026-11-23) and #14 (2026-11-24), read from their committed READMEs and observation files. "Documented" means stated by a first-party page I re-fetched on 2026-11-25. "Inferred" means my reasoning from those, and it is not platform-verified.

## 1. Question and scope

> Which documented and verified channels can convey the confirmed scorecard contract faithfully alongside Foundry-managed and customer-process metric scoring, including raw confidence/outcome evidence, original recorded precision, evaluator-computed values, missing or unavailable results, and execution/partial-run status?

The contract is the one confirmed in #5 on 2026-11-25. Its items that matter here:

| # | Contract requirement (from #5) | Short name used below |
|---|---|---|
| R1 | Attempt-level evidence is inspectable: each confidence rating paired with its outcome and source; missing ones marked | **pair evidence** |
| R2 | Original recorded precision preserved, including the pre-normalization rating | **original precision** |
| R3 | Evaluator-computed values with no silent rounding or lossy conversion; if a surface alters representation, expose the authoritative value and the limitation | **computed values** |
| R4 | Each configured metric is available / unavailable (reason + evidence counts) / not-applicable (reason), with counts and denominators | **availability** |
| R5 | Execution status (timeouts, cancellations, setup failures, invalid/incomplete experiments) separate from task outcome and from measurement availability; partial scorecards say they are partial | **execution status** |
| R6 | Optional customer acceptance per metric (met / not met / not assessed + reason); Foundry's default pass/fail must not masquerade as acceptance | **acceptance vs default pass/fail** |
| R7 | An accurate, accessible scorecard must exist alongside Foundry when native presentation cannot express the contract | **fallback guarantee** |

Distinctions kept throughout: documented vs observed vs inferred; native Foundry display vs offering-owned reporting; **input preservation** (what Foundry echoes back of what was sent) vs **output preservation** (what an evaluator's returned values look like afterward); Foundry default pass/fail vs customer acceptance.

Out of scope: choosing the reporting boundary (that is the decision this research feeds), implementing the offering.

## 2. Short answer

1. **No documented or tested Foundry-native channel conveys the full contract.** Every native result path I could find attaches a Foundry-computed `passed`/`label`/`threshold` to each score, summarizes runs as pass counts, and has no documented slot for "unavailable + reason + counts". The one documented exception for skipped results (the customer-hosted endpoint evaluator, preview) is untested, and still requires `score` and `passed` on completed items. The portal's rendering of anything beyond a per-evaluator score and explanation is undocumented.
2. **Input preservation is verified in part; output preservation is not.** In #12, the tested opaque JSON string (including confidence/outcome pairs, escaping and Unicode) came back exactly in `datasource_item`. Structured floats sent as numbers did not (0.83 came back as 0.8300000000000001). This demonstrates an input reference copy for the tested samples, not a guarantee for other payloads or a rendering of the scorecard.
3. **An offering-owned report is a plausible fallback; no native channel is verified to carry the entire contract.** The #12/#14 offline prototypes rendered captured values locally, but do not establish production accessibility, full contract coverage or an authoritative transport for every managed output. Reporting must retain Foundry-computed values when Foundry performs scoring; substituting customer-side calculations would change the selected scoring direction. This is not a Foundry-native capability.
4. **R6 is at risk on every native channel**: pass/fail is always present on cloud eval items, and the only observed run-level aggregate was a pass count (and one grouped run was reported as `failed` because one of twelve metrics scored 0 against the default 0.5 threshold).
5. **Execution status is only partly visible natively and is ambiguous.** Run `status` and item `sample.error` exist, but #14 saw `completed` items carrying `TARGET_INVOCATION_FAILED`, and #12 saw an evaluator exception produce a generic run failure with no item results. Completion status alone cannot be used as an execution verdict.
6. Material uncertainties for the reporting boundary are listed in section 8, chiefly how the current portal renders custom scores, whether the endpoint evaluator route can carry skip/properties, and whether raw evidence survives a managed run at realistic size. The human decision can determine which further evidence is necessary.

## 3. Source ledger

All fetched 2026-11-25 unless stated. "ms.date" and "updated_at" are from the page front matter.

| ID | Source | Version / date | Used for |
|---|---|---|---|
| S1 | [Get cloud evaluation results](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-results) | ms.date 2026-10-30; updated_at 2026-11-17; commit 9f569454 | Result fields, binarization, aggregate = pass rate, errors in report |
| S2 | [Custom code-based evaluators](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/other-custom-evaluators) (preview) | ms.date 2026-11-11; updated_at 2026-11-24; commit dd514075; needs `azure-ai-projects>=2.8.0` | Code evaluator contract, limits |
| S3 | [Endpoint-based evaluators](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/endpoint-based-evaluators) (preview) | ms.date 2026-11-11; updated_at 2026-11-24; commit dd514075; needs `azure-ai-projects>=2.11.0` | Request/response schema 0.0.2, skipped status, properties |
| S4 | [View evaluation results in the portal](https://learn.microsoft.com/en-us/azure/foundry/how-to/evaluate-results) | ms.date 2026-09-25; updated_at 2026-11-24 | What the portal says it shows |
| S5 | [REST reference: Azure OpenAI Evals (API v1)](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/evals) | ms.date 2026-05-27; updated_at 2026-07-09; commit 6462e3bd | Output item, run, result-count schemas |
| S6 | [azure-ai-projects 2.11.0 changelog](https://github.com/Azure/azure-sdk-for-python/blob/azure-ai-projects_2.11.0/sdk/ai/azure-ai-projects/CHANGELOG.md) | 2026-11-13 (PyPI upload 2026-11-13) | Only the first ~7 000 characters were read; no external-results import API seen there |
| S7 | [azure-ai-evaluation 1.18.12 changelog](https://github.com/Azure/azure-sdk-for-python/blob/azure-ai-evaluation_1.18.12/sdk/evaluation/azure-ai-evaluation/CHANGELOG.md) | 2026-11-09 (PyPI upload 2026-11-12); latest on PyPI at fetch time | Versions; first ~5 000 characters read |
| S8 | [#9 research: Foundry SDK metrics reporting](https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/e5e9460/docs/research/2026-11-22-foundry-sdk-metrics-reporting.md) | 2026-11-22; docs + azure-ai-evaluation 1.18.12 source; **no live test** | Local `evaluate()` upload, App Insights, aggregation |
| S9 | #12 live prototype: [`prototypes/foundry-managed-evals/README.md`](https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/6d38485cf97621d91dbeb1a0224039b5e8cda11a/prototypes/foundry-managed-evals/README.md) and `observations-cbee5be8a2.json` | 2026-11-23; Sweden Central; REST `api-version=v1`, header `Foundry-Features: Evaluations=V1Preview`; Python 3.13.18, Azure CLI 2.93.0; **SDK not validated** (TLS failures) | Managed code-evaluator observations |
| S10 | #14 prototype: [`prototypes/todo-plumbing/README.md`](https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/27d5c66b816aacd77b0a2bce8e94c68e1112530c/prototypes/todo-plumbing/README.md), `run.py`, `observations-fa75b86f19.json` (branch `joranbergfeld-wayfinder-map-review`, head b6c6490) | 2026-11-24 | Twelve metrics through managed evaluators; unavailable handling; evidence shape |
| S11 | Issue resolutions #5, #6, #9, #12, #14, #4, #8, #11 (issue comments) | 2026-11-22 to 2026-11-25 | Contract and prior decisions |

Package versions at fetch time (PyPI): `azure-ai-projects` 2.11.0, `azure-ai-evaluation` 1.18.12. Both match the versions the #9 research used.

## 4. What each channel documents

### 4.1 Cloud evaluation output items and run object (S1, S5)

- S1 documents per-item result fields `label`, `score`, `threshold`, `reason`, `details`, and states that any nonbinary score is binarized to pass/fail against the threshold. The run-level aggregate is a pass rate with passed/failed/total counts. Latency and estimated-cost targets are documented; the cost estimate excludes evaluator usage.
- S5 (v1 REST reference) says an output item's `results[]` entry requires `name`, `passed`, `score`; `type` and a free `sample` object are optional. `datasource_item` is an `object` holding "details of the input data source item". The run's `result_counts` lists `errored`, `failed`, `passed`, `total` (#12 additionally observed a `skipped` count); `per_testing_criteria_results` and a `report_url` are present. `sample` carries a required `error` object.
- **Reading:** `passed` is mandatory at the schema level. No documented field holds an evaluator-supplied availability state, count or free properties for a code evaluator. The `sample` object in `results[]` is described as optional "sample or intermediate data produced by the grader", but #12 observed no evaluator-controlled content there (see 5.2). I found no documented way for a code evaluator to populate it.

### 4.2 Code-based custom evaluator (S2, preview)

- Contract: `grade(sample, item) -> float`, range 0.0–1.0. Exception or timeout yields 0.0 plus an error shown in the report.
- Limits: 256 KB code, 2 minutes, no network, 2 GB memory, 1 GB disk, 2 cores; documented package set includes numpy 2.2.4, scipy 1.15.2, pandas 2.2.3. Turn-level only.
- The page still lists `pass_threshold` and `deployment_name` as required init parameters for code evaluators. #12 observed that evaluators without those parameters ran (see 5.2), so the documentation and the observed behaviour disagree.
- **Reading:** a single float is the only value channel. There is no documented place for reason, counts, or an unavailable state; the documented failure mode (0.0) is itself a value that would be read as a result.

### 4.3 Endpoint-based (customer-hosted) evaluator (S3, preview, schema 0.0.2)

- The endpoint receives normalized `messages` plus `context`, the original dataset row unchanged. `data_mapping` is ignored in 0.0.2. Timeout 120 s.
- Response: HTTP 200 JSON with `status` (`completed` / `skipped` / `error`), `score` and `passed` (required when completed), optional `reason`, `label`, `properties` (free object, may hold `dimension_scores`), `usage`, `model`. Any other top-level field makes Foundry reject the response and record an item error. A `completed` result needs at least one scored dimension (1–5 integer or null). Foundry does not derive `passed` from `score`; the endpoint does.
- `score` must lie within the registered metric range and be an integer for `ordinal` metrics. A 0.0.2 evaluator defines exactly one metric. The portal may register 0.0.1; 0.0.2 needs SDK or REST.
- This is the **only documented first-party contract with a "not applicable / skipped" state plus a reason** and with free-form per-item properties.
- **What it does not say:** whether `skipped` items are excluded from the aggregate pass rate, whether `properties` or `reason` are shown in the portal or returned in output items, or how `skipped` appears in `result_counts`. S3 also gives no way to return multiple metrics per item (one metric) or any aggregate-level object.
- The compute stays in the customer's environment, so it does not avoid the customer-process dependency.

### 4.4 Portal presentation (S4)

- Run list columns: Name, Target, Dataset, Status, Evaluation tokens, Target tokens, Scores ("aggregate score for each evaluator"). Row view: query, response, ground truth, evaluator score and explanation. The Compare view runs statistical tests between runs.
- S4 does not state whether a continuous evaluator's aggregate is a mean or a pass rate, and mentions no evidence columns, custom properties, counts, or unavailable states.
- **Reading:** nothing documented lets the portal display attempt-level pair evidence or unavailability. Whether it can is unverified.

### 4.5 Local `evaluate()` upload and App Insights (S8)

- From #9 (docs + SDK source, no live test): the upload route is documented for classic projects; the source also has a project-endpoint-URL path whose current-portal rendering was never tested; aggregation averages numeric columns, so unavailable/reason/count columns would not show up in `metrics`; App Insights `gen_ai.evaluation.result` events exist but nothing shows a Foundry eval view for them.
- I re-checked the two SDK changelogs (S6, S7; partial reads) and saw no public import-external-results API added since. Because I read only the head of each changelog, **absence is not proven**.

## 5. Observed evidence (existing prototypes, not re-run)

### 5.1 What #12 and #14 showed about input preservation

| Observation | Source | Status |
|---|---|---|
| Opaque JSON-string evidence (confidence/outcome pairs, escaping, Unicode) returned exactly in `datasource_item` | S9 | Observed (sample probes, REST) |
| Nested arrays of attempts also accepted | S9 | Observed |
| Structured floats altered on the way through: 0.83 → 0.8300000000000001; 0.49999999999999994 → 0.49999999999999906, in both inline and uploaded-JSONL probes | S9 | Observed |
| Uploaded JSONL output items have a different evidence shape (nested `item` plus flattened `item.*`) | S9 | Observed |
| A 1.9 MB row worked; 2.1 MB gave HTTP 413 | S9 | Observed |
| #14's grouped evidence sent only `attempt_id, outcome, actions, resources, confidence, violations, exposed`. It did **not** send `confidence_raw` (original response text) or `outcome_source`, and `confidence` was already normalized (`int/100`) | S10 `run.py` (`grouped_evidence`, ~line 535) | Observed in code. **Raw/original-precision preservation through the managed channel was therefore never exercised.** |
| The customer-side observation JSON did retain `confidence_raw`, `confidence_provenance`, `outcome_source` | S10 | Observed (customer-side, not Foundry) |

### 5.2 What #12 and #14 showed about output representation

| Observation | Source | Status |
|---|---|---|
| Items return `results[]` with `name, metric, score, label, reason: null, threshold, passed, status`; `reason` was null even though evaluators were free to compute a reason | S9 | Observed |
| Evaluators with no `init_parameters` ran with `{}`, yet a default `threshold: 0.5` with `label`/`passed` was attached | S9 | Observed (contradicts S2's required-parameter wording) |
| Two task scores 0.25 and 1.0 gave "1 passed / 1 failed"; the run did not report their 0.625 mean | S9 | Observed |
| A combined grouped run reported `{total:1, passed:0, failed:1}` because one criterion (fault_robustness, 0) failed the default threshold, though ten others were fine | S10 | Observed |
| Distinct inputs 0.8300000000000001 and 0.83 produced identical scores | S9 | Observed |
| `resource_consistency` score 0.9231092263693602 returned numerically; the check against an independent oracle used `math.isclose(rel_tol=1e-12)`, not bit equality, and the oracle uses the same formulas | S10 | Observed; tolerance-based |
| Declared 0–1 score range was not enforced (-0.1 and 1.1 accepted) | S9 | Observed |
| No evaluator-returned counts, reasons or properties appeared in output items | S9 | Observed |

### 5.3 What they showed about missing/unavailable results and failures

| Observation | Source | Status |
|---|---|---|
| Graders that raise when a metric is unavailable: all twelve managed unavailable probes failed and produced **no output items** | S10 | Observed |
| Mixed available/unavailable all-twelve runs failed outright (11/1 synthetic, 10/2 live). Submitting only the ten applicable metrics succeeded | S10 | Observed |
| Exceptions, null/object returns, NaN/inf and a 130 s sleep each failed a single-item run with an empty `output_items` and the generic message "All examples failed due to invalid user input". The documented per-item 0.0 plus error was not seen | S9 | Observed (contradicts S2) |
| The customer-side report kept the unavailable reasons | S10 | Observed (customer-side) |
| Some prompt-cloud items showed `completed` while `sample.error=TARGET_INVOCATION_FAILED` | S10 | Observed |
| Execution/partial-run status through Foundry-native surfaces was not exercised; customer-side records did carry interruption/retry data (`transport_retries`, a stopped-run DNS failure) | S10 | Observed (customer-side) |

**Limits on all of the above:** REST only (the SDK was not validated because of TLS failures); one region; synthetic and small live data; the unavailable behaviour is only for scalar graders that raise, not for every Foundry approach (for example the endpoint evaluator, which was not run); native portal rendering and export were never inspected; `report_url` values expired after cleanup.

### 5.4 The offline scorecard

`prototypes/foundry-managed-evals/scorecard.html`/`scorecard.py` and `todo-plumbing/review.html` render **captured API values locally**, showing measurements separate from service pass/fail and submitted vs returned evidence. They demonstrate parts of a customer-side presentation, not complete coverage of the later contract or a production-accessible delivery. They are not a Foundry surface and do not show that Foundry can display the same thing.

## 6. Evidence matrix

Legend per cell: **D** documented by a first-party page; **O** observed in a prior live prototype (#12/#14); **I** inferred, not verified; **U** unverified/undocumented; **✗** documented or observed *not* to work; blank = not applicable.

Channels:

- **A** Foundry portal run/row views (native display)
- **B** Cloud eval output items + run object via API (code evaluator route)
- **C** Opaque JSON string echoed in `datasource_item` (input preservation only)
- **D** Endpoint-based evaluator response (customer-hosted, preview)
- **E** Local `evaluate(azure_ai_project=...)` upload
- **F** App Insights evaluation events
- **G** Offering-owned report produced by the customer process (outside Foundry)

| Requirement | A Portal | B Output items/API | C String echo | D Endpoint evaluator | E `evaluate()` upload | F App Insights | G Offering-owned report |
|---|---|---|---|---|---|---|---|
| **R1 pair evidence** (confidence ↔ outcome ↔ source, missing marked) | U (S4 documents no evidence columns) | ✗ for evaluator-returned (O: no properties/counts returned) | **O** for sample strings (exact echo; the Foundry-held copy is input) | U: `properties` free-form (D) but display and output-item exposure unknown | U (per-row columns may upload; rendering unknown) | U | **I**: unconstrained, rendered from the customer process; demonstrated offline (5.4) |
| **R2 original precision** (raw rating text, pre-normalization) | U | ✗: numbers altered (O); and #14 never sent raw text | **O** only for strings sent; #14 omitted `confidence_raw`/`outcome_source`, so **untested for the real payload** | U | U | U | **I**: customer process holds `confidence_raw` (O customer-side in #14) |
| **R3 computed values, no lossy conversion** | U (S4: "aggregate score"; mean vs pass rate unstated) | O: 0.9231092263693602 returned, checked to 1e-12 only; O: float inputs altered; score range unenforced | n/a (input echo) | D: score must be in range, integers for ordinal; precision of returned score U | U (`_aggregate_metrics` averages numeric columns, per S8) | U | **I**: can preserve and display authoritative values it actually receives, with limitations disclosed; exact transport of Foundry-computed values remains unverified |
| **R4 availability** (unavailable/not-applicable, reason, counts) | U | ✗: no documented state; scalar graders that raise yield no items (O); D: aggregate = pass rate and counts only | I: could carry a status string as opaque input, but that is not Foundry's result | **D**: `status: skipped` + `reason` exists; U: aggregate treatment, portal display; also requires a score+`passed` when completed, and one metric per evaluator | ✗ for columns (aggregate averages numerics, S8) | U | **I**: full control; #14 customer report retained reasons (O) |
| **R5 execution/partial-run status** | D: Status column; O: `completed` with `TARGET_INVOCATION_FAILED` | D: run `status`; `sample.error`; O ambiguous (see 5.3) | n/a | D: errors recorded per item; U: display | U | U | **I**: customer process knows timeouts/retries/stops (O: `transport_retries`, DNS failure in #14); cannot see Foundry-side internals except through API |
| **R6 acceptance vs default pass/fail** | ✗ default pass/fail/threshold is attached to every item (S1, O) | ✗: `passed` required (S5); default 0.5 threshold applied even with no threshold supplied (O); a grouped run failed on one metric (O) | n/a | D: the endpoint sets `passed` itself, so acceptance could be encoded; but that makes Foundry's `passed` carry customer meaning (a masquerade risk), U for display | U | n/a | **I**: acceptance is independent and optional, and can show "not assessed" |
| **R7 fallback guarantee** | Not guaranteed | Not guaranteed | Not guaranteed | Not guaranteed | Not guaranteed | Not guaranteed | Only candidate channel, with the packaging, hosting, and access still undecided |

Reading the matrix: column G is a candidate fallback, not a demonstrated guarantee: its ability to deliver depends on available inputs, managed outputs, precision and customer access. Column C has positive observed *Foundry-side* preservation only for tested input strings; #14's separate managed payload left out the raw-rating and source fields.

## 7. Findings, labelled

### Documented (first-party, fetched 2026-11-25)
- Every cloud eval item has a Foundry-attached pass/fail result; nonbinary scores are binarized against a threshold; run aggregates are pass rates (S1). The REST schema requires `passed` on each grader result (S5).
- A code evaluator returns one 0–1 float and has no documented side channel (S2).
- An endpoint evaluator may return `status: skipped` + `reason` and free-form `properties`, but a completed result needs `score` + `passed`, extra top-level fields are rejected, and a 0.0.2 evaluator has one metric (S3).
- The portal documentation describes only per-evaluator aggregate scores, per-row score and explanation, and a cross-run comparison (S4).

### Observed in earlier live work (REST, 2026-11-23/24; not re-run here)
- Exact preservation of an opaque JSON string in `datasource_item`; alteration of structured floats; default threshold and pass/fail attached unrequested; no evaluator-returned reason/counts/properties in output items; all-or-nothing failure for graders that raise; the grouped-run `failed` outcome driven by a single metric; ambiguous `completed`+`TARGET_INVOCATION_FAILED`.

### Inferred (not platform-verified)
- An offering-owned report could express R1–R6 together if it receives the required authoritative values and evidence and remains accessible. This has not been demonstrated end to end, especially for Foundry-computed output precision.
- Sending evidence as an opaque string plus typed fields would likely give a faithful Foundry-held reference copy for small payloads, but only the string half was observed, and only without `confidence_raw`/`outcome_source`.
- A neutral "informational" threshold would not remove pass/fail; it would invent a threshold and make pass rates meaningless (reasoning from S1/S5, not tested).

### Contradictions between documentation and observation
| Topic | Documentation | Observation |
|---|---|---|
| Required `pass_threshold`/`deployment_name` for code evaluators | S2 says required | S9: evaluators with empty `init_parameters` ran |
| Error handling | S2: exception/timeout → 0.0 + error in report | S9: single-item runs failed with empty `output_items` and a generic error |
| Score range | S2: 0.0–1.0 | S9: -0.1 and 1.1 accepted |
| `result_counts` | S5 lists errored/failed/passed/total | S9 observed a skipped count too |

Because of these, documentation alone is an unreliable basis for the reporting boundary; behaviour must be re-observed at decision time (preview features can change).

## 8. Remaining gaps and the evidence needed before choosing the boundary

| # | Gap | Why it matters | Evidence that would close it | Cost / approval |
|---|---|---|---|---|
| G1 | **Portal rendering is unseen.** What the current portal shows for a custom-evaluator run (Scores column meaning, per-row detail, whether `reason`, `properties`, skips, extra columns appear) | Decides whether "native display" can carry anything beyond a number | A disposable run with the existing #12 evaluators, then screenshots of run list, row view, and compare view, recorded in a repo note; check whether Scores is a mean or a pass rate | Needs a Foundry project, fresh run; the earlier `report_url`s are expired |
| G2 | **Endpoint evaluator is untested** | It is the only documented skipped+reason route; unknown aggregate, display and output-item behaviour | A small Azure Function that echoes precomputed `status/score/passed/reason/properties`; observe output items, `result_counts`, pass rate, portal | Needs hosting and a connection; preview feature; customer-hosted component |
| G3 | **Raw evidence through the managed channel** was never sent (`confidence_raw`, `outcome_source`, pre-normalization rating) | R1/R2 hinge on it; size and shape at production scale unknown (2.1 MB limit seen) | Re-run the #12 string-echo probe with the real field set, at the largest realistic group size, and compare byte-for-byte | Small, repeatable via REST |
| G4 | **Output-value precision**: only a tolerance (1e-12) check exists, and the sample size is small | R3 requires no silent loss | Compare returned scores to the customer's values bit-for-bit, over many values, via both the REST route and the SDK | Small; SDK path first needs the TLS problem solved |
| G5 | **Unavailable/skipped semantics in a grouped run** other than the "filter to applicable metrics before submitting" workaround | R4 | Covered by G2; plus confirm how skipped items affect pass rate | As G2 |
| G6 | **Execution-status semantics**: what `completed` + `sample.error` means, and what Foundry exposes for cancelled/interrupted runs | R5 | Provoke a cancelled run and a target failure; record run status, item status, `sample.error`, and `result_counts` | Disposable run |
| G7 | **`evaluate(azure_ai_project=<project endpoint URL>)` route** is unverified in the current portal (S8) | The only route that might show customer-computed per-row values and columns natively | Run it with precomputed rows and inspect portal and API | Disposable run |
| G8 | **SDK-level validation** (`azure-ai-projects` 2.11.0): all live evidence is REST | Production code will probably use the SDK | Repeat the sample probes via the SDK once the TLS issue is gone | Environment fix |
| G9 | **Retention/export and access** of Foundry run data and the datasets it holds | If Foundry is the reference copy of evidence, retention and permissions matter | Documentation check plus a retention observation; confirm RBAC for readers of results | Needs docs or tenant admin input |
| G10 | **Changelog completeness**: only the head of each changelog was read | An import-external-results API may exist or appear | Full changelog scan and a targeted search before the decision is finalized | Cheap |
| G11 | **Preview stability**: code and endpoint evaluators are preview; behaviours contradict docs | A chosen boundary could silently break | Re-observe at decision time and treat the pinned versions in section 3 as the baseline | n/a |

## 9. Newly precise decision questions for the map

1. **Authoritative record.** Is the offering-owned report the authoritative scorecard, with Foundry holding per-attempt/per-metric inputs and outputs as a reference copy, or must any part of the scorecard be shown natively? (The evidence says native display cannot be shown to carry R1–R6; it does not say it must not be tried.)
2. **Pass/fail on Foundry items.** How should the offering treat Foundry's mandatory pass/fail: ignore it, label it "service default", pick a neutral convention (which invents a threshold), or encode customer acceptance into it (which makes Foundry's `passed` carry customer meaning)? Which is acceptable for R6?
3. **Evidence encoding.** Do we send evidence as an opaque JSON string (exact echo observed) alongside typed fields (floats altered), and which representation is the authoritative one when they differ?
4. **Unavailable metrics in grouped runs.** Accept the workaround of submitting only applicable evaluators and keeping unavailable metrics and their reasons only in the customer report, or require a native channel such as the endpoint evaluator (G2)?
5. **Probe approval.** Approve a disposable Foundry probe covering G1–G7 (portal rendering, endpoint evaluator skip/properties, raw-evidence echo, execution-status provocation, `evaluate()` upload), and who owns the endpoint-hosting cost and the preview-feature risk?
6. **Retention and access.** Who may read scorecards and attempt-level evidence (confidence texts, outcome sources), and for how long, in Foundry versus in the offering-owned report? (G9)
7. **Execution status contract.** Is the customer-process record, rather than Foundry run status, the source of truth for R5, given the ambiguous `completed` + `TARGET_INVOCATION_FAILED` observation?

## 10. Caveats on this research

- No live test was performed for #17. Nothing here claims Foundry portal or endpoint-evaluator behaviour beyond what the cited pages and earlier prototypes state.
- The #12/#14 live evidence is REST-only, from one region, and mostly synthetic. #14's verification used tolerance-based comparison against an oracle that shares the same formulas.
- #14's managed evidence payload left out raw-rating and outcome-source fields, so R2 is "not exercised", not "passed".
- Documentation pages for preview features were updated between 2026-11-17 and 2026-11-24; any of the details could change after this date.
- The REST reference page (S5) is dated 2026-07-09 and may lag the preview features.

# Can SDK-calculated multi-attempt reliability metrics be shown in current Foundry?

Evidence date: **2026-11-22**. Documentation, SDK source and package-tag evidence only. No Azure resource was called and nothing was run, so every statement is "documented", "read in source" or "not verified"; none is observed service behavior.
Ticket: [#9](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/9), narrow follow-up to [#8](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/8) (artifact `docs/research/2026-11-22-foundry-evaluator-production-integration.md`, commit `95da3fbfa12550c169b0523f678e825da1d40f17`; its section 6 and unknown 5 are the starting point). This note does not repeat #8's GA/preview matrix for invocation, hosted agents or the custom-evaluator catalog.

## 1. Answer in brief

**Question:** can externally (SDK) calculated multi-attempt metric values, plus their unavailable status, reasons and evidence counts, be made visible in the *current* Foundry evaluation experience, without being presented as a pass rate and without invented thresholds?

**Verdict: no documented, verified path exists.** Three candidate routes can put *some* numbers into Foundry. Each one fails at least one of the three conditions (current portal, not a pass rate/threshold, unavailable-with-reason visible), or its portal rendering is undocumented.

1. **Cloud evaluation runs always produce pass/fail.** Every item result carries a binary `label`/`passed` derived from a `threshold`, and the documented run aggregate is `pass_rate` per criterion plus `passed/failed/total` counts [S1]. The REST schema for an output-item result requires `name`, `passed` and `score` [S2]. A Foundry-hosted evaluator therefore cannot show a reliability value without also emitting a pass/fail verdict and a threshold.
2. **Evaluators that could carry precomputed values are all preview** (code-based and endpoint-based), need the `Foundry-Features: Evaluations=V1Preview` header, and Learn says Microsoft does not recommend preview for production [S3][S4]. Code-based evaluators can read dataset fields, but return one float, require `pass_threshold`, and record errors as `0.0` [S3]. Endpoint-based evaluators can return `status: skipped` with a `reason` and a free-form `properties` object, but still require `passed` when completed and need a customer-hosted HTTP endpoint [S4]. Whether the portal displays `properties`, skip reasons or skip counts is **not documented**.
3. **Local `evaluate(..., azure_ai_project=...)` logging is documented only for the classic portal.** The Learn page says it "isn't available for the new Foundry portal", is marked preview and was last updated 2026-02-27 [S5]. The SDK source (azure-ai-evaluation 1.18.12) also contains a second upload route keyed on the *project endpoint URL* that targets the current project shape [S6][S7]. It is undocumented on Learn beyond the README, uses a `2025-11-15-preview` internal REST API [S7], and its rendering in the current portal is **not verified**.

The honest classification is: **per-item values with a pass/fail threshold: partially possible (preview). Aggregate multi-attempt values shown as such, with unavailability, reasons and counts: not verified in current Foundry.**

## 2. Source ledger

| ID | Source | Date evidence |
|---|---|---|
| S1 | Learn, Get cloud evaluation results ("Interpret results", "Review aggregate results"): https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-results | ms.date 2026-10-30; source `articles/foundry/observability/how-to/cloud-evaluation-results.md` |
| S2 | Learn REST reference, Azure OpenAI evals (`OpenAI.EvalRunOutputItemResult`; API version v1): https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/evals | read 2026-11-22 |
| S3 | Learn, Other custom evaluators (preview): https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/other-custom-evaluators | commit `9f569454df894e4187555a2587a75fe1a2afcde5` per #8 S7 |
| S4 | Learn, Endpoint-based evaluators (preview): https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/endpoint-based-evaluators | updated 2026-11-17 |
| S5 | Learn (classic), Evaluate locally with the Azure AI Evaluation SDK (preview): https://learn.microsoft.com/en-us/azure/foundry-classic/how-to/develop/evaluate-sdk | last updated 2026-02-27 |
| S6 | `azure-ai-evaluation` README at tag `azure-ai-evaluation_1.18.12` (https://github.com/Azure/azure-sdk-for-python/blob/azure-ai-evaluation_1.18.12/sdk/evaluation/azure-ai-evaluation/README.md), lines 81-96 and 150-156 | 1.18.12, 2026-11-09 (CHANGELOG); PyPI upload 2026-11-12 per #8 |
| S7 | `azure-ai-evaluation` source at the same tag: `azure/ai/evaluation/_evaluate/_evaluate.py` (`evaluate`, `_evaluate`, `_aggregate_metrics`), `_evaluate/_utils.py` (`_log_metrics_and_instance_results_onedp`, `_log_metrics_and_instance_results`), `_common/onedp/_configuration.py` (default `api_version`), `_common/onedp/operations/_operations.py` (URL templates) | tag 1.18.12. Function names are cited because line numbers were only read on `main` on 2026-11-22 |
| S8 | Learn, See evaluation results in the Foundry portal: https://learn.microsoft.com/en-us/azure/foundry/how-to/evaluate-results | ms.date 2026-09-25; `ms.custom: classic-and-new` |
| S9 | Learn, Evaluate existing datasets with the Foundry SDK: https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-datasets | search metadata 2026-09-07 |
| S10 | Learn, Migrate from the Foundry (classic) portal: https://learn.microsoft.com/en-us/azure/foundry/how-to/navigate-from-classic | ms.date 2026-09-11 |
| S11 | Learn, Introduction to cloud evaluation (workflow chooser): https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation | ms.date 2026-10-30 |
| S12 | `azure-ai-projects` 2.11.0 CHANGELOG and source tag `azure-ai-projects_2.11.0` | 2026-11-13 |
| #8 | Prior artifact (above) | 2026-11-22 |

The Learn docs repository (`MicrosoftDocs/azure-ai-docs`) was not readable (SAML-protected), so Learn pages were read as published HTML.

## 3. Current Foundry versus classic

- Learn's migration page states that **hub-based projects are not visible in the current portal**, that `azure-ai-projects` 2.x "targets the new portal" and 1.x "targets the classic portal", and maps `azure-ai-evaluation` as "local evaluations unchanged" [S10]. "Unchanged" is an ambiguous statement about logging; it does not say local-run logs appear in the current portal.
- The classic local-evaluation article is scoped "Applies only to: Foundry (classic) portal. This article isn't available for the new Foundry portal" [S5]. There is **no current-portal counterpart** to that article among the pages read.
- The results page for the current portal is tagged `classic-and-new` [S8]; its run list shows target, dataset, status, token counts and "Scores: Aggregate score for each evaluator used" [S8]. How "aggregate score" is computed for a continuous evaluator (mean score versus pass rate) is **not stated** [S8][S1].

## 4. Candidate routes, assessed one at a time

"Per-item" = one row per evaluated unit. "Aggregate" = run-level figure shown for a criterion.

### 4.1 Cloud Evals API with a JSONL/CSV dataset and built-in evaluators
- Documented, GA feature area (#8 matrix; Evaluations GA, some evaluators preview). Dataset rows may hold precomputed outputs, but they are evaluated by evaluators that *compute their own* score from mapped fields [S9]. Uploading a dataset that contains SDK-calculated reliability columns does not display those columns as metrics; `datasets.upload_file` creates a versioned dataset, nothing more [S9].
- **Not a route.** It scores responses; it does not publish values.

### 4.2 Cloud Evals API with a custom code-based "pass-through" evaluator (preview)
- Mechanism (documented): `grade(sample, item) -> float` reads dataset fields via `item.get(...)`; score is continuous 0.0-1.0; `pass_threshold` and `deployment_name` are required initialization parameters; exceptions/timeouts give `0.0` flagged as error; turn-level only; no network [S3].
- Inference: a row-level float precomputed by the SDK could be returned unchanged.
- **Fails the criteria:** (a) a threshold parameter is mandatory, so each row gets a `passed` verdict and the run gets a pass rate [S1][S3]; (b) there is no "unavailable" return, and the only documented non-score outcome is an error recorded as `0.0` [S3], which is indistinguishable from a real zero in a score aggregate (#8 section 7); (c) reasons and counts have no documented field for code evaluators; (d) the metric domain is fixed to [0, 1]; (e) preview.
- Not verified: whether the portal Scores column shows the mean of `score` or the pass rate for such evaluators; whether output items echo the input row (#8 unknown 5).

### 4.3 Cloud Evals API with an endpoint-based evaluator, schema 0.0.2 (preview)
- Mechanism (documented): the service POSTs one item per call; `context` holds the submitted row unchanged for dataset runs; response `status` is `completed|skipped|error`, with `score`, `passed` (both required when completed), `reason`, `label`, `properties` (can include `dimension_scores`), `usage`, `model`; unknown top-level fields are rejected; `passed` is **not** derived from `score`; the threshold is set on `EvaluatorMetric` and echoed in each request; exactly one ordinal or continuous metric is required; 120 s limit; the endpoint is customer-hosted behind a project connection [S4].
- This is the only documented Foundry contract that has a first-class **skipped + reason** outcome and a free-form place for evidence counts (`properties`).
- **Open against the criteria:** (a) `passed` is required on completion, so a pass/fail verdict exists per item; the evaluator author chooses it, which either invents a threshold or sets it to a constant, and a constant verdict yields a meaningless pass rate [S4][S1]; (b) `dimension_scores` is typed as integer 1-5 or null, so it does not carry arbitrary rates or counts; (c) no page says the portal renders `properties`, skip reasons, or skip counts, or how skipped items enter `result_counts`/`pass_rate` [S1][S4][S8]; (d) it needs a hosted HTTP endpoint (an Azure resource the customer must operate) and preview opt-in; (e) one item per request means any cross-attempt reduction must already be in the row (inference).

### 4.4 Local `evaluate(...)` with `azure_ai_project` as a dict (MLflow route)
- Documented only for the classic portal (preview, classic-only, 2026-02-27) [S5]. Source resolves a `Microsoft.MachineLearningServices/workspaces/<name>` tracking URI, then creates an MLflow run and uploads artifacts (`_log_metrics_and_instance_results` in S7). That is the hub/workspace shape that the current portal does not list [S10] (the link from "workspace-shaped" to "hub-based only" is inference, not stated).
- **Not a current-Foundry route.** Included so it is not rediscovered as one.

### 4.5 Local `evaluate(...)` with `azure_ai_project` as the project endpoint URL
- Mechanism (source and README): `azure_ai_project="https://{resource}.services.ai.azure.com/api/projects/{project}"` selects `_log_metrics_and_instance_results_onedp`, which writes `instance_results.jsonl` plus a `metrics` dict, creates an evaluation result and run through the project's evaluation API, marks it `Completed`, and returns the `AiStudioEvaluationUri` as `studio_url` [S6 lines 94-96, 150-156][S7]. This is the current resource-based project URL shape, the the same endpoint shape that `azure-ai-projects` 2.x uses [S10][S12].
- API status: the internal client's default `api_version` is `2025-11-15-preview`; the URL templates include `/evaluations/runs:upload`, `/evaluations/runs:updateUpload/{name}` and `/evaluationResults/...` [S7]. No Learn REST page for these was found, and `azure-ai-projects` 2.11.0 exposes no matching public operation (searched `_operations.py`, `_patch.py`) [S12]. The package is `Production/Stable` (#8), but this route is private-module plumbing over a preview API.
- Aggregation behavior (source, `_aggregate_metrics`): it first computes `_aggregation_binary_output(df)` (binary pass/fail metrics derived from `*_result` columns), then converts content-safety scores and `*_label` columns to defect rates, excludes `*_threshold`/`*_result` and token-count columns from the mean, replaces the "not applicable" sentinel string with `None`, and takes `df.mean(numeric_only=True)`, dropping NaN results [S7]. Consequences: `metrics` is not an arbitrary dict you control through `evaluate()`; a task-level reliability value would be averaged across rows; non-numeric `unavailable`/reason columns would be absent from `metrics` (kept only in the row artifact); a not-applicable row is excluded from the mean rather than counted, and no unavailable count is produced; and if an evaluator emits `*_result` columns the SDK adds its own pass/fail-rate metrics, so such columns should be avoided to prevent pass-rate presentation (inference from source; not run).
- Not verified: that the run appears in the current portal's Evaluation list; how its rows and `metrics` render; whether custom metric names are displayed; whether the upload is supported for customers (the `azure-ai-evaluation` docs for this are the README only).
- This is the **best candidate to prove or disprove in a hands-on probe**, because it avoids the per-item pass/fail contract of cloud runs.

### 4.6 Direct REST upload of results
- The URL templates in 4.5 suggest a lower-level contract, but there is no public reference, schema or support statement. **Cannot verify; do not rely on.**

### 4.7 App Insights evaluation events
- Source: `azure-ai-evaluation` emits OpenTelemetry events named `gen_ai.evaluation.result` with `gen_ai.evaluation.score.value`, `.score.label`, `.explanation`, `gen_ai.evaluation.status`, `gen_ai.evaluation.passed`, and a JSON `gen_ai.evaluation.properties` attribute, through a private `_app_insights_configuration` keyword (`_log_events_to_app_insights`, `emit_eval_result_events_to_app_insights` in S7). Cloud evaluation can also route results to Application Insights when connected [S11].
- **Not verified:** that these events appear in a Foundry evaluation or monitoring view (only a third-party sample describes querying `customEvents`), or that the private keyword is supported. This is a KQL/Workbook route, not a Foundry evaluation scorecard.

### 4.8 Not found
- MLflow logging directly to a current Foundry project: no current-portal documentation found.
- A documented "import/publish externally computed results" operation: none found in the Learn pages for datasets, cloud evaluation, results, custom evaluators or the REST evals reference [S1][S2][S3][S4][S9][S11].

## 5. Comparison

| Route | Portal | Label | Per-item / aggregate | Avoids pass rate and thresholds | Unavailable + reason + counts | Verified |
|---|---|---|---|---|---|---|
| 4.1 dataset + built-ins | current | GA area | per-item, pass-rate aggregate | no | no | documented, not a route |
| 4.2 code pass-through | current | preview | per-item float; pass-rate aggregate | no (threshold required) | no (error = 0.0) | contract documented; render unverified |
| 4.3 endpoint evaluator | current | preview | per-item; pass-rate aggregate | no (`passed` required) | skipped + reason yes; counts only in `properties`, rendering unverified | contract documented; render unverified |
| 4.4 local, dict project | classic only | preview | rows + mean metrics | n/a | not carried | documented, classic |
| 4.5 local, endpoint URL | current (unverified) | internal preview API | rows + mean metrics | yes if the portal shows raw metrics | only in row artifact; dropped from `metrics` | source read, no docs, not rendered |
| 4.6 direct REST | unknown | preview internal | unknown | unknown | unknown | not verifiable |
| 4.7 App Insights | none (KQL) | not a Foundry eval view | events | yes | `properties` JSON | source read |

## 6. Required assumptions if a route is later chosen

1. Accept a **preview** dependency (4.2/4.3 documented preview; 4.5/4.7 undocumented internal preview). #8 already recorded that acceptability is a customer decision.
2. Metrics must be flattened to **per-task or per-run rows** before upload, because every route scores or stores one row at a time. The SDK calculation stays authoritative; Foundry shows it, it does not recompute it.
3. The evidence contract needs fields for value, unavailable flag, reason and counts that are **not** named `*_label`, `*_threshold` or `*_result` (those trigger defect-rate or pass/fail handling, or are excluded, in `_aggregate_metrics`) [S7].
4. For routes 4.2/4.3, a **neutral threshold convention** (for example "informational, always pass") would be needed; it would make the Foundry pass rate meaningless and must be labeled as such. This is a workaround, not a documented capability, and still counts as inventing a threshold.

## 7. Gaps and unverified

1. Whether the current portal shows a run created by `evaluate(azure_ai_project=<endpoint URL>)` (4.5), and how it renders `metrics`, rows and custom column names.
2. Whether the portal Scores column and Compare view use mean `score` or `pass_rate` for continuous custom evaluators; which statistic the t-test uses [S8].
3. Whether endpoint-evaluator `properties`, `reason`, `label` and skipped items are visible in the run details, and how skipped items affect `result_counts`/`pass_rate`.
4. Whether output items echo arbitrary input columns (#8 unknown 5).
5. Public contract, support policy and api-version stability for `/evaluations/runs:upload` and `/evaluationResults` (4.5, 4.6).
6. App Insights `gen_ai.evaluation.result` events in a current Foundry view (4.7).
7. Whether a hub-based project can host the dict route 4.4 at all in the current service; only classic documentation was read.
8. No hands-on validation. No evaluator was registered, no run uploaded, and no portal screenshot was seen.

## 8. Alternative to investigate

Run a **disposable hands-on probe** (needs a test project and user approval for Azure operations) in this order:
1. 4.5: call `evaluate()` with a tiny JSONL of precomputed metric rows and a fake no-op evaluator whose outputs are the SDK values, plus `azure_ai_project=<endpoint URL>`. Observe the Evaluation list, run details, `metrics`, and whether string and null columns survive.
2. 4.3: register a 0.0.2 endpoint evaluator that echoes `context` fields into `score`/`properties` and returns `skipped` with reasons. Observe where `properties` and skipped items appear.
3. If neither renders the values acceptably, treat Foundry as the *invocation and per-attempt evidence* layer only, and publish the reliability scorecard from portable customer code (report, CI output or App Insights/Workbook), which is consistent with #8's section 6 inference and avoids any pass-rate presentation.

This note makes no architecture or product decision.

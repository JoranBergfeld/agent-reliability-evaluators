# Production-ready Foundry evaluator integration: GA vs preview, per capability

Evidence date: **2026-11-22** (all pages and packages read on that date; no Azure resources were called, so everything here is documentation, changelog and package-index evidence, not observed service behavior).
Ticket: [#8](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/8), map [#1](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/1).
Extends, and does not repeat, the closed #4 artifact `docs/research/2026-11-17-foundry-evaluator-coverage.md` (branch `research/foundry-reliability-coverage`, commit `2b600d035e4653881eebbe1cf77c01b2f5d09b23`). Where this note disagrees with #4, this note is newer and says so.

This note records facts, labeled inferences and unknowns. It does not choose an architecture, a metric policy or a product shape.

## 1. Answer in brief

1. **Invoking Foundry-managed agents has a verified non-preview path (Python).** Hosted Agents were announced generally available on 2026-07-09 [S1][S2]. `azure-ai-projects` 2.3.0 (2026-07-01) removed the `allow_preview=True` requirement for Hosted Agent methods [S3]; current release is 2.11.0 (2026-11-13), classifier `Production/Stable`, Python >=3.10, `openai>=3.0.0` [S4]. The Learn quickstart invokes a hosted agent with `project_client.get_openai_client(agent_name=...)` then `responses.create(...)` [S5]. .NET hosted-code deployment is still prerelease [S5][S2].
2. **Running custom reliability logic *inside* Foundry has no verified GA path.** Every custom evaluator type that executes inside Foundry (prompt, code, endpoint) is labeled preview, "not recommended for production workloads", and registration requires the `Foundry-Features: Evaluations=V1Preview` header [S6][S7][S8]. Rubric evaluators are described as preview on the built-in catalog page [S9] but are not marked preview on the custom-evaluators overview [S6]; status unresolved.
3. **Running the logic from customer code/CI has a verified stable base.** Local callables via `azure-ai-evaluation` 1.18.12 (`Production/Stable`) [S10] plus the stable Projects SDK Evals client for invocation and result retrieval [S4][S11]. The Foundry-provided GitHub Action for CI evaluation is preview [S12].
4. **Multi-attempt and controlled-variation metrics have no verified Foundry-side reducer.** Cloud evaluation scores each item independently and aggregates only as pass rate/counts per criterion [S11]. Grouping attempts by task and condition must happen in portable customer code over retrieved output items (inference, section 6).
5. A GA base service or SDK does **not** make the custom-evaluator or CI-action layers production-ready; they are separate capabilities with their own labels (matrix below).

## 2. Source ledger

| ID | Source | Date evidence |
|---|---|---|
| S1 | Azure blog, "GPT-5.6 now available in Microsoft Foundry": https://azure.microsoft.com/en-us/blog/gpt-5-6-now-available-in-microsoft-foundry/ ("Hosted agents in Foundry Agent Service is now generally available"; resilient tasks stay private preview) | announcement 2026-07-09 (date cross-checked to S2 release table; the blog page's own date was not parsed) |
| S2 | Foundry devblog, "What's new in Microsoft Foundry: July and August 2026": https://devblogs.microsoft.com/foundry/whats-new-in-microsoft-foundry-july-august-2026/ | 2026-09-09 |
| S3 | azure-sdk-for-python `azure-ai-projects` CHANGELOG: https://github.com/Azure/azure-sdk-for-python/blob/azure-ai-projects_2.11.0/sdk/ai/azure-ai-projects/CHANGELOG.md (2.3.0 section; 2.8.0 samples; 2.11.0 section) | tag 2.11.0, 2026-11-13 |
| S4 | PyPI JSON for `azure-ai-projects`, `openai`, `agent-framework`: https://pypi.org/pypi/azure-ai-projects/json | read 2026-11-22 |
| S5 | Learn, hosted-agent quickstart and hosted-agents concept page (https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/quickstart-hosted-agent; concept page ms.date 2026-11-16, repo commit `2d7b2ca9e04f488fe69bb5b4bac5a400def5fb8b`) | quickstart ms.date 2026-10-26, updated 2026-11-11 |
| S6 | Learn, Custom evaluators overview: https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/custom-evaluators | ms.date 2026-11-11, updated 2026-11-17, commit `9f569454df894e4187555a2587a75fe1a2afcde5` |
| S7 | Learn, Other custom evaluators (prompt and code): .../evaluation-evaluators/other-custom-evaluators | same dates and commit as S6 |
| S8 | Learn, Endpoint-based custom evaluators: .../evaluation-evaluators/endpoint-based-evaluators | same dates and commit as S6 |
| S9 | Learn, Built-in evaluators and Agent evaluators: .../concepts/built-in-evaluators, .../evaluation-evaluators/agent-evaluators | ms.date 2026-09-09 and 2026-09-30, updated 2026-11-17, same commit |
| S10 | PyPI JSON for `azure-ai-evaluation`: https://pypi.org/pypi/azure-ai-evaluation/json | 1.18.12 uploaded 2026-11-12 |
| S11 | Learn, Run cloud evaluations / get results / evaluate agents: .../observability/how-to/cloud-evaluation, `cloud-evaluation-results`, `cloud-evaluation-targets` | ms.date 2026-10-30, 2026-10-30, 2026-09-17 (targets page updated 2026-09-17, not re-updated by the 2026-11-17 batch) |
| S12 | Learn, Run an evaluation in GitHub Action (preview): https://learn.microsoft.com/en-us/azure/foundry/how-to/evaluation-github-action | ms.date 2026-11-05, updated 2026-11-06 |
| S13 | Learn, Foundry GA/preview feature status page and SDK overview: https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/sdk-overview | GA page updated 2026-11-17; SDK overview ms.date 2026-10-06 |
| S14 | Learn, Agent Framework evaluation: https://learn.microsoft.com/en-us/agent-framework/agents/evaluation | ms.date 2026-11-01, commit `77ccacb126bb88de7e3b0af64f530c870e9c672f` |
| P4 | #4 artifact (see above) | evidence date 2026-11-17 |

## 3. GA vs preview matrix (each capability separately)

"Verified" = a primary source states the label. "Not labeled" = no label found in the page text; not treated as GA.

| Capability | Label | Evidence |
|---|---|---|
| Hosted Agents service (Responses/Invocations/A2A protocols) | **GA** (2026-07) | S1, S2, S13. Learn hosted-agent pages carry no preview label [S5]. A March 2026 blog said public preview; superseded. |
| Hosted-agent sub-features: resilient/long-running tasks | Private preview | S1 |
| Hosted-agent session compute APIs, memory snapshots, steering | Preview (per concept page; details not re-verified feature by feature) | S5 |
| Python `azure-ai-projects` Hosted Agent methods, `.agents`, `.toolboxes` | **Stable** since 2.3.0; latest 2.11.0, `Production/Stable`, Python >=3.10 | S3, S4 |
| JS/TS `@azure/ai-projects` hosted/toolbox ops | Stable per devblog since 2.3.0; Node >=22; SDK overview lists 2.8.0 | S2, S13 |
| Java hosted-agent management | Separate `azure-ai-agents` package; label not independently verified | S2 |
| .NET hosted-agent deployment | **Preview**: quickstart installs `Azure.AI.Projects --version 2.1.0-beta.4`; `Azure.AI.Projects` 2.0.1 stable lacks it; newer management APIs on `3.0.0-beta.1` | S2, S5 |
| `get_openai_client(agent_name=...)` | Preview in 2.1.0 (2026-04-20, required `allow_preview=True`); GA quickstart (2026-10-26) uses it without a preview flag. The changelog does not state the exact release that made it stable. | S3 (2.1.0 entry), S5 |
| Prompt-agent invocation through the same client | Not separately verified in this pass | none |
| Evals API (create eval, run, output items) via `get_openai_client().evals` | GA feature area ("Evaluations GA, some evaluators and features Preview") | S13, S11 |
| `azure_ai_target_completions` with `azure_ai_agent` target | Not labeled on the page; typed model `TargetCompletionEvalRunDataSource` carries no `Preview` suffix (contrast `TracesPreviewEvalRunDataSource`) | S11, S3 (2.1.0 entry) |
| Evaluation job creation | Long-running operations since Python 2.4.0 (behavior change: result is not immediately available) | S2 |
| Built-in agent evaluators Task Completion, Task Adherence, Intent Resolution, Customer Satisfaction, Output/Tool Use Quality | **Preview** (Intent Resolution is now marked preview; #4 did not record this) | S9 |
| Rubric evaluators | Built-in page: preview; overview page: unmarked | S9, S6 |
| Prompt-based custom evaluators | **Preview** | S7 |
| Code-based custom evaluators | **Preview** | S7 |
| Endpoint-based custom evaluators (`schema_version` 0.0.2) | **Preview** | S8 |
| Cloud trace evaluation | Mixed: Python samples use "GA `azure_ai_trace_data_source`" (2.8.0, 2026-10-02), while the GA page lists trace-to-eval-dataset conversion as preview and `TraceSourcePreviewEvalRunDataSource` still exists in 2.11.0. Unresolved. | S3, S13 |
| Scheduled dataset/trace evaluation rules | Added in 2.11.0; label not verified | S3 |
| Foundry GitHub Action for evaluation | **Preview** | S12 |
| Local `azure-ai-evaluation` 1.18.12 | Package `Production/Stable`, Python >=3.9. Per-evaluator labels inside it were not re-checked (see #4). | S10, P4 |
| Agent Framework evaluation (`evaluate_agent`, `LocalEvaluator`, `FoundryEvals`) | `agent-framework` 1.26.0 `Production/Stable`, Python >=3.11; no preview/experimental marker found in the page text; API stability not independently verified | S14, S4 |
| Entra ID auth for evaluations/agents | Required; API keys not accepted for these | S13 |

## 4. The three surfaces, kept separate

### 4.1 Local evaluator callables (customer process)
- Callable evaluators and `evaluate(...)` are in `azure-ai-evaluation` (1.18.12) [S10]; the `evaluate` aggregate is a mean of numeric columns with no arbitrary reducer [P4].
- Agent Framework `evaluate_agent(..., num_repetitions=N)` runs each query N times "independently" and returns 1 query x N items [S14]. It also accepts custom check functions returning `bool`, `float` (>=0.5 = pass), a dict with `score` or `passed`, or `CheckResult` [S14]. No documented fault-injection or environment-reset hook (unchanged from #4).
- Nothing in these packages runs inside Foundry. A local callable cannot be uploaded as-is to the cloud service (inference from the evaluator contracts below; no upload API for arbitrary callables was found).

### 4.2 Cloud-hosted custom evaluators (execute under Foundry evaluation runs)
All three are preview and need `headers={"Foundry-Features": "Evaluations=V1Preview"}` on `evaluators.create_version` [S7][S8].

| | Code-based | Prompt-based | Endpoint-based |
|---|---|---|---|
| Where it runs | Foundry sandbox | Foundry judge model | Customer HTTP endpoint behind a project connection |
| Contract | `grade(sample: dict, item: dict) -> float` in `code_text`, score 0.0-1.0, `continuous` metric | Judge prompt returning JSON `{result, reason}`; ordinal/continuous/binary | POST per item; schema 0.0.2 request (`messages`, `evaluation_target`, `context`, `metadata`, `agent_definition`, `threshold`); response `status` completed/skipped/error, `score`, `passed`, `reason`, `label`, `properties`, `usage`, `model` only |
| Limits | code <256 KB, 2 min per call, 2 GB RAM/1 GB disk/2 CPU, **no network**, fixed package list (numpy 2.2.4, scipy, pandas, scikit-learn, rapidfuzz, sympy, jsonschema, pydantic 2.10.6, deepdiff, nltk, rouge-score, pyyaml); exceptions/timeouts recorded as 0.0 and flagged as error | n/a | endpoint must answer within **120 seconds** [S8] |
| Evaluation level | **Turn only**; a conversation-level run skips code evaluators [S7] | not stated in the pages read | Turn and conversation; conversation receives all `messages` [S8] |
| Required init params | `deployment_name` (even though no LLM is called) and `pass_threshold` | `deployment_name`, `threshold` | threshold set on `EvaluatorMetric` and echoed in each request |
| Aggregate | pass rate per criterion [S11] | same | same |

Facts that resolve or change #4:
- **Code evaluators and conversation-level**: #4 recorded a conflict (H vs V). The 2026-11-17 page states code evaluators support turn level only and are skipped in conversation runs [S7]. Resolved in favor of V.
- **Schema drift**: current pages show `definition.init_parameters`, `data_schema` and `metrics` as above; endpoint evaluators use `EndpointBasedEvaluatorDefinition(connection_name, schema_version, metrics)` and `EvaluatorVersion(evaluator_type=EvaluatorType.CUSTOM, supported_evaluation_levels=[TURN, CONVERSATION])` [S8]. SDK >=2.11.0 exposes `schema_version`/`EndpointEvaluatorSchemaVersion` (the endpoint page pins `azure-ai-projects>=2.11.0`; the prompt/code page pins `>=2.8.0`) [S3][S7][S8].
- **Endpoint contract**: 0.0.1 is the default if `schema_version` is unset and "remains supported"; 0.0.2 is "recommended", ignores `data_mapping`, requires exactly one `ordinal` or `continuous` metric, rejects unknown top-level response fields, and the portal may still register 0.0.1 [S8].
- **Endpoint timeout**: #4 recorded 30 s; the 2026-11-17 page says 120 s [S8]. Newer value used; cause not explained by the source.
- Endpoint auth: API key (`api-key` header) or Entra ID (bearer token from a managed identity scoped to an app registration) via project connections; creating the connection needs `azure-mgmt-cognitiveservices` [S8].
- Each endpoint call carries one item. `context` holds the submitted row unchanged for dataset/model/agent-target runs; `metadata` carries `run_type` and, when available, `response_id`, `agent_name`, `agent_version`, `conversation_id`, `trace_id`, `span_id` [S8]. Nothing documents that the service passes other items of the run, orders items, or serializes calls.

### 4.3 Foundry agent invocation endpoints
- Protocols and URLs: Responses `{project_endpoint}/agents/{name}/endpoint/protocols/openai/responses`; Invocations `.../protocols/invocations`; A2A `.../protocols/a2a` (plus Activity, Invocations WebSocket) [S5].
- Python invocation (stable SDK): `AIProjectClient(...).get_openai_client(agent_name=...)` then `responses.create(input=...)`; deployment through `agents.create_version_from_code` and `agents.update_details(agent_endpoint=...)` [S5].
- Agent versions are immutable; one version is served per endpoint; no traffic splitting; environment variables are immutable per version [S5]. Sessions are VM-isolated with persisted `$HOME`, idle timeout 2-240 min (default 15), deleted after 30 days idle [S5]. Hosted runs are billed per allocated CPU/memory [S5].
- Foundry evaluation can invoke the agent itself: data source `azure_ai_target_completions`, target `{"type":"azure_ai_agent","name":...,"version":...}`. Version is optional (latest if omitted), so a reproducible run needs it pinned. Prompt agents and Responses-protocol hosted agents use `input_messages`; Invocations-protocol hosted agents use `{"message": "{{item.query}}"}`. Outputs are mapped through `{{sample.output_text}}` and `{{sample.output_items}}` [S11].
- Runtime constraints that matter for CI images: the hosted-agent quickstart requires Python 3.13+, whereas the Projects SDK minimum is 3.10 [S4][S5]. #4's unresolved Python inconsistency is therefore resolved as different requirements for different components, not a contradiction.

## 5. Version and pin facts (supersede #4)

| Package | Version/date | Notes |
|---|---|---|
| `azure-ai-projects` | 2.11.0, 2026-11-13; 2.3.0 = first with stable hosted agents (2026-07-01); 2.12.0 unreleased | `openai>=3.0.0`, `azure-identity>=1.15.0`, `azure-core>=1.37.0`, Python >=3.10 [S4] |
| `openai` | 3.57.0, 2026-11-20 | #4's `openai>=2.8.0` is outdated; Projects >=2.5.0 requires `openai>=3.0.0` [S2][S4] |
| `azure-ai-evaluation` | 1.18.12, 2026-11-12, Python >=3.9 | unchanged since #4 [S10] |
| `agent-framework` | 1.26.0, 2026-11-12, Python >=3.11 | [S4] |
| JS `@azure/ai-projects` | 2.8.0 per SDK overview, Node >=22 | not independently fetched from npm |
| .NET | `Azure.AI.Projects` 2.0.x stable; hosted deployment on prerelease | [S2][S5] |

Contract-affecting changes in the window: evaluation job creation is long-running from 2.4.0 [S2]; `schema_version` for endpoint evaluators in 2.11.0 [S3]; typed eval data sources (JSONL, CSV, target completions, conversation simulation, traces) in 2.11.0 [S3]; `list_diagnostics()` for evaluators is preview [S3].

## 6. Multi-attempt and controlled-variation evidence

Verified:
- Cloud evaluation returns per-item results (`evals.runs.output_items.list`, paged) and run-level `result_counts` and `per_testing_criteria_results` with `passed`, `failed`, `pass_rate`. The documented aggregate is a pass rate; there is no documented custom reducer or cross-item grouping [S11].
- Agent targets: model-target runs can expose `latency.target` (p50/p95) and `estimated_cost.target` on the run; the page describes these for model targets only, so agent-target latency/cost availability is unverified [S11].
- Agent Framework offers `num_repetitions` for repeated independent runs [S14].
- The preview Foundry GitHub Action advertises confidence intervals and significance tests when comparing agents `name:version` [S12]; it is preview and its statistics are not specified in the pages read.
- Endpoint evaluators evaluate one item per request (section 4.2).

Inference (not documented; to be validated by a later prototype, not asserted):
- Attempt grouping can be carried as columns on each dataset row (task id, attempt id, condition) so `context` and run output items preserve them, then regrouped in customer code. Whether output items echo arbitrary input columns was not verified.
- Reducing across attempts (consistency, robustness, controlled variations) therefore lives in portable customer-side code that consumes per-attempt evidence, whichever surface produced it. Foundry custom evaluators remain per-attempt (or per-conversation) scorers.
- Repeating attempts through an `azure_ai_agent` target by duplicating rows is plausible; whether duplicates get isolated sessions, or whether the target can reset state or inject faults, is unverified.

Not documented anywhere read: a fault/perturbation injection hook for Foundry-managed agents, a way to control `temperature`/seed for agent targets, and how many hosted sessions an evaluation run opens.

## 7. What each capability means for the twelve metrics (evidence-shape view only)

The metric list and definitions are in the paper-research artifact (branch `research/paper-reliability-metrics`) and the #4 coverage table, which this note does not restate. By evidence shape:
- **Per-attempt evidence** (outcome, confidence rating, violation assessment, trace-derived facts): can be produced by a local callable, or by a turn/conversation-level endpoint evaluator (preview). A code evaluator is turn-only and has no network or customer packages, so it cannot carry portable logic beyond a pasted function.
- **Grouped multi-attempt evidence**: needs a reducer outside Foundry evaluators (section 6). Aggregation inside Foundry is limited to pass-rate.
- **Controlled-variation evidence**: needs an experiment harness that controls inputs and faults; nothing in Foundry was found that provides this, and Agent Framework `num_repetitions` repeats but does not vary.
- **Unavailable measurements** (glossary term): a service error on the evaluator side is recorded by Foundry as `0.0` for code evaluators [S7], which is indistinguishable from a real score in the aggregate unless the error flag is read. This is a data-integrity constraint on any metric reported as unavailable.

## 8. Production-path assessment

Statements below are only about what the cited labels allow.

| Requirement | Verified non-preview path | Preview dependency |
|---|---|---|
| Call hosted agents from customer code/CI | Python `azure-ai-projects>=2.3.0` (latest 2.11.0) + Responses endpoint [S3][S4][S5] | none identified for Responses invocation; Invocations-protocol client in the stable SDK not verified |
| Call prompt agents | Same client family; label for the prompt-agent path not separately verified | |
| Run reliability logic in customer process | `azure-ai-evaluation` / plain Python over invocation results | none |
| Run reliability logic as a Foundry evaluator | none found | prompt/code/endpoint custom evaluators, all preview [S7][S8] |
| Use Foundry evaluation to invoke the agent (target) | Evals API + `azure_ai_target_completions` (label not stated) [S11] | built-in agent evaluators Task Completion/Adherence etc. are preview [S9] |
| CI | customer-written CI using the SDK; Foundry GitHub Action is preview [S12] | Action |
| Aggregate across attempts | customer code | none in Foundry |

Constraint on "initially against Foundry-managed agents": for .NET hosted-code deployment, a prerelease SDK is currently required [S5]; the Python path is the only one this note verified as stable end to end.

## 9. Unverified or unresolved

1. Exact release after 2.1.0 in which `get_openai_client(agent_name=...)` stopped requiring `allow_preview=True` (the 2.3.0 changelog does not mention it; the Oct 2026 quickstart omits the flag).
2. Whether `azure_ai_target_completions` against a hosted agent is GA or preview (page has no label); whether the Evals API's agent-target path is covered by the GA statement for Evaluations.
3. REST `api-version` for create-evaluator (the REST reference page was not fetched in this pass; #4's gap remains). Preview header is `Foundry-Features: Evaluations=V1Preview`.
4. Rubric evaluator status (preview vs unmarked), prompt-evaluator conversation-level support, and trace-evaluation GA vs preview (conflicting signals above).
5. Whether run output items echo arbitrary input columns (task/attempt/condition identifiers) and in what shape.
6. Whether Foundry opens an isolated hosted session per evaluated item, and any concurrency/rate limits for agent-target runs.
7. Stable-SDK client for the Invocations protocol and Responses `api-version` stability for the REST route.
8. Per-evaluator status inside `azure-ai-evaluation` 1.18.12, Agent Framework evaluation API stability, Java/JS hosted-agent behavior, JS npm version, and the exact date on the Azure blog page.
9. Whether Microsoft will promote custom evaluators to GA, and when. No roadmap source was found.
10. No hands-on validation: no evaluator was registered, no run executed, no hosted agent invoked.

## 10. Dependencies for downstream tickets

- Any design that needs "inside Foundry" execution of custom reliability logic depends on preview custom evaluators; the acceptability of that dependency is a customer/product decision this note does not make.
- The evidence contract must carry task id, attempt id, condition and outcome source in a form that survives the Foundry round trip (section 6, unknown 5), and stay independent of any Foundry-specific message schema.
- Pin and test against: `azure-ai-projects>=2.11.0` (for endpoint `schema_version`) with `openai>=3.0.0`; Python 3.10+ in CI (3.13+ if the CI also deploys hosted agents from code); Entra ID auth in CI.
- Evaluation job LRO behavior (2.4.0+) affects any CI polling wrapper.
- Prototype work, if later scheduled, should close unknowns 1, 2, 5 and 6 directly against a test project.

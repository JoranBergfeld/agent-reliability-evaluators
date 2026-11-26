# Foundry token usage across evaluation paths

**Research date:** 2026-11-26. **Question:** [issue #24][issue24]. This is a source review and analysis of previously saved observations, not a new experiment. No resources were deployed, Foundry agents invoked, or Foundry token usage incurred for this research. Documentation is mutable; version and observation boundaries below matter.

## Decision and conclusion

The agreed [decision][decision22] is after-the-fact reporting of available tokens per **Attempt**, **Evaluator**, and **Evaluator sweep**. An Evaluator computes one metric; a sweep groups planned Attempts into a scorecard. Include model calls for the target, an isolated post-task confidence follow-up, optional shared task generation, evaluator scoring, and tool-side model work where reported. Count shared work once in the sweep; per-Evaluator totals can overlap and must be labeled nonadditive. Explicitly represent failed, cancelled, and retried Attempts and unavailable usage. Unknown is not zero. No currency estimates, budget caps, or LLM judge are introduced here.

**Bottom line:** returned Responses and some managed evaluation items can expose actual token counts, and both prompt and Responses-hosted paths have historical evidence. There is **no demonstrated complete, deduplicated ledger** across all required phases and all failure modes. In particular, historical `per_model_usage` rows can contain overlapping aggregate and model-specific views. APIs, historical samples, traces, and a portal display are not interchangeable accounting layers.

Use these labels throughout: **D** = documented for the named API or adapter; **O** = observed in the pinned prototype files; **I** = inference/design implication; **?** = not established. Observation does not prove a future contract or complete coverage.

## Scope and versions

- **Current documented APIs:** Foundry project Responses at `/openai/v1/responses` and Evals at `/openai/v1/evals/...`; the current cloud guide requests Python `azure-ai-projects>=2.2.0` and uses its OpenAI client. The documentation does not pin an exact OpenAI client version, so `>=2.2.0` is a prerequisite, not a tested version. [Responses reference][response-api]; [cloud setup][cloud-setup]; [Evals item reference][item-api]; [Evals run reference][run-api].
- **Historical prototype:** the [saved #13 observation][proto13] started 2026-11-24 01:24 UTC; its [harness][proto13-code] identifies agent/evaluator API `v1`, OpenAI API `v1`, and ARM connection API `2025-06-01` (the last is **not** an evaluation API version). It used `gpt-4.1-mini`, model version `2025-04-14`, `GlobalStandard`. Its hosted agent registered Responses protocol `2.0.0` with Python 3.13; its [requirements][proto13-requirements] did not pin the installed Agent Framework package versions. Its prompt/customer call used project `/openai/v1/responses`; its hosted/customer call used the hosted `/agents/.../endpoint/protocols/openai/responses?api-version=v1` endpoint. Do not equate protocol, API, model, and package versions. The [#14 observation][proto14] is a separate later campaign using the same four entry-point combinations; the cited saved files, not a new live run, are the evidence.
- **CI:** CI may orchestrate either customer-code or managed runs, but it does not itself create extra Foundry counters. No CI-specific observation establishes access, retention, token completeness, or error recovery. Credentials/permissions, data export, and artifact retention remain operational prerequisites, not evidence of availability. [Cloud setup][cloud-setup]; [trace setup][trace-setup].

## Coverage matrix

| Path or work | Documented or observed availability | Gap that prevents a complete guarantee |
| --- | --- | --- |
| Prompt agent, customer code | **D:** Responses may carry `usage`. **O:** #14 saved usage for all 8 returned responses of 11 Attempts; 3 prompt/customer Attempts instead had invocation errors. #13 captured 2 normal response counts and 2 HTTP 400 tool failures without response usage. [response-api]; [proto13]; [proto14] | **?** Tokens consumed before a failed/no-response call, internal model/tool calls, and all retry costs are not guaranteed. A successful response is not proof of task success. |
| Hosted agent, customer code | **D:** hosted implementations may expose Responses or Invocations. **O:** #14 saved usage in all 11 Responses-hosted responses; #13 saved four numeric usage objects. [hosted]; [proto13-host]; [proto14] | **?** A custom hosted agent/provider may omit or incompletely aggregate usage. The default Python Invocations nonstreaming success is `{"response":"..."}` and its streaming protocol has `delta`, then `done` or `error`, not a generic promised usage field. Do not extrapolate Responses evidence to Invocations. |
| Prompt agent, Foundry-managed target/scoring | **D:** `azure_ai_target_completions` with `azure_ai_agent` supports prompt agents; output-item sample and run usage fields are documented. **O:** #14 saved target `sample.usage` on 8/11 items; the three fault items had `TARGET_INVOCATION_FAILED` and null usage. [targets]; [item-api]; [proto14-fault] | **?** Per-physical-call usage, failed-call tokens, and a durable mapping to every planned Attempt or scoring call are not established. `completed` evaluation/item status can coexist with a target error. |
| Hosted agent, Foundry-managed target/scoring | **D:** the same target type supports hosted agents, but Invocations inputs differ. **O:** #14 saved numeric target `sample.usage` for all 11 Responses-hosted items, including fault cases; #13 saved four. [targets]; [proto13]; [proto14] | **?** This is one Responses adapter/framework/model combination, not proof for arbitrary hosted code, Invocations, external tools, or cancellation. |
| Evaluator scoring | **D:** output items have named criterion `results`, whose optional `sample` is an object; the run has model usage. The portal documents separate run-level **Evaluation tokens** and **Target tokens**. **O:** #13's historical `builtin.task_adherence` criterion had separate `results[].sample.usage`; a code-based managed rescore had empty run usage and null item sample usage. [item-api]; [portal]; [proto13-code]; [proto13]; [rescore14] | **?** Neither `results[].sample.usage` nor a complete per-Evaluator scoring ledger is guaranteed for every built-in, custom, endpoint-based, or failed evaluator. If customer-owned scoring itself calls a model, its separately captured response/trace is evidence only when that instrumentation reports usage. Historical model-based scoring is evidence, **not** a recommendation to add a judge. |
| Separate confidence follow-up | **D:** Responses can link a new response via `previous_response_id` or conversation. **O:** #14 recorded confidence in the task response, not in the newly required follow-up. [runtime]; [decision16]; [proto14] | **?** Whether an isolated post-task call with **no further agent action** works for every prompt/hosted and customer/cloud path, and whether its separate usage survives errors, has not been demonstrated. Linking responses is not proof of isolation or of a business Attempt ID. |
| Optional shared generated variants | **D:** managed synthetic generation uses a model, evaluates generated queries, and stores them as a reusable dataset. [synthetic] | **?** No documented, complete generation-call token ledger or call-to-reused-variant/consumer mapping was found. **I:** a customer-controlled generation response can provide usage when returned, but explicit provenance is still needed. |
| Retries, failure, cancellation | **D:** Responses and evaluation runs expose terminal states/errors; streamed requests can fail after HTTP 200. **O:** #13 error responses and null target usage demonstrate missing error-path counts. [response-api]; [responses-guide]; [results-guide]; [proto13-fault] | **?** Counts for every accepted-but-failed, automatically retried, interrupted, or cancelled physical call, including tool-side models. A missing count cannot be assumed zero. |
| CI running either path | **I:** the same APIs can be called from automation and reported artifacts can be retained. [cloud-setup]; [results-guide] | **?** No separate CI token or failure-path guarantee was documented or observed; CI cannot restore missing counters merely by persisting output. |

## What the actual fields mean

### Response and hosted adapters

**D:** The [Responses API][response-api] documents a unique response `id`, second-resolution `created_at`, status (`completed`, `failed`, `cancelled`, `incomplete`, etc.), `error`, `incomplete_details`, model, `previous_response_id`, agent reference, optional metadata, and optional/nullable `usage`. Its example reports `input_tokens`, `output_tokens`, `total_tokens`, `input_tokens_details.cached_tokens`, and `output_tokens_details.reasoning_tokens`; the current schema also lists `cache_write_tokens` and optional cache-write TTL details. The [Python response-usage model][usage-model] describes the same top-level breakdown. Cached input and reasoning output are subdivisions, not extra totals to add. The [optimizer documentation][optimizer] states those subset relationships for that separate optimizer product; it is not evidence that every evaluation call reports those fields. Actual fields and nested breakdowns depend on the response/provider.

**D:** The Responses schema also defines optional `tool_usage` aggregates for billable tools. `image_gen` has input, output, and total tokens, with text/image and optional cached-input breakdowns; `web_search.num_requests` counts requests, not tokens. The schema does not establish that arbitrary external tool-side model calls are included, or whether image-generation counts overlap the outer `usage`; do not add those surfaces without reconciliation. These fields were not observed in the to-do prototype. [response-api].

**D:** `id` identifies a response, not necessarily every physical model call, retry, or tool invocation inside it. A hosted `x-agent-session-id`/sandbox identity and a conversation ID are session/context identifiers, not application Attempt IDs. A hosted agent can invoke multiple providers or tools. Neither the Responses fields nor the hosting guide establishes that every external tool's model use is included in the outer response; retain separately reported tool-side usage only with provenance, or mark that part unknown. [response-api]; [hosted]; [proto13-host].

**D, narrowly scoped:** The Python `agent-framework-hosting-responses` **self-hosting** adapter preserves a valid native `ResponseUsage` or reconstructs it from compatible input, output, cache-read/cache-write, and reasoning counters; incomplete or inconsistent usage can be omitted, and provider-specific details can be lost. This is not a guarantee about Foundry's separate hosted adapter. [self-hosted].

**D, different endpoint:** For managed-compute **Chat Completions**, the documented `stream_options.include_usage` requests final usage; an omitted cache field means unavailable, unlike an explicit zero. This option is not a documented general rule for Responses streams. Responses streaming can send HTTP 200 followed by an error, and loss of the terminal response can leave usage unavailable. Retrieval depends on the response being stored and accessible; a hosted `store=false` request is one-shot. [managed-compute]; [responses-guide]; [hosted].

### Managed Evals and scorer output

**D:** `GET /openai/v1/evals/{eval_id}/runs/{run_id}/output_items/{output_item_id}` documents `eval_id`, `run_id`, item `id`, `datasource_item_id`, `datasource_item`, `status`, criterion `results`, and `sample`. The sample usage schema lists `prompt_tokens`, `completion_tokens`, `cached_tokens`, and `total_tokens`, plus a sample `error` with code/message. A criterion result includes `name`, optional `metric`, status (`completed`, `errored`, `skipped`), and optional object `sample`; its schema does **not** promise a nested usage object. The item's `created_at` is the run creation time, not an individual Attempt timestamp. The reference example is model sampling and does not by itself prove identical complete agent-path behavior. [item-api]; [item-api-detail].

**O versus D:** The item schema marks `sample.usage` required, yet [#13's prompt/cloud fault][proto13-fault] and [#14's fault][proto14-fault] have `sample.usage: null` with `sample.error.code = TARGET_INVOCATION_FAILED`; their run/item can still be `completed`. Handle the observed null defensively, rather than treating the reference as proof that all paths produce numeric counts. Saved item ID `"1"` is reused across different runs, so at minimum scope an output item by `(project/endpoint, eval_id, run_id, output_item_id)` rather than treating `id` alone as globally unique. An application must explicitly connect the source row to its planned Attempt. [proto13]; [proto14].

**D:** A run's `per_model_usage[]` contains `model_name`, `invocation_count`, prompt/completion/total/cached counts; the run also has status, result counts, and error. The Learn reference permits null, and an observed code rescore has an empty list. These are **run-wide**, not a promised per-Attempt or per-Evaluator mapping; model names alone do not identify generation versus target versus scoring. [run-api]; [rescore14].

**D:** The model-target-only optional `estimated_cost.target.model_costs` reports **non-cached** `prompt_tokens` separately from `cached_tokens` and `completion_tokens`; do not apply this field convention to Responses or blindly combine it with another counter. Its limited availability and model-wide scope do not satisfy the decision, and currency reporting remains out of scope. [results-guide].

### Numerical checks from saved prototypes

All figures below are **O**, not estimates or universal guarantees. The small #13 experiment is independently useful because it saved both run and item payloads:

| #13 combination | Baseline target token totals | Fault cases / scoring |
| --- | --- | --- |
| Prompt/customer | `580 + 216 = 796`; `580 + 217 = 797` | Two tool-related HTTP 400 errors; error payload included a `request_id`, but no saved response usage. A request ID is useful provenance, not proof of consumed tokens or a response ID. [proto13] |
| Hosted/customer | Four completed response totals: `6194`, `3912`, `3873`, `5697` | Includes fault cases whose tools independently encountered 503. A completed response did not establish task success or prove coverage of every internal model call. [proto13] |
| Prompt/cloud | Target samples `574 + 213 = 787` and `574 + 200 = 774` | Separate historical `tool-evidence` criterion usage `1862 + 114 = 1976` and `1849 + 158 = 2007`; two faults had null target sample usage. [proto13-cloud]; [proto13-fault] |
| Hosted/cloud | Four target sample totals: `5609`, `2296`, `2265`, `5539` | Includes fault cases; scorer usage is a different field/phase. [proto13-hosted-cloud] |

**Overlap trap:** The first prompt/cloud baseline's run reports `gpt-4.1-mini-2025-04-14 = 1976`, `todo-gpt-41-mini = 787`, `azure_ai_agent_target = 787`, and `azure_ai_evaluation = 1976`. Its target and scorer samples respectively show `787` and `1976`. The paired duplicate counts and category labels strongly indicate overlapping model/deployment and category views in this particular capture: summing all four gives `5526`, twice the two separate sample totals `2763`. Exact aliasing rules are undocumented; equal token counts alone are not an identity proof for other records. [proto13-cloud].

A separate [#14 positive-control run][proof14-overlap] reports model rows `103036` and `81411`, and category `azure_ai_evaluation = 184447`, exactly their sum. That further demonstrates why category and per-model rows cannot safely be blindly added. Its historical rubric-scoring measurements are evidence only, not the proposed evaluator design. The [saved deterministic rescore][rescore14] has `per_model_usage: []` and `sample.usage: null`; an empty list alone is not proof that all unobserved sources consumed zero.

The broader [#14 campaign][proto14] saved 11 Attempts in each combination: direct prompt 8/11 with numeric usage, direct hosted 11/11, managed prompt target 8/11, and managed hosted target 11/11. Its three prompt/cloud missing samples carried target errors. These counts describe captured objects, not a guarantee that every internal call was counted. [#13's resolution][issue13] explicitly says traces were not assessed; separate confidence follow-ups, managed variant generation, cancellation, and billed retries were not live-tested here.

## Identity, provenance, and safe attribution

**I: an accounting design, not a Foundry promise.** Preserve raw evidence with its version, project/endpoint, timestamps, status, and source, then maintain explicit relationships:

1. Assign application-owned IDs for the sweep, each planned Attempt, each Evaluator, and each separately observable logical and physical invocation. Record phase (target, confidence, generation, scoring, or tool-side model), parent/retry relationship, and consumers. Preserve every failed/cancelled/retried record even if it has no usage.
2. Record response IDs, request/error IDs when provided, and response/usage copies as distinct fields; a response ID deduplicates copies of that **same response** within its service scope, not hidden provider calls. `previous_response_id` can show conversation lineage but does not by itself identify a business Attempt or prove that a new call was action-free. Preserve the explicit Attempt-to-follow-up link. [response-api]; [runtime].
3. For managed results, retain `(project, eval_id, run_id, output_item_id)`, `datasource_item_id` and a stable dataset/row-to-Attempt mapping, criterion name, sample and criterion status/error, and counts. A `results[].sample` usage observation is scoped to the particular criterion result, not a universal contract. Do not presume a sample contains the target's response ID or a one-to-one physical-call key. [item-api]; [proto13].
4. For generated/shared evidence, link generation invocation to dataset/version and variants, and variants/target evidence to all consuming Evaluators. Count an evidenced invocation once in the sweep even if several Evaluators consume it; per-Evaluator totals can intentionally overlap and must say so. If the underlying unit or overlap is unresolved, present a partial total and unknown/ambiguous components rather than claiming exactness.
5. Prefer **one proven non-overlapping accounting layer** for a unit: an authoritative response aggregate *or* reconciled model-call spans, not both. Similarly, do not add response usage to its evaluation-sample/trace copy, add a parent aggregate to its child spans, add cached/reasoning subsets to their parents, or sum category and deployment aggregates without a documented mapping. Shared evidence is one source with several consumer edges, not several calls. An explicit numeric zero is different from absent/null/unobservable usage.
6. Distinguish a known subtotal from a complete total, and identify which Attempts/phases are missing or ambiguously overlapped. Only call a value zero when a source explicitly reports zero or a no-model-call path is otherwise actually established. Model names, equal counts, timestamps, a session ID, and temporal proximity are not sufficient deduplication keys.

**D:** Foundry tracing is generally available for prompt, hosted, and external agents. It relies on configured tracing/Application Insights and access; custom instrumentation, sampling, ingestion delay, retention, and privacy/cost considerations affect completeness. Microsoft's [KQL examples][trace-kql] use trace `operation_Id`, span `id`, `operation_ParentId`, `gen_ai.response.id`, and `gen_ai.usage.input_tokens`/`output_tokens` when populated. Parent-child links help only when context is actually propagated; a trace may include aggregate and child counters. The development-status [OpenTelemetry GenAI convention][otel] says one logical span **should** cover all automatic retries, so one span is not necessarily one billable physical attempt. Timestamps help order evidence, not prove identity or uniqueness. [trace]; [trace-setup].

**D:** Foundry can evaluate stored responses by explicit response ID without replaying the target; its `azure_ai_responses` input accepts inline `file_content`, not `file_id`. Re-evaluation may perform scoring but must not be interpreted as another target call. Trace-based evaluation can select a subset of traces and is not a proof of complete accounting. [stored].

**D, specialized preview:** With `Foundry-Features: ModelRouterControls=V1Preview`, model router can return an optional `model_selection_details.model_router_details.routing_trace`, with ordered model attempts, statuses, errors, latency and, for Responses, output IDs. It helps expose some failovers but does not document per-physical-attempt token counts. Do not assume the preview header or metadata is enabled for every agent or evaluation path. [router].

## Remaining unknowns and documentation tensions

1. Exact coverage and overlap semantics for `per_model_usage` aliases, run-level categories, item samples, scorer samples, provider spans, and external tool models; no authoritative cross-layer mapping was located. Identical counts are suggestive only within the observed contexts.
2. Which failures, internal retries, cancellations, stream interruptions, timeouts, and partial executions expose usable counts or request IDs in each protocol; no live billing reconciliation was performed. The required-vs-null sample contradiction above remains unresolved.
3. Whether a **separate, action-isolated** confidence follow-up can be reliably driven and attributed in every path, especially cloud-managed targets and custom hosted agents. Historical in-response self-ratings do not answer that question. [decision16]; [proto14].
4. How managed generation usage is exposed and associated with reusable variants and consumers. The [synthetic guide][synthetic] uses `azure_ai_synthetic_data_gen_preview` / `synthetic_data_gen_preview`, while the [current run schema][run-api] shows `azure_ai_synthetic_data_gen` / `synthetic_data_gen`; compatible API/SDK/version behavior needs independent verification rather than assuming either spelling works everywhere.
5. Whether all selected deterministic/custom scorers are truly model-free, or how their separately reported model calls can be attributed. An empty usage array, missing scorer sample, or portal total does not settle this.
6. Trace coverage and identity propagation for customer code, managed scoring, tools, retry attempts, and CI; prototype traces were not assessed. No assertion here requires creating Application Insights or running a paid validation. [issue13]; [trace-setup].

## Sources

Microsoft documentation was reviewed on 2026-11-26; current pages and SDK `main` can change. Prototype links are pinned to repository commits. Issue comments record user decisions, not platform guarantees.

[issue24]: https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/24
[decision22]: https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/22#issuecomment-7186013607
[decision16]: https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/16#issuecomment-7167414872
[issue13]: https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/13#issuecomment-7123929405
[proto13]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/1548741/prototypes/todo-plumbing/observations-ddc66b24f7.json
[proto13-cloud]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/1548741/prototypes/todo-plumbing/observations-ddc66b24f7.json#L1665-L1775
[proto13-fault]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/1548741/prototypes/todo-plumbing/observations-ddc66b24f7.json#L3240-L3310
[proto13-hosted-cloud]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/1548741/prototypes/todo-plumbing/observations-ddc66b24f7.json#L5359
[proto13-code]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/1548741/prototypes/todo-plumbing/run.py
[proto13-host]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/1548741/prototypes/todo-plumbing/hosted/main.py
[proto13-requirements]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/1548741/prototypes/todo-plumbing/hosted/requirements.txt
[proto14]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/b6c649068e05fe2bd79ac62e82527b2a77da8eeb/prototypes/todo-plumbing/observations-fa75b86f19.json
[proto14-fault]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/b6c649068e05fe2bd79ac62e82527b2a77da8eeb/prototypes/todo-plumbing/observations-fa75b86f19.json#L9902
[proof14-overlap]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/b6c649068e05fe2bd79ac62e82527b2a77da8eeb/prototypes/todo-plumbing/proof-1c2deb86be.json#L10840-L10865
[rescore14]: https://github.com/JoranBergfeld/agent-reliability-evaluators/blob/b6c649068e05fe2bd79ac62e82527b2a77da8eeb/prototypes/todo-plumbing/rescore-38788adc5f.json
[response-api]: https://learn.microsoft.com/en-us/azure/foundry/reference/rest/get-response
[usage-model]: https://learn.microsoft.com/en-us/python/api/azure-ai-agentserver-responses/azure.ai.agentserver.responses.models.responseusage?view=azure-python-preview
[cloud-setup]: https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation
[targets]: https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-targets
[item-api]: https://learn.microsoft.com/en-us/azure/foundry/reference/rest/evals-get-eval-run-output-item
[item-api-detail]: https://ai.azure.com/api-reference/llm/evals/get-eval-run-output-item.md
[run-api]: https://learn.microsoft.com/en-us/azure/foundry/reference/rest/evals-get-eval-run
[results-guide]: https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-results
[portal]: https://learn.microsoft.com/en-us/azure/foundry/how-to/evaluate-results
[hosted]: https://learn.microsoft.com/en-us/agent-framework/hosting/foundry-hosted-agent
[self-hosted]: https://learn.microsoft.com/en-us/agent-framework/hosting/self-hosting/responses
[runtime]: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/runtime-components
[synthetic]: https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-synthetic-data
[stored]: https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-deployed-interactions
[trace]: https://learn.microsoft.com/en-us/azure/foundry/observability/concepts/trace-agent-concept
[trace-setup]: https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-setup
[trace-kql]: https://github.com/microsoft/azure-skills/blob/main/skills/microsoft-foundry/foundry-agent/trace/references/kql-templates.md
[otel]: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md
[managed-compute]: https://learn.microsoft.com/en-us/azure/foundry/concepts/scale-managed-compute
[responses-guide]: https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses
[optimizer]: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-optimizer-costs
[router]: https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/monitor-model-router

# Token usage

After a run, the reliability scorecard reports what was measured of the model tokens the run used: for each Attempt, for each Evaluator and for the Evaluator sweep as a whole. Overlaps and gaps are stated.

Token usage is metadata. It is not a reliability metric, it is in no metric entry, and no acceptance criterion reads it. It changes no metric value and no acceptance assessment.

| | |
| --- | --- |
| Reported in | `token_usage` of the machine-readable scorecard, the "Token usage" section of the report, and `summary.md` |
| Standalone | `evaluator_token_usage(tasks)` for an Evaluator called on its own |
| Declared by the customer | `PreparationUsage`, passed to `run_sweep(preparation_usage=[...])`, only for variations the customer generated with a model |
| Source | [`src/reliability_evaluators/scorecard/usage.py`](../../src/reliability_evaluators/scorecard/usage.py), [`summary.py`](../../src/reliability_evaluators/scorecard/summary.py) |

## What it is and what it is not

- **Tokens, after execution.** Every count is one that a platform or the customer reported after the use it belongs to. There is no estimate beforehand and no planning view.
- **Not currency.** No price is applied. This is not a report of all cost: a tool or a service that reports no model tokens is not in it.
- **No cap.** Nothing is stopped because of usage. The only limits on a sweep are its [execution limits](../sweep/execution-limits.md) and whatever your platform enforces.
- **Missing is not zero.** A count that was not reported is missing, with a reason. It is never counted as zero and never guessed.

## Where a count comes from

A *use* is one thing that could have used a model. There are four kinds.

| Use | Where its count comes from |
| --- | --- |
| The invocation of the agent for an Attempt | `invocation.token_usage.total_tokens`, as the agent target recorded it for the Attempt |
| The confidence request that followed an Attempt | `confidence_invocation.token_usage.total_tokens`, as the agent target recorded it for that request |
| The scoring of an Evaluator | Zero when it is scored in your process. What Foundry reported for each of its managed runs when it is scored there |
| Preparation that you did with a model | What you declare as `PreparationUsage` |

### Attempts

The three Foundry agent targets record what the platform returned about each invocation on the Attempt, including the usage that was reported as `invocation.token_usage`, and a reason in `invocation.token_usage_missing` when there is none. The [prompt agent target](../foundry_prompt_agent/readme.md) and the [hosted agent target](../foundry_hosted_agent/readme.md) record the `usage` object of the response. The [Foundry cloud evaluation target](../foundry_cloud_evaluation/readme.md) records the `usage` of the output item of the Attempt's run.

The count of an invocation is the `total_tokens` of that object, when it is a whole number. Nothing is derived from its parts: a usage that states input and output tokens and no total is missing.

Next to the count the report repeats what identifies the request on the platform, so that a request without a count can still be looked up there:

| Field of the use | Contents |
| --- | --- |
| `response_id` | The identifier of the response, when the platform returned one |
| `request_ids` | The request identifier headers of the answer. Empty when there were none |
| `evaluation_run` | `eval_id`, `run_id` and `output_item_id` of the cloud evaluation run the Attempt was invoked through. `null` for an Attempt that was invoked from your process |
| `source` | Where the count comes from, as the agent target states it. `null` when the count is missing or the target states no source |

**A count is what was reported, and `source` says by whom.** Foundry runs a prompt agent and reports the usage of its response itself. A hosted agent's own code produces its response, usage included, and Foundry passes that on: the count is then the agent's own. The report does not check a count against what a model deployment metered ([ADR 0011](../adr/0011-a-count-is-reported-as-it-was-stated-with-its-source.md)). [Observed live](#observed-live), the usage of a prompt agent's response was what the deployment metered, to the token, and the usage of the fixture's hosted agent was about three times what the deployment metered.

| What the agent target recorded | Reported as |
| --- | --- |
| A `token_usage` with a whole-number `total_tokens` | `reported`, with that count |
| No `token_usage`, and a `token_usage_missing` | `missing`, with that reason |
| No `token_usage` and no reason | `missing`: `the agent target recorded no token usage for the invocation` |
| A `token_usage` without a whole-number `total_tokens` | `missing`: nothing is made of its parts |
| Nothing at all | `missing`: `the agent target recorded nothing of the invocation` |
| Nothing, for an Attempt whose setup failed | `no_model_use`, zero: a customer control failed before the agent was invoked |

An agent target of your own reports usage the same way: return `AgentResponse(invocation={"token_usage": {"total_tokens": 790}, ...})`, and raise `InvocationFailed(..., invocation=...)` for an invocation that failed after it used tokens. Add `"token_usage_source"` to say where the count comes from, and `"response_id"`, `"request_ids"`, or `"eval_id"`, `"run_id"` and `"output_item_id"` to identify the request. Record a usage only where it is one: a prompt agent's response that was cancelled in the background was [observed](#observed-live) to state a usage of 0 tokens after its agent had used a model.

Attempts that failed, timed out, were cancelled or were retried are in the usage like any other, with their execution status and with whatever was reported for them. A retried Attempt and its retry are two Attempts and two uses. An Attempt has usage whether or not a metric could be calculated from it.

A resource measurement of token usage is something else. It is evidence that [resource consistency](../resource_consistency/readme.md) may be calculated from, when its provider declares it reliably sourced. The usage report does not read resource measurements, and resource consistency does not read the usage report.

### Confidence follow-ups

The confidence request that follows a completed Attempt is a request of its own, so it is a use of its own. An agent target reports it by returning `ConfidenceResponse(..., invocation={"token_usage": {...}})`. The sweep keeps that on the Attempt as `confidence_invocation`, apart from `invocation`, so what the request used is attributed to the Attempt it was sent for.

| What happened | Reported as |
| --- | --- |
| No confidence request was sent for the Attempt | `confidence_follow_up` is `null`: there was no use |
| A request was sent and the target recorded its usage | `reported`, with that count |
| A request was sent and the target recorded nothing of it | `missing`: `the agent target recorded nothing of the confidence request` |
| The request failed with `InvocationFailed` | What that exception carries as `invocation`, read as above |
| The response belongs to another Attempt | `missing`. What that response used is not attributed to this Attempt |

The Foundry prompt agent target sends a confidence request and records what the platform returned for it, its `usage` included: see [the confidence rating](../foundry_prompt_agent/readme.md#the-confidence-rating). The Foundry hosted agent target and the Foundry cloud evaluation target do not support an isolated follow-up, so they send none. What a follow-up response reports for each of them was [observed](#the-confidence-follow-up).

### Scoring

The canonical metric definitions are code. An Evaluator that is scored in your process uses no model, so the usage of its scoring is zero, and that zero is known. It is reported with the basis `no_model_use`.

An Evaluator that is scored by [Foundry-managed scoring](../managed_scoring/readme.md) has managed runs of its own: two for a result, and one more each time Foundry did not grade a row and was asked again ([ADR 0006](../adr/0006-managed-results-come-back-as-encoded-parts.md)). Each run is a use of its own. What Foundry reports for a run is kept as it was reported, with the identifiers of the eval and the run, and is read in this order:

1. The usage on the results of the Evaluator's criterion, when the result of every row of the run states a whole-number `total_tokens`. The rows are graded one by one, so the count of the run is the sum over its rows. The per-model rows of the run are then not added: they are another view of the same run.
2. Otherwise, the per-model usage rows of the run. Rows that repeat the same counts under another name are counted once. A saved run reported the same counts under the name of a model and under the name of a category, and summing its four rows doubled the true total ([#24](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/24)).
3. Rows with different counts are listed as reported and are not summed. Another saved run reported two model rows and a category row equal to their sum. Whether rows overlap is not documented, so no count is made of rows that differ, and the count of that run is missing.
4. A run that reports no usage on the result of every row and no per-model row is missing, with that reason. It is not zero. [Observed live](#foundry-managed-scoring), every managed run of a derived evaluator reported an empty list of rows and no usage on its results. That is the absence of a report, not a report of zero.

A zero that Foundry reports is counted as zero. A run that failed is a use like any other, with whatever it reported. When no managed run is known for an Evaluator, its scoring is one use, and it is missing. This adds no model-based judge: the derived evaluators are code.

### Generated variations

The offering generates no task variations. The prompt variants of an experiment are yours, and so is anything else your controls apply. If you generated a variation with a model, the offering cannot know what that used. It is reported only when you declare it:

```python
from reliability_evaluators.scorecard import PreparationUsage
from reliability_evaluators.sweep import run_sweep

scorecard = run_sweep(
    experiments=[add_item_reworded],
    agent_target=my_agent_target,
    metrics=["outcome_consistency", "prompt_robustness"],
    preparation_usage=[
        PreparationUsage(
            preparation_id="prompt-variants",
            description="The two prompt variants of add-item were generated with a model.",
            source="usage logged by our variant script",
            experiment_ids=["add-item"],
            total_tokens=450,
        ),
    ],
)
```

- `experiment_ids` names the experiments whose variation was prepared. Each has to be declared in the sweep and has to declare a variation.
- `total_tokens` is the count. If you know that a model was used and not how much, give `missing_reason` instead: the usage is then reported as missing and the totals that include it are partial.
- A declaration that cannot be reported is rejected before any Attempt is made.

Declared preparation is listed under `shared_preparation`, apart from the Attempts. It is counted once in the sweep total. It is included in the total of every Evaluator that was given an Attempt made under the variation of one of the named experiments.

Without a declaration nothing is reported for generated variations, and nothing is assumed.

## Totals

A total counts each use once. It says how much of what it covers was reported:

| `coverage` | Meaning | `total_tokens` |
| --- | --- | --- |
| `complete` | No use is missing a count | The sum of the reported counts |
| `partial` | Some uses are missing a count | The sum of the reported counts only |
| `unavailable` | Uses are missing a count and none was reported | `null`. It is not zero |

A total also gives `uses_reported`, `uses_missing`, and `missing`: each use without a count, named, with its reason. A use that is known to have used no model, such as scoring by code, is neither reported nor missing. It adds nothing, and it does not turn an unavailable total into a partial one.

### Per Attempt

The usage of an Attempt is its invocation and, where one was sent, its confidence follow-up.

### Per Evaluator

The total of an Evaluator covers every Attempt it was given, each with its confidence follow-up, the shared preparation of what those Attempts were made under, and its own scoring. The Attempts are the ones in `metrics[].attempts_used` of the scorecard.

Evaluators [share Attempts where that is valid](../sweep/several-evaluators.md). An Attempt that two Evaluators were given is in both their totals, with everything it used, the confidence follow-up included. Such a total is labelled:

- `"overlapping": true` and `"additive": false`, with the other Evaluators in `overlaps_with`.
- Adding overlapping totals counts what they share more than once. Do not add them.

An Evaluator whose Attempts were made separately has `"overlapping": false`.

### For the sweep

The sweep total counts every Attempt once, whichever Evaluators were given it and also when none was, every declared preparation once, and the scoring of every Evaluator once.

## Worked example

One Task, three Attempts, with outcome consistency and the Brier-based score selected. The agent target reports usage.

| Attempt | Execution status | Invocation | Confidence follow-up | Attempt total |
| --- | --- | --- | --- | --- |
| `close/attempt-1` | completed | 790 | 120 | 910, complete |
| `close/attempt-2` | failed | not recorded | not sent | unavailable |
| `close/attempt-3` | completed | 812 | sent, nothing recorded | 812, partial |

Known: 790 + 120 + 812 = 1722 tokens. Two uses are missing a count, so 1722 is a partial sum. Both Evaluators are scored by code in your process, which adds zero.

```json
"token_usage": {
  "unit": "tokens",
  "sweep": {
    "coverage": "partial",
    "total_tokens": 1722,
    "uses_reported": 3,
    "uses_missing": 2,
    "missing": [
      {"use": "agent_invocation", "attempt_id": "close/attempt-2",
       "reason": "the agent target recorded nothing of the invocation"},
      {"use": "confidence_follow_up", "attempt_id": "close/attempt-3",
       "reason": "the agent target recorded nothing of the confidence request"}
    ]
  },
  "evaluators": [
    {"metric_id": "outcome_consistency",
     "total": {"coverage": "partial", "total_tokens": 1722, "uses_reported": 3, "uses_missing": 2, "missing": ["..."]},
     "overlapping": true, "additive": false, "overlaps_with": ["brier_based_score"],
     "attempt_ids": ["close/attempt-1", "close/attempt-2", "close/attempt-3"],
     "preparation_ids": [],
     "scoring": [{"basis": "no_model_use", "total_tokens": 0,
                  "reason": "the Evaluator is calculated by code in the customer's process, which uses no model",
                  "scoring_path": "customer_process"}]},
    {"metric_id": "brier_based_score", "...": "the same Attempts, so the same total, labelled the same way"}
  ],
  "shared_preparation": [],
  "attempts": [
    {"attempt_id": "close/attempt-1", "task_id": "close", "execution_status": "completed", "retry_of": null,
     "coverage": "complete", "total_tokens": 910,
     "invocation": {"basis": "reported", "total_tokens": 790, "reason": null,
                    "response_id": "resp_1", "request_ids": {}, "evaluation_run": null, "source": null},
     "confidence_follow_up": {"basis": "reported", "total_tokens": 120, "reason": null,
                              "response_id": null, "request_ids": {}, "evaluation_run": null, "source": null},
     "used_by": ["outcome_consistency", "brier_based_score"]},
    "..."
  ]
}
```

Both Evaluator totals are 1722, and the sweep total is 1722 as well, not 3444.

The scorecard also carries three texts with the numbers, so that a reader of the JSON alone is told what they mean: `meaning`, `scope` and `evaluator_totals`.

## A standalone Evaluator

An Evaluator can be [called on its own](../outcome_consistency/readme.md), without a sweep and without a scorecard. It reports the usage of the Attempts it is given, and their total, from the same grouped evidence:

```python
from reliability_evaluators.evaluators import outcome_consistency
from reliability_evaluators.evidence import group_by_task
from reliability_evaluators.scorecard import evaluator_token_usage

tasks = group_by_task(attempts)
result = outcome_consistency.evaluate(tasks)
usage = evaluator_token_usage(tasks)

usage["total"]     # {"coverage": ..., "total_tokens": ..., "uses_reported": ..., "uses_missing": ..., "missing": [...]}
usage["attempts"]  # one entry per Attempt, as in a scorecard
usage["scoring"]   # one use of zero, known: the Evaluator is code in your process
```

The two calls are independent. The usage is read from the Attempts and not from the metric result, and the metric result holds no usage.

## The CI job summary

The offering ships no CI integration ([ADR 0004](../adr/0004-copy-the-source-distribution.md)). It emits the text of a job summary, and your pipeline publishes it.

- With `artifact_dir`, the sweep writes `summary.md` next to `scorecard.json`, `report.html` and `evidence.json`.
- `render_summary(scorecard)` in `reliability_evaluators.scorecard` returns the same text for a scorecard you already have.

The summary is Markdown. It names the status of the scorecard and lists each metric with its state, its value, its scoring path and its acceptance assessment. Token usage follows under a heading of its own, apart from the metrics: the sweep total with its coverage, a table per Evaluator with the overlap label, the shared preparation, a table per Attempt, each source the agent target states for the counts of the Attempts, and every missing count with its reason. Like the report, it is rendered from the scorecard and from nothing else: every number in it is copied from the scorecard.

Publishing is one step in your pipeline, for example:

```sh
# GitHub Actions
cat artifacts/reliability/summary.md >> "$GITHUB_STEP_SUMMARY"

# Azure Pipelines
echo "##vso[task.uploadsummary]$PWD/artifacts/reliability/summary.md"
```

## Recalculation and rescore

Neither makes an Attempt or calls an agent.

- A [recalculation](../sweep/late-and-corrected-outcomes.md) carries the token usage of the earlier scorecard over. It is what the sweep used, and the recalculation scores by code. The usage has to be what the supplied evidence gives, like the rest of the execution record, and what you declared for preparation is kept as it was reported.
- A [customer-process rescore](../managed_scoring/readme.md#a-customer-process-rescore) adds `customer_process_rescore` to the usage of the Evaluator it rescored: a scoring of its own, by code in your process, that used no tokens. What the managed runs used stays as it was reported, and so do the totals. A rescore does not stand in for a count that Foundry did not report.

## Assumptions

These are assumptions, not confirmed decisions.

- **The CI job summary is text that the offering emits and your pipeline publishes** (specification assumption 21), since no first-party CI integration ships. The file name `summary.md` and its layout are choices of this implementation.
- **Usage for generated variations is reported only when you provide it** (specification open point 24). The decision on token usage counts model-generated task variations, and the offering generates none.
- The count of a use is the `total_tokens` that was reported for it. Usage of a model behind a tool is included only where what reported the count includes it in that total. Nothing is added from other fields of a response, and a count is not checked against what a model deployment metered.
- An Evaluator's total includes the confidence follow-up of an Attempt it was given, also when the Evaluator does not use confidence. The follow-up is part of what that Attempt used, and the total is labelled overlapping.

## Observed live

Observed on 2026-11-27 in Sweden Central, on the disposable fixture in [`live_fixture/`](../../live_fixture/README.md), from a workstation with Azure CLI 2.93.0 and Python 3.15, signed in as a user. The fixture's prompt agent runs `gpt-4.1-mini` (`2025-04-14`) with one OpenAPI tool. Its hosted agent runs the same model deployment through Agent Framework (`agent-framework` 1.26.0, `agent-framework-foundry-hosting` 1.0.0b261112) and declares the `responses` protocol, version `2.0.0`. This records what happened. No documentation was consulted for it, and none of it is a reliability result for any agent. Synthetic data only. This closes the verification that [#52](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/52) asked for, after the research of [#24](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/24).

The interfaces as they worked on that date, on the project endpoint unless it says otherwise:

| Purpose | Request |
| --- | --- |
| Invoke the prompt agent, and read a response of it again | `POST /openai/v1/responses`, `GET /openai/v1/responses/{id}`, with the version in the path and no `api-version` |
| Invoke the hosted agent, and read a response of it again | `POST /agents/{name}/endpoint/protocols/openai/responses?api-version=v1`, `GET` of the same route with `/{id}` |
| Cancel a response that runs in the background | `POST` of either route with `/{id}/cancel` |
| Cloud evaluation and Foundry-managed scoring | `/openai/v1/evals`, `/openai/v1/evals/{eval id}/runs`, `…/runs/{run id}`, `…/cancel`, `…/output_items` |
| What the model deployment metered | Azure Monitor metrics `InputTokens`, `OutputTokens`, `TotalTokens` and `ModelRequests` of the Foundry account, per minute and per `ModelDeploymentName`, read with `az monitor metrics list` |

### What each path exposes

"Metered" is what the deployment's own metrics counted for a minute in which nothing else used the deployment. A request made in one minute was counted under the next minute's timestamp, every time. The metrics are a second count to compare with. Whether they are what is billed was not established.

| Path | Count that is returned | Identifiers | Compared with what was metered |
| --- | --- | --- | --- |
| Prompt agent, customer code, completed | `usage` of the response: `input_tokens`, `output_tokens`, `total_tokens`, with details | Response, two request identifier headers, served model. The response can be read again, with the same usage | Equal to the token, 4 of 4: 854, 224, 343 and 447 tokens. One model request for each invocation, with no, one, two or five tool calls |
| Prompt agent, customer code, a tool answers HTTP 503 | None. The project answers HTTP 400 `tool_user_error` | Request identifier headers only | One model request and 0 tokens. The service had recorded four tool calls and the failed fifth |
| Prompt agent, customer code, the caller stops at a time limit of 2 seconds | Nothing comes back | None | 506 tokens in one model request. The agent had made three tool calls and made no more |
| Hosted agent, customer code, completed | `usage` of the response, in the same fields | Response, agent session, two request identifier headers. The response can be read again on the agent's own route, with the same usage. `GET /openai/v1/responses/{id}` answers HTTP 403 | **Not equal.** 10,527 tokens stated and 3,358 metered in six model requests. See [below](#the-count-of-the-hosted-agent) |
| Hosted agent, customer code, a tool answers HTTP 503 | The response completes, with `usage` | As completed | 10,437 stated and 3,318 metered in six model requests |
| Hosted agent, customer code, the caller stops at a time limit of 5 seconds | Nothing comes back | None | No model request was metered, and the service recorded no call, 75 seconds later |
| Prompt agent, cloud evaluation, completed | `sample.usage` of the output item: `prompt_tokens`, `completion_tokens`, `cached_tokens`, `total_tokens` | Eval, run, output item, response, conversation | Equal to the token: 827 |
| Prompt agent, cloud evaluation, a tool answers HTTP 503 | None. The run and the item report `completed`, the item carries `TARGET_INVOCATION_FAILED`, and the run has no usage row | Eval, run, output item. No response | One model request and 0 tokens |
| Hosted agent, cloud evaluation, completed | `sample.usage` of the output item | Eval, run, output item, response, conversation | Not equal: 10,505 stated and 3,336 metered in six model requests |
| Hosted agent, cloud evaluation, a tool answers HTTP 503 | The item completes, with `sample.usage` | As completed | 10,428 stated and 3,309 metered in six model requests |
| Either agent, cloud evaluation, the run is cancelled | None when the cancellation is answered. Later: see [below](#a-cancelled-run) | Eval and run | The agent ran on and used the model: 806 and 3,359 tokens were metered for two cancelled runs |
| Foundry-managed scoring of a derived evaluator | None: see [below](#foundry-managed-scoring) | Eval and run | Not compared. A derived evaluator calls no model |

**The same invocation, read through both entry points, has the same count.** Once for a prompt agent and once for a hosted agent, the response that an output item names was read from customer code by its identifier. Its `usage` held the numbers of the item's `sample.usage` under the other field names: 601 input, 205 output and 806 total for the prompt agent, and 9,839, 655 and 10,494 for the hosted agent. An Attempt is invoked through one entry point, so nothing is counted through both.

**An Attempt is one response, or one run with one item.** Each has identifiers of its own, so a count is attributed to its Attempt, and a retry is another response or another run. The usage rows of a cloud evaluation run repeat the usage of its one item: twice for a prompt agent, once for a hosted agent. They are not read.

### The count of the hosted agent

Foundry returns the response of a hosted agent as the agent's own code made it, usage included, from customer code and through cloud evaluation alike. The usage the fixture's agent stated was not what the deployment metered:

| Model calls in the response | Stated by the response | Metered | Stated more than metered |
| --- | --- | --- | --- |
| 1: no tool call | 307 in, 3 out, 310 total | 307 in, 3 out, 310 total, one request | 0 |
| 2: one tool call | 1,038 in, 63 out, 1,101 total | 713 in, 33 out, 746 total, two requests | 355 |
| 3: two tool calls | 2,290 in, 150 out, 2,440 total | 1,208 in, 62 out, 1,270 total, three requests | 1,170 |
| 6: the fixture's Task | 9,839 in, 688 out, 10,527 total | 3,125 in, 233 out, 3,358 total, six requests | 7,169 |

With one model call the two are equal. With more, the numbers fit a count that adds to every model call the tokens of the calls before it in the same response. With two model calls that is exact: a first call of 325 in and 30 out is counted twice. With three and six it is consistent with the numbers and was not proven. What in the agent's code produces this was not looked for, and it was seen with this one agent and these package versions only.

The usage report gives the count as the response stated it, because it is the only count there is, and says in `source` that it is the agent's own. It is not corrected and not replaced ([ADR 0011](../adr/0011-a-count-is-reported-as-it-was-stated-with-its-source.md)). **Do not read the total of a sweep on a hosted agent as tokens the deployment processed without comparing it with your own deployment's metrics.** For the fixture's Task it was 3.1 times what was metered.

A prompt agent is different. Foundry makes the model calls itself, as one model request per invocation whatever the number of tool calls, and the usage of the response was what the deployment metered in every isolated invocation.

### Attempts that did not complete

Observed by hand, one request each unless it says otherwise:

- **A failing tool of a prompt agent exposes no count in any way that was tried.** Not streamed: HTTP 400 and no response. Streamed: a `response.created` event names a response, an `error` event follows, and the `response.failed` event carries `"usage": null`. In the background: the response is returned as `queued` with an identifier, and read again it has the status `failed`, the tool calls up to the failed one and no usage.
- **A response that is cut short states a usage.** With `max_output_tokens: 60` the prompt agent's response had the status `incomplete` and a usage of 399 tokens. With `max_output_tokens: 16` the hosted agent's had the status `incomplete` and a usage of 1,166. An agent target records such a response as a failed invocation that keeps its usage, and the report counts it. That is covered by the default tests against a fake project. No target can ask for a limit on the output, so it was not seen through the sweep.
- **A prompt agent's response that is cancelled in the background states a usage of zero, which is not what was used.** Twice, the cancellation was answered with the status `cancelled` and a usage of 0 input, 0 output and 0 total tokens, and the response still stated that 45 seconds later. Both times the service recorded all five tool calls, the second time four of them after the cancellation was answered. The deployment metered 816 tokens for the second, in a minute with nothing else. No agent target here cancels a response, so this zero is never recorded by them.
- **A hosted agent's response that is cancelled in the background states no usage.** Cancelled a few seconds after it was made, it had the status `cancelled` and `"usage": null`, also 45 seconds later. The agent had made four tool calls and made no more. A cancellation that came after the response had completed was refused with HTTP 400.
- **A caller that stops waiting learns nothing.** No response identifier comes back before the response does, so nothing identifies the invocation and nothing can be read later.

#### A cancelled run

A cloud evaluation run was cancelled by hand 2 to 25 seconds after it was made, 14 times: eight for the prompt agent and six for the hosted agent. The live check cancelled eight more at its time limits. Every cancellation was answered with the run as `canceled`, with no output item and no usage row. In all 14 made by hand the agent went on, and the service had all five tool calls afterwards.

- **20 of the 22 runs never reported anything.** The twelve made by hand were read again 100 seconds later, and the eight of the check when its sweep had ended and again minutes after that. Each was `canceled` with no output item and no usage row.
- **Two did.** The first run cancelled for each agent type, read again 80 seconds later, was still `canceled` and had an output item with the status `completed`, the usage of the invocation and the run's usage rows: 806 tokens for the prompt agent, which is what the deployment metered, and 10,578 for the hosted agent. Twelve later runs were cancelled in the same way, four of them with nothing else running in the project, and none of them reported anything. What made the difference was not found.

So an Attempt whose run had not ended at the time limit is missing, with the reason that its result was not read. The usage names the eval and the run in `evaluation_run`, so the run can be looked at later. Mostly there will be nothing to find.

### The confidence follow-up

The prompt agent target sends the follow-up ([ADR 0009](../adr/0009-prompt-agent-follow-up-rests-on-tool-choice-none.md)). The other two targets send none, so what a follow-up reports for them was observed by hand, once each, with a response that continued the Attempt's with `tool_choice: "none"`.

| Attempt | Follow-up request | Usage of the follow-up response | Usage of the Attempt, read again afterwards |
| --- | --- | --- | --- |
| Prompt agent, customer code | `previous_response_id` | 729 in, 2 out, 731 total | Unchanged: 810 |
| Hosted agent, customer code | `previous_response_id`, on the agent's route | 770 in, 2 out, 772 total | Unchanged: 10,427 |
| Prompt agent, cloud evaluation | `previous_response_id` of the response the output item names, from customer code | 730 in, 2 out, 732 total | Unchanged: the run and its item still reported 806 |
| Hosted agent, cloud evaluation | The `conversation` the output item names, on the agent's route | 767 in, 2 out, 769 total | Unchanged: the run and its item still reported 10,494 |

- **A follow-up is a response of its own with a usage of its own**, so it is attributed to the Attempt it continues without touching that Attempt's count. Its input tokens include the conversation of the Attempt, which the model reads again. That is use, not a count made twice.
- For the hosted agent through cloud evaluation, `previous_response_id` did not work: the response came back with the status `failed`, `Cannot find an existing agent session for previous_response_id=…`, and `"usage": null`. A hosted follow-up that names only the `agent_session_id` started a new conversation.
- In none of these follow-ups did the service record a call.
- In the live check the prompt agent target's follow-up was a response of its own for each of the Attempts, and its count and the Attempt's were each what the platform gave when the two responses were read again afterwards.

### Foundry-managed scoring

Every managed run of the derived evaluator of outcome consistency reported nothing of its usage: `per_model_usage` was an empty list, the result of the criterion had `"sample": null` in every output item, and the item's own `sample.usage` was null. That was so in all seven runs of three sweeps, of 2 to 39 rows each, one of them a run that asked again for rows Foundry had not graded. The usage report shows each such run as missing, with the reason that Foundry reported no usage, and the sweep total as a partial sum. It is not shown as zero.

The rules for usage rows could not be observed on a managed run of the offering, because such a run reports no row. They were compared with three runs made by hand that do use a model, with the built-in evaluator `builtin.task_adherence` as a criterion. No judge is part of the offering, and these runs score nothing.

| Run | Usage rows | On the items |
| --- | --- | --- |
| The judge alone, three rows | `gpt-4.1-mini-2025-04-14` 4,595 and `azure_ai_evaluation` 4,595 | The judge's results: 1,532, 1,514 and 1,549, which is 4,595. The deployment metered 4,595 |
| A prompt agent as the target, with the judge | `gpt-4.1-mini-2025-04-14` 2,016, `todo-gpt-41-mini` 821, `azure_ai_agent_target` 821, `azure_ai_evaluation` 2,016 | The target 821, the judge's result 2,016 |
| A hosted agent as the target, with the judge | `gpt-4.1-mini-2025-04-14` 1,980, `azure_ai_agent_target` 10,496, `azure_ai_evaluation` 1,980 | The target 10,496, the judge's result 1,980 |

Every use appears under the name of a category, and again under the name of a model or a deployment where Foundry names one. Rows that repeat the same counts were one use, and the usage on the results added up to it. Adding all rows of the second run gives 5,674 for a use of 2,837. So counting exact repeats once, and not summing rows that differ, held in these three runs.

### The live check

`tests/live/test_token_usage_live.py` runs one sweep for each agent type through each entry point, with outcome consistency scored in the customer's process, and reads the token usage of its scorecard. Each sweep makes Attempts that end in every way the fixture can make them end. A count in the report is compared with what the platform says when the check asks it again: the response read back by its identifier, or the output item read back from the run. Sums are added in the check from those counts.

| What was done | Prompt agent, customer code | Prompt agent, cloud evaluation | Hosted agent, customer code | Hosted agent, cloud evaluation |
| --- | --- | --- | --- | --- |
| Every tool works, two Attempts | `completed`, reported | `completed`, reported | `completed`, reported | `completed`, reported |
| The update tool answers HTTP 503 | `failed`, missing: `the Foundry project returned no response, so no usage was reported` | `failed`, missing: `the output item of the cloud evaluation run carried no usage` | `completed`, reported | `completed`, reported |
| The update tool answers HTTP 503 in the Attempt and works in its retry | `retried`, missing as above. Its retry: `completed`, reported | `retried`, missing as above. Its retry: `completed`, reported | `completed`, reported. No retry is made | `completed`, reported. No retry is made |
| A time limit of 2, 10, 5 and 15 seconds, with one retry | `retried` and `timed_out`, both missing: `the agent target recorded nothing of the invocation` | `retried` and `timed_out`, both missing: `the result of the cloud evaluation run was not read, so no usage is known` | `retried` and `timed_out`, both missing: `the agent target recorded nothing of the invocation` | `retried` and `timed_out`, both missing as for the prompt agent |
| The update tool answers HTTP 503 and the sweep is cancelled while the Attempt is in flight | `cancelled`, missing: `the Foundry project returned no response, so no usage was reported` | `cancelled`, missing: `the output item of the cloud evaluation run carried no usage` | `completed`, reported | `completed`, reported |

It ran twice on the fixture and passed both times: 19 checks in 20 minutes 18 seconds, and 24 checks in 19 minutes 42 seconds. Two checks are skipped each time, because only a cloud evaluation run has usage rows. The first run was made before the usage report stated the source of a count and named the evaluation run, and before the confidence follow-up of the prompt agent target was merged into the sweep. The second was made with the source and the check as they are here, and has the checks of those. That is 60 Attempts in the sweeps of the table, 30 in each run.

- **Every Attempt was in the usage with the execution status and the count or the reason of the table**, both times.
- **Every reported count was the one the platform gave when it was asked again**: 16 counts in each run. A prompt agent's response and a hosted agent's response were read back by their identifiers, and an output item from its run.
- **Each sweep total was the sum of the reported counts, a partial sum that names every missing count.** For the prompt agent three counts were reported and five were missing: 2,755 and 2,760 tokens from customer code, 2,740 and 2,767 through cloud evaluation. For the hosted agent five were reported and two were missing: 56,507 and 55,755 from customer code, 56,133 and 56,082 through cloud evaluation. The Evaluator's total was the same, and its scoring by code added a known zero.
- **A count was 875 to 995 tokens for the prompt agent and 10,974 to 11,573 for the hosted agent**, for the same Task with the same model. The second is the hosted agent's own count, as above.
- **An Attempt without a count kept what identifies it.** A failed invocation of the prompt agent from customer code kept both request identifier headers. An Attempt through cloud evaluation named its eval, its run and, where the run had returned one, its output item. An Attempt that was stopped at a time limit from customer code kept nothing, because nothing came back.
- **The usage rows of a cloud evaluation run repeated the usage of its one item**, twice for the prompt agent and once for the hosted agent, for all 16 Attempts that reported a count through cloud evaluation. A run whose target failed had no row.
- **The confidence follow-up of the prompt agent was a use of its own**, in the second run. For two Attempts, the follow-up's count (895 and 893 tokens) and the Attempt's (898 and 896) were each what the platform gave for the two responses when they were read again after the follow-up. The four counts made a complete sweep total of 3,582.
- **Foundry-managed scoring reported no usage.** A sweep of two Attempts with outcome consistency scored in Foundry made three managed runs the first time and two the second. Each was missing with its reason, and the sweep total was the count of the two Attempts as a partial sum: 1,763 and 1,746.

**What could not be induced.** A failing tool does not fail the invocation of a hosted agent, so through the sweep no hosted Attempt ended as `failed`, none was retried after a fault, and none ended as `cancelled`: the sweep records `cancelled` only for an invocation that fails after the cancellation was asked for, and a time limit ends as `timed_out` whether or not the sweep was cancelled. The only hosted Attempts that did not complete were the ones stopped at a time limit. A hosted response with the status `failed` or `incomplete` was seen by hand only. The sweep cannot interrupt an invocation, so cancelling an Attempt on the platform while it runs was tried by hand only, as above.

### Resources

Two resource groups were created for this work, in the subscription named `Sandbox`:

| Resource group | What it was for |
| --- | --- |
| `rg-are-52-2b5fa34d` | Never became a fixture. Its model deployment was refused with `(InvalidResourceProperties) Failed to validate policies for model gpt-4.1-mini/2025-04-14`, as one was for the prompt agent target. Provisioning again a few minutes later, with nothing changed, succeeded. |
| `rg-are-52-4918529c` | The fixture for everything above. |

The first was deleted by the provisioning that failed. The second was deleted with the fixture's `teardown`, which took 29 minutes. Both soft-deleted Foundry accounts were purged. Afterwards a separate `az group exists` returned `false` for each of the two, and no soft-deleted account of either was listed. The evals and the runs made above were in the fixture's project and went with it.

## Not verified

- **Whether what the deployment's metrics count is what is billed**, and why a prompt agent's invocation that failed on a tool was metered as a request with no tokens.
- **Why the hosted agent's response stated more than was metered**, and what a hosted agent built with anything else states. The comparison was made with one agent, one model and one Task.
- **A hosted agent that serves the Invocations protocol.** It is not supported, and its usage is unverified.
- **Tool-side model usage.** The fixture's tool is a plain service. Whether the usage of a response includes a model that a tool calls was not observed.
- **A confidence follow-up sent by a target for a hosted agent or through cloud evaluation.** Neither target sends one. What is above was observed by hand.
- **Usage for generated variations.** The offering generates none, and what a customer declares is reported as declared.
- **Usage rows of a managed run that uses a model.** The offering makes none. The three runs above were made by hand.
- **More than one Attempt at a time**, a run with more than one row, and a failed managed run: what it reports of its usage was not looked at live.
- The self-contained form of the live check, in which it provisions and tears down its own fixture. The check was run on a fixture provisioned beforehand.

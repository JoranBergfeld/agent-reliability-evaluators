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

The two Foundry agent targets record what the platform returned about each invocation on the Attempt, including the `usage` object of the response as `invocation.token_usage`, and a reason in `invocation.token_usage_missing` when there is none. See [the Foundry prompt agent target](../foundry_prompt_agent/readme.md) and [the Foundry hosted agent target](../foundry_hosted_agent/readme.md). The [Foundry cloud evaluation target](../foundry_cloud_evaluation/readme.md) records the `usage` of the output item of the Attempt's run the same way.

The count of an invocation is the `total_tokens` of that object, when it is a whole number. Nothing is derived from its parts: a usage that states input and output tokens and no total is missing. The identifiers the platform returned for the request, `response_id` and `request_ids`, are repeated next to the count, so that a request without a count can still be looked up on the platform.

| What the agent target recorded | Reported as |
| --- | --- |
| A `token_usage` with a whole-number `total_tokens` | `reported`, with that count |
| No `token_usage`, and a `token_usage_missing` | `missing`, with that reason |
| No `token_usage` and no reason | `missing`: `the agent target recorded no token usage for the invocation` |
| A `token_usage` without a whole-number `total_tokens` | `missing`: nothing is made of its parts |
| Nothing at all | `missing`: `the agent target recorded nothing of the invocation` |
| Nothing, for an Attempt whose setup failed | `no_model_use`, zero: a customer control failed before the agent was invoked |

An agent target of your own reports usage the same way: return `AgentResponse(invocation={"token_usage": {"total_tokens": 790}, ...})`, and raise `InvocationFailed(..., invocation=...)` for an invocation that failed after it used tokens.

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

The two Foundry agent targets do not support an isolated follow-up in this version, so they send no confidence request.

### Scoring

The canonical metric definitions are code. An Evaluator that is scored in your process uses no model, so the usage of its scoring is zero, and that zero is known. It is reported with the basis `no_model_use`.

An Evaluator that is scored by [Foundry-managed scoring](../managed_scoring/readme.md) has managed runs of its own: two for a result, and one more each time Foundry did not grade a row and was asked again ([ADR 0006](../adr/0006-managed-results-come-back-as-encoded-parts.md)). Each run is a use of its own. What Foundry reports for a run is kept as it was reported, with the identifiers of the eval and the run, and is read in this order:

1. The usage on the results of the Evaluator's criterion, when the result of every row of the run states a whole-number `total_tokens`. The rows are graded one by one, so the count of the run is the sum over its rows. The per-model rows of the run are then not added: they are another view of the same run.
2. Otherwise, the per-model usage rows of the run. Rows that repeat the same counts under another name are counted once. A saved run reported the same counts under the name of a model and under the name of a category, and summing its four rows doubled the true total ([#24](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/24)).
3. Rows with different counts are listed as reported and are not summed. Another saved run reported two model rows and a category row equal to their sum. Whether rows overlap is not documented, so no count is made of rows that differ, and the count of that run is missing.
4. A run that reports no usage on the result of every row and no per-model row is missing, with that reason. It is not zero. A run of a code evaluator was observed, in the earlier prototypes, to report an empty list of rows and no usage on its results. That is the absence of a report, not a report of zero.

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
                    "response_id": "resp_1", "request_ids": {}},
     "confidence_follow_up": {"basis": "reported", "total_tokens": 120, "reason": null,
                              "response_id": null, "request_ids": {}},
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

The summary is Markdown. It names the status of the scorecard and lists each metric with its state, its value, its scoring path and its acceptance assessment. Token usage follows under a heading of its own, apart from the metrics: the sweep total with its coverage, a table per Evaluator with the overlap label, the shared preparation, a table per Attempt, and every missing count with its reason. Like the report, it is rendered from the scorecard and from nothing else: every number in it is copied from the scorecard.

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
- The count of a use is the `total_tokens` its platform reported. Usage of a model behind a tool is included only where the platform includes it in that total. Nothing is added from other fields of a response.
- An Evaluator's total includes the confidence follow-up of an Attempt it was given, also when the Evaluator does not use confidence. The follow-up is part of what that Attempt used, and the total is labelled overlapping.

## What is not verified

Which paths expose token counts has not been verified live here. That is [#52](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/52). What is known comes from saved runs ([#24](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/24)):

- Returned Responses carried usage. Invocations that the platform answered with an error carried none: three of eleven prompt-agent Attempts in a saved campaign.
- Whether a failed, cancelled or interrupted call reports the tokens it used is not established. Such an Attempt is reported with whatever was recorded, and otherwise as missing.
- A hosted agent that serves the Invocations protocol is not supported, and its usage is unverified.
- Scoring usage is not guaranteed per Evaluator. A managed run of a code evaluator reported none in the earlier prototypes. What the runs of a derived evaluator report was not looked at.
- How per-model usage rows alias each other is not documented. The rule here counts exact repeats once and refuses to sum rows that differ.
- The usage of a confidence follow-up has not been observed on either Foundry agent type: neither target sends one in this version.

Attempts invoked through Foundry cloud evaluation are reported like any other. The count is the `total_tokens` of the usage that the output item of the Attempt's run carried. An Attempt whose target failed, or whose run had not ended at the time limit, is missing with a reason. The run's own usage rows are kept on the Attempt as `invocation.run_per_model_usage` and are not read here: for a prompt agent they were observed to count the same tokens twice. What was observed is in [the documentation of that entry point](../foundry_cloud_evaluation/readme.md#observed-live). The counts of the two entry points were never compared on the same invocation.

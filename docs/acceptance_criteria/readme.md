# Acceptance criteria

An acceptance criterion is your own condition on one reliability metric. The reliability scorecard reports, for each criterion you declare, whether it was **met**, **not met**, or **not assessed** with a reason.

The offering has no threshold of its own. A metric without a criterion has no acceptance verdict at all.

| | |
| --- | --- |
| Declared with | `AcceptanceCriterion`, passed to `run_sweep(acceptance_criteria=[...])` |
| Reported in | `metrics[].acceptance` of the machine-readable scorecard |
| Source | [`src/reliability_evaluators/scorecard/acceptance.py`](../../src/reliability_evaluators/scorecard/acceptance.py) |

## Declaring a criterion

```python
from reliability_evaluators.scorecard import AcceptanceCriterion, to_json
from reliability_evaluators.sweep import run_sweep

scorecard = run_sweep(
    tasks=tasks,
    agent_target=my_agent_target,
    metrics=["outcome_consistency"],
    acceptance_criteria=[
        AcceptanceCriterion(
            metric_id="outcome_consistency",
            condition={"value": {"at_least": 0.8}, "minimum_counts": {"tasks": 2}},
        ),
    ],
)
print(to_json(scorecard))
```

A metric has at most one criterion. The `condition` is JSON-compatible data, so it can be kept in a file next to your pipeline, and it is repeated in the scorecard.

The offering assesses a condition with either or both of these parts. Every part must hold for the criterion to be met.

| Part | Meaning |
| --- | --- |
| `value` | Compares the metric value. Comparisons: `at_least` (≥), `greater_than` (>), `at_most` (≤), `less_than` (<). Several can be given, for example `{"at_least": 0.2, "at_most": 0.9}`. |
| `minimum_counts` | Requires at least that many **valid** items of an evidence unit the metric counts, for example `{"tasks": 5, "attempts": 25}`. Valid is the evidence the value was calculated from, not everything that was declared. The units are the keys of the metric's `counts`. |

Higher is not better for every metric. Read the metric's `definition.interpretation` before choosing a direction.

## Worked example

Two Tasks. `close` succeeds on 4 of 5 Attempts, so its Task value is (2 × 0.8 − 1)² = 0.36. `archive` fails on all 3 Attempts, so its Task value is 1. Outcome consistency is (0.36 + 1) / 2 = 0.68, calculated from 2 valid Tasks.

The criterion above requires a value of at least 0.8 and at least 2 valid Tasks. The count holds and the value does not, so the criterion is not met:

```json
"acceptance": {
  "condition": {"value": {"at_least": 0.8}, "minimum_counts": {"tasks": 2}},
  "status": "not_met",
  "reason": null,
  "assessed_by": "offering",
  "provenance": null,
  "checks": [
    {"subject": "value", "comparison": "at_least", "threshold": 0.8, "observed": 0.6799999999999999, "met": false},
    {"subject": "counts.tasks.valid", "comparison": "at_least", "threshold": 2, "observed": 2, "met": true}
  ]
}
```

| Field | Content |
| --- | --- |
| `condition` | The condition you declared. |
| `status` | `met`, `not_met` or `not_assessed`. |
| `reason` | Why the criterion was not assessed. `null` otherwise. |
| `assessed_by` | `offering` when the offering evaluated the condition, `customer` when your own assessment was recorded, `null` when not assessed. |
| `provenance` | The provenance of your own assessment. `null` otherwise. |
| `checks` | One entry per comparison the offering made: what was compared, the threshold, the observed number and whether it held. |

A metric with no criterion has `"acceptance": null`.

## When a criterion is not assessed

Only a metric that is available, with an authoritative value, can be met or not met. Everything else is not assessed, and the reason says why. Missing evidence can neither open nor close your gate.

| Situation | `reason` |
| --- | --- |
| The metric is an unavailable measurement | `the metric is an unavailable measurement: ` followed by the measurement's own reason |
| The metric is not applicable | `the metric is not applicable: ` followed by its reason |
| The metric is not configured | `the metric is not configured` |
| The metric has no result because its Attempts were cut short | `the metric has no result because of an execution failure: ` followed by the failure's reason; see [execution limits and partial scorecards](../sweep/execution-limits.md#metrics-in-a-partial-scorecard) |
| The value is an unverified result | `the value is an unverified result: its fidelity to the evaluator-computed value has not been established` |
| The condition is not one the offering assesses | `unsupported criterion: ` followed by what was not understood |
| Your own assessment failed or returned no verdict with provenance | `the customer-supplied assessment ` followed by what went wrong |

This holds whatever the criterion says. A criterion that only requires a minimum count is still not assessed when the measurement is unavailable.

An unsupported criterion never passes. A condition with one part the offering understands and one it does not is unsupported as a whole, so the understood part cannot pass on its own. Examples of unsupported conditions: free text, an unknown part such as `{"every_task": ...}`, an unknown comparison such as `between`, a threshold that is not a number, a minimum count that is not a whole number, and a count of a unit the metric does not report.

An unsupported criterion is reported as unsupported even when the measurement is also unavailable, so that you can correct the criterion without another run.

## Supplying your own assessment

For a condition the offering does not assess, you can decide yourself and have the scorecard record your decision with its provenance. Give the criterion a `customer_assessment`: a function that receives a copy of the metric's scorecard entry and returns a `CustomerAssessment`.

```python
from reliability_evaluators.scorecard import AcceptanceCriterion, CustomerAssessment


def no_task_below_0_3(metric):
    met = all(task["value"] >= 0.3 for task in metric["details"]["per_task"])
    return CustomerAssessment(met=met, provenance="release-gate.py at 4f2c1d9, rule 'no Task below 0.3'")


criterion = AcceptanceCriterion(
    metric_id="outcome_consistency",
    condition="no Task value below 0.3",
    customer_assessment=no_task_below_0_3,
)
```

With the Task values 0.36 and 1 of the worked example, the scorecard records:

```json
"acceptance": {
  "condition": "no Task value below 0.3",
  "status": "met",
  "reason": null,
  "assessed_by": "customer",
  "provenance": "release-gate.py at 4f2c1d9, rule 'no Task below 0.3'",
  "checks": []
}
```

- `provenance` is required. It says who or what decided, and from what.
- When a `customer_assessment` is given, the offering does not evaluate the `condition` itself. The condition is recorded as the statement of what you assessed.
- Your function is not called for a metric that is unavailable, not applicable, not configured or unverified. Such a metric is not assessed, as above.
- A function that raises, or that returns anything other than a `CustomerAssessment` with a `True` or `False` verdict and a provenance, leaves the criterion not assessed. The measurements in the scorecard are kept.
- Your function receives a copy. It cannot change the measurement.

## Deciding a gate in a pipeline

The machine-readable scorecard carries the condition, the status, the reason and every check. A pipeline needs nothing else, and does not need this library:

```python
import json
import sys

with open("scorecard.json") as file:
    scorecard = json.load(file)

assessments = {
    metric["metric_id"]: metric["acceptance"] for metric in scorecard["metrics"] if metric["acceptance"]
}
for metric_id, assessment in assessments.items():
    print(metric_id, assessment["status"], assessment["reason"] or "")

# Your policy, not the offering's. This one fails the build unless every criterion is met.
sys.exit(0 if all(assessment["status"] == "met" for assessment in assessments.values()) else 1)
```

The scorecard has no overall verdict. Whether a criterion that was not assessed blocks a release is your decision.

## What acceptance is not

- **Not a task outcome.** A task outcome says whether one Attempt succeeded and is reported under `attempts[].task_outcome`. Acceptance says whether a metric meets your criterion and is reported under `metrics[].acceptance`. An agent that fails every Attempt is perfectly consistent, so a criterion of outcome consistency at least 0.9 is met while every task outcome is a failure.
- **Not a platform pass/fail label.** An acceptance assessment is produced only from a criterion you declared. The scorecard carries no Foundry pass/fail label today. When one is captured it is a platform diagnostic, reported apart from `acceptance`.
- **Not a threshold of the offering.** No criterion is declared for you and no default applies.

## Declarations that are rejected

These are rejected with an error before any Attempt is made, because the scorecard could not report them:

- a criterion for something that is not one of the twelve reliability metrics;
- more than one criterion for the same metric;
- a condition that is not JSON-compatible data, including a threshold that is NaN or infinite.

## Numbers

A comparison uses the metric value exactly as recorded in the scorecard, an IEEE 754 double-precision number. Nothing is rounded and no tolerance is applied. The compared number is shown as `observed`.

A value can therefore differ from the fraction you calculate by hand in its last digit, and a threshold placed exactly on that fraction can fall on either side. In the worked example the value is recorded as 0.6799999999999999, so `at_least: 0.68` is not met. Choose thresholds with that in mind.

## Assumptions

This comes from the specification and is not a confirmed decision. It is implemented as written.

1. **The first iteration supports a comparison against a metric value plus minimum evidence counts** (specification assumption 20). Any other criterion is reported as not assessed, unless you supply your own assessment for it. There is no full acceptance-criteria language.

Choices made within that assumption:

- A minimum evidence count is compared with the `valid` count of the unit.
- A metric has one criterion. Its parts are combined with "and"; there is no "or".

## What is verified

Tests drive `run_sweep` and assert on the scorecard. They cover met, not met, every comparison at its boundary, minimum counts, unsupported criteria, customer-supplied assessments, and the unavailable and not configured states.

A value from [Foundry-managed scoring](../managed_scoring/readme.md) is an unverified result, and a sweep test shows that a criterion on it is not assessed.

No Evaluator returns "not applicable" yet, so that row of the table above is implemented and **not exercised by a test**. It falls under the same rule as the tested states: anything other than an available metric with an authoritative value is not assessed.

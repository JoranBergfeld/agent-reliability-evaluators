# Several Evaluators in one sweep

One Evaluator sweep can score several metrics. It returns one reliability scorecard with each selected metric reported on its own and every other metric as not configured. There is no combined score.

Attempts cost money, so the sweep does not make an Attempt twice when one Attempt can serve two Evaluators. It also never scores an Evaluator from Attempts that were made for something else. This page says when Attempts are shared, when they are made separately, and how the scorecard shows it.

| | |
| --- | --- |
| Selected with | `run_sweep(metrics=[...])`: a metric identity, or a `ConfiguredEvaluator` |
| Reported in | `metrics[].attempts_used` of the machine-readable scorecard |
| Source | [`src/reliability_evaluators/sweep/reuse.py`](../../src/reliability_evaluators/sweep/reuse.py) |

## Selecting Evaluators

```python
from reliability_evaluators.sweep import run_sweep

scorecard = run_sweep(
    tasks=tasks,
    agent_target=my_agent_target,
    metrics=[
        "outcome_consistency",
        "distributional_trajectory_consistency",
        "sequential_trajectory_consistency",
        "constraint_compliance",
    ],
    action_evidence=native_tool_calls,
)
```

A metric can be selected once. Selecting it twice is rejected before any Attempt is made, because a scorecard reports each metric once.

## When Attempts are shared

Each Evaluator names what it needs its Attempts to be made with. Two Evaluators share Attempts when everything they both name is the same. What only one of them names is collected for that one and does not change what the other is scored from.

| Evaluator | What it needs of its Attempts |
| --- | --- |
| Outcome consistency | Nothing in particular. Every completed Attempt has a task outcome. |
| Constraint compliance | Nothing in particular. The violation assessor belongs to the Task, so every Attempt at the Task is assessed the same way. |
| Distributional trajectory consistency | Action evidence from one declared evidence source, read by one declared recorder. |
| Sequential trajectory consistency | The same. |

With one `action_evidence` declaration for the whole sweep, as above, nothing differs. All four Evaluators are scored from the same Attempts, and each planned Attempt is made once.

The action types are not part of what is matched. They are applied when a metric is calculated and do not change what is recorded for an Attempt. Two Evaluators that read the same recording with different action types share Attempts.

Experimental conditions are not declared in this version. When they are, the condition an Evaluator needs becomes one more thing that has to be the same.

## When separate Attempts are made

Configure an Evaluator with a declaration of its own when it needs something other than what the sweep declares:

```python
from reliability_evaluators.sweep import ActionEvidenceDeclaration, ConfiguredEvaluator, run_sweep

native_tool_calls = ActionEvidenceDeclaration(
    evidence_source="native tool calls", record=read_tool_calls, action_types=action_types
)
service_side_effects = ActionEvidenceDeclaration(
    evidence_source="service-side effects", record=read_service_log, action_types=action_types
)

scorecard = run_sweep(
    tasks=tasks,
    agent_target=my_agent_target,
    metrics=[
        "outcome_consistency",
        "distributional_trajectory_consistency",
        ConfiguredEvaluator(metric_id="sequential_trajectory_consistency", action_evidence=service_side_effects),
    ],
    action_evidence=native_tool_calls,
)
```

An Attempt holds one action recording from one source. Sequential trajectory consistency needs a different one here, so the sweep attempts every Task again for it. Outcome consistency needs nothing in particular and shares the first Attempts.

The rule, in the order the metrics are given:

1. An Evaluator joins the first set of Attempts whose requirements match its own.
2. Otherwise it starts a set of its own, and every Task is attempted again, as often as the Task declares.

The sets are made one after another: every Task for the first set, then every Task for the second. The first set's Attempts are identified as `<task>/attempt-<n>`, the same as in a sweep with one set. Later sets carry their number: `<task>/set-2/attempt-<n>`. An Attempt's `task_id` is the Task either way.

Two recordings are the same when they have the same `evidence_source` and the same `record` callable. A different callable is a different requirement, also when it happens to read the same data.

`ConfiguredEvaluator(action_evidence=...)` is rejected for an Evaluator that does not use action evidence. The declaration would have no effect, and the sweep does not accept one silently.

## What the scorecard shows

Every configured metric says which Attempts its Evaluator used:

```json
{
  "metric_id": "sequential_trajectory_consistency",
  "state": "available",
  "value": 1.0,
  "evidence": {"attempt_ids": ["close/set-2/attempt-1", "close/set-2/attempt-2"]},
  "attempts_used": {
    "attempt_ids": ["close/set-2/attempt-1", "close/set-2/attempt-2"],
    "shared_with": []
  }
}
```

| Field | Meaning |
| --- | --- |
| `attempts_used.attempt_ids` | Every Attempt that was made for this Evaluator, whatever became of it. An Attempt whose invocation failed is listed. |
| `attempts_used.shared_with` | The other metrics whose Evaluators used any of the same Attempts, in scorecard order. Empty when the Attempts were made for this Evaluator alone. |
| `evidence.attempt_ids` | The Attempts the value rests on. Never more than `attempts_used.attempt_ids`, and fewer when an Attempt was left out, for the reasons given in `counts`. |

Each Attempt appears once in the scorecard's `attempts`, however many Evaluators used it. Anything that adds up over Attempts, such as cost, should add up over `attempts` for the sweep, and treat a per-metric total as overlapping with the metrics in `shared_with`.

A metric that is not configured has no `attempts_used`.

## Alone or grouped, the result is the same

An Evaluator is given the Attempts of its own set and nothing else. Which other Evaluators were selected does not change its value, its counts or its details. Selecting it alone on the same evidence gives the same metric entry, apart from `shared_with` and, for an Evaluator in a later set, the Attempt identities.

## When one Evaluator's Attempts fail

An Evaluator that cannot calculate a value from its Attempts reports an unavailable measurement with its reason and counts. That concerns its own metric only. The sweep goes on to the next set, and the metrics of the other Evaluators keep the values their own Attempts support.

A customer callable that breaks its contract still stops the whole sweep: a task-success rule that returns no sourced task outcome, a violation assessor that returns no violation assessments, or an action recorder that returns something other than action events. That is a mistake in the declaration and not a failed Attempt.

## What is not there yet

- Experimental conditions, and so the matching of them.
- An Evaluator with its own Tasks or its own number of Attempts. Every Evaluator is scored from the sweep's Tasks.

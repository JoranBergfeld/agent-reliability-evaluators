# Several Evaluators in one sweep

One Evaluator sweep can score several metrics. It returns one reliability scorecard with each selected metric reported on its own and every other metric as not configured. There is no combined score.

Attempts cost money, so the sweep does not make an Attempt twice when one Attempt can serve two Evaluators. It also never scores an Evaluator from Attempts that were made for something else, or under an experimental condition the Evaluator is not calculated from. This page says when Attempts are shared, when they are made separately, and how the scorecard shows it.

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

## What an Evaluator needs of its Attempts

Each Evaluator declares what it needs. The sweep reads two things from that.

**What its Attempts have to be made with.** This decides whether two Evaluators can share Attempts.

| Evaluator | What its Attempts are made with |
| --- | --- |
| Outcome consistency | Nothing in particular. Every completed Attempt has a task outcome. |
| Resource consistency | Nothing in particular. Every Attempt is timed, and the agent target reports the rest. |
| Constraint compliance, conditional harm severity | Nothing in particular. The violation assessor belongs to the Task, so every Attempt at the Task is assessed the same way. |
| Distributional and sequential trajectory consistency | Action evidence from one declared evidence source, read by one declared recorder. |
| Calibration, strict confidence discrimination, Brier-based score | A confidence follow-up after each completed Attempt. |

**Which experimental conditions it is calculated from.** Every Evaluator that exists today is calculated from Attempts under the baseline. An Attempt at a Task given without an experiment had no variation applied to it and is used as a baseline Attempt.

## When Attempts are shared

Two Evaluators share Attempts when everything they both need the Attempts to be made with is the same. What only one of them needs is collected for that one and does not change what the other is scored from. A confidence follow-up, for example, comes after the task outcome, the violation assessments and the recorded actions of an Attempt have been taken, so calibration, strict confidence discrimination and the Brier-based score share Attempts with each other and with any other Evaluator. The agent is asked once per Attempt, however many of the three are selected.

With one `action_evidence` declaration for the whole sweep, as above, nothing differs. All selected Evaluators are scored from the same Attempts, and each planned Attempt is made once.

The action types are not part of what is matched. They are applied when a metric is calculated and do not change what is recorded for an Attempt. Two Evaluators that read the same recording with different action types share Attempts.

Within the Attempts it shares, an Evaluator is given only those made under a condition it is calculated from. Attempts under the variation of an experiment are kept in the scorecard and are given to no Evaluator that is calculated from the baseline.

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
2. Otherwise it starts a set of its own. Every Task and every experiment is attempted again for it, as often as declared.

The sets are made one after another: every Task and experiment for the first set, then every Task and experiment for the second.

| | First set | Later sets |
| --- | --- | --- |
| Attempt at a Task | `<task>/attempt-<n>` | `<task>/set-2/attempt-<n>` |
| Experiment | `<experiment>` | `<experiment>/set-2` |
| Attempt of an experiment | `<experiment>/<condition>/attempt-<n>` | `<experiment>/set-2/<condition>/attempt-<n>` |

The first set is identified exactly as in a sweep with one set. An Attempt's `task_id` is the Task either way.

An experiment that is run again is a run of its own. It has its own entry under `experiments` in the scorecard, and your controls are invoked around each of its Attempts with the experiment identity `<experiment>/set-2`. A test resource that an experiment did not restore stops every later experiment that may change it, in the same set and in later ones: an experiment is not run again on state its first run left behind.

Two recordings are the same when they have the same `evidence_source` and the same `record` callable. A different callable is a different requirement, also when it happens to read the same data.

The agent is asked for its confidence only after the Attempts of a set in which an Evaluator uses confidence. Attempts made separately for another Evaluator have no confidence evidence.

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
| `attempts_used.attempt_ids` | Every Attempt this Evaluator was given, whatever became of it. An Attempt whose invocation failed is listed. An Attempt under a condition the Evaluator is not calculated from is not. |
| `attempts_used.shared_with` | The other metrics whose Evaluators used any of the same Attempts, in scorecard order. Empty when no other Evaluator used them. |
| `evidence.attempt_ids` | The Attempts the value rests on. Never more than `attempts_used.attempt_ids`, and fewer when an Attempt was left out, for the reasons given in `counts`. |

Each Attempt appears once in the scorecard's `attempts`, however many Evaluators used it. Anything that adds up over Attempts, such as cost, should add up over `attempts` for the sweep, and treat a per-metric total as overlapping with the metrics in `shared_with`. An Attempt that no metric lists, such as one under a variation today, still counts for the sweep.

A metric that is not configured has no `attempts_used`. The human-readable report shows the field with every other field of a metric, and links each Attempt identity to that Attempt's evidence.

## Alone or grouped, the result is the same

An Evaluator is given the Attempts of its own set and nothing else. Which other Evaluators were selected does not change its value, its counts or its details. Selecting it alone on the same evidence gives the same metric entry, apart from `shared_with` and, for an Evaluator in a later set, the Attempt identities.

## When one Evaluator's Attempts fail

An Evaluator that cannot calculate a value from its Attempts reports an unavailable measurement with its reason and counts. That concerns its own metric only. The sweep goes on to the next set, and the metrics of the other Evaluators keep the values their own Attempts support.

A customer callable that breaks its contract still stops the whole sweep: a task-success rule that returns neither a sourced task outcome nor `NO_TASK_OUTCOME_YET`, a violation assessor that returns no violation assessments, or an action recorder that returns something other than action events. That is a mistake in the declaration and not a failed Attempt.

## What is not there yet

- An Evaluator that is calculated from Attempts under a variation. The three robustness metrics need one. Until then no metric lists the Attempts under a variation.
- An Evaluator with its own Tasks, experiments or number of Attempts. Every Evaluator is scored from the sweep's Tasks and experiments.

# Late and corrected task outcomes

A task outcome is not always known, or right, when the sweep ends. A label can arrive a day later, and a check can turn out to have been wrong. You can supply or correct the task outcome of an Attempt after the run and recalculate. You get a new reliability scorecard that uses the corrected outcome and marks the earlier result as superseded.

| | |
| --- | --- |
| Supplied with | `OutcomeCorrection`, passed to `recalculate(outcome_corrections=[...])` |
| Left open with | `NO_TASK_OUTCOME_YET`, returned by a task-success rule |
| Reported in | `attempts[].superseded_task_outcomes` and `superseded_results` of the new scorecard |
| Source | [`src/reliability_evaluators/sweep/recalculation.py`](../../src/reliability_evaluators/sweep/recalculation.py), [`src/reliability_evaluators/evidence/corrections.py`](../../src/reliability_evaluators/evidence/corrections.py) |

## You supply the earlier scorecard

**Assumption.** The offering keeps no evidence store ([ADR 0003](../adr/0003-no-evidence-store.md)), so a recalculation works from the earlier scorecard that you supply from your platform. This follows open point 25 of the [specification](https://github.com/JoranBergfeld/agent-reliability-evaluators/issues/25) and is not a confirmed decision.

What you supply is the earlier `scorecard.json`, read as JSON. It holds the attempt-level evidence and the earlier results, so nothing else is needed. `evidence.json` alone is not enough: it has no results to supersede.

If your platform no longer has the scorecard, the recalculation cannot be done. The offering does not notice that a platform deleted or expired it.

## Recalculating

```python
import json
from pathlib import Path

from reliability_evaluators.evidence import OutcomeCorrection, TaskOutcome
from reliability_evaluators.scorecard import ArtifactLocation
from reliability_evaluators.sweep import RecalculationFailed, recalculate

earlier = json.loads(Path("artifacts/reliability/scorecard.json").read_text(encoding="utf-8"))

try:
    scorecard = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[
            OutcomeCorrection(
                attempt_id="close/attempt-3",
                task_outcome=TaskOutcome(success=True, source="manual review of the service audit log"),
                provenance="J. Jansen on 2026-11-27: the check ran before the service had saved the item",
            ),
        ],
    )
except RecalculationFailed as failure:
    print(failure)  # what stopped it. There is no new scorecard, and the earlier one stands.
    raise

ArtifactLocation("artifacts/reliability-recalculated").deliver(scorecard)
```

An `OutcomeCorrection` names one Attempt by its identity and gives three things. All three are required.

| Field | Content |
| --- | --- |
| `attempt_id` | The identity of the Attempt, as in `attempts[].attempt_id` of the earlier scorecard. |
| `task_outcome` | The outcome, binary, with its `source`: what it was determined from. |
| `provenance` | Who or what supplied or corrected the outcome, when and why. |

The same call supplies an outcome that was missing and corrects one that was recorded. A corrected scorecard can be corrected again: supply the new scorecard as the earlier one.

### Declarations are given again

The earlier scorecard holds data and cannot hold your code. Declare again what the sweep was given, where a metric needs it:

| Argument | Needed when |
| --- | --- |
| `action_evidence` | A trajectory metric was scored. Only its evidence source and action types are used: nothing is recorded again. |
| `metrics` | An Evaluator was configured with declarations of its own. Give the same `ConfiguredEvaluator`. |
| `acceptance_criteria` | The earlier scorecard assessed an acceptance criterion. Each is assessed again, on the new result. |

The metrics that are scored are those the earlier scorecard scored. A recalculation adds none and drops none.

If you call an Evaluator on its own, without a sweep, `apply_outcome_corrections(attempts, corrections)` in `reliability_evaluators.evidence` gives you the corrected Attempts to call it with. Each keeps the record that was replaced.

## Leaving an outcome open during the sweep

A task-success rule that cannot decide during the sweep returns `NO_TASK_OUTCOME_YET`:

```python
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import NO_TASK_OUTCOME_YET


def labelled_by_support(attempt, response):
    label = my_labels.get(attempt.attempt_id)
    if label is None:
        return NO_TASK_OUTCOME_YET
    return TaskOutcome(success=label, source="label by the support team")
```

The Attempt is recorded as completed with `"task_outcome": null`. It is not a task failure. A metric that is calculated from task outcomes leaves it out and counts it as missing, with the reason `no task outcome recorded`. Its confidence rating is still collected, so the pair is complete once the outcome is supplied.

Returning `None`, or anything else that is not a `TaskOutcome`, still stops the sweep: that is a mistake in the rule and not an open outcome.

## What the new scorecard holds

The new scorecard has the shape of any scorecard, and one more part: `superseded_results`. A scorecard from a sweep does not have that part.

**The corrected Attempt** carries the new outcome and keeps the record it replaced, with the provenance of the correction. The list is ordered oldest first. A record of `null` means that the Attempt had no task outcome until one was supplied.

```json
{
  "attempt_id": "close/attempt-3",
  "task_outcome": {"success": true, "source": "manual review of the service audit log"},
  "superseded_task_outcomes": [
    {
      "task_outcome": {"success": false, "source": "final state of the to-do service"},
      "correction_provenance": "J. Jansen on 2026-11-27: the check ran before the service had saved the item"
    }
  ]
}
```

**The earlier result** is kept in `superseded_results`, marked as superseded, with the corrections that replaced it. `metrics` is the earlier scorecard's `metrics` as it was, including each acceptance assessment. After a second recalculation the list has two entries, oldest first.

```json
"superseded_results": [
  {
    "status": "superseded",
    "outcome_corrections": [
      {
        "attempt_id": "close/attempt-3",
        "task_id": "close",
        "task_outcome": {"success": true, "source": "manual review of the service audit log"},
        "provenance": "J. Jansen on 2026-11-27: the check ran before the service had saved the item",
        "superseded_task_outcome": {"success": false, "source": "final state of the to-do service"}
      }
    ],
    "metrics": ["... the twelve metric entries of the earlier scorecard ..."]
  }
]
```

The current results are in `metrics`, as always. A pipeline that reads `metrics` reads the recalculated values.

The [human-readable report](../report/readme.md) of a recalculated scorecard says at the top that it is a recalculation. It shows the superseded results in a section of their own, after the attempt-level evidence, with every superseded metric marked as not being the current result.

## Worked example

One Task, `close`, with four Attempts. The first two succeeded and the last two failed. The agent's confidence ratings were 75, 75, 75 and 25.

| | Outcome consistency | Brier-based score |
| --- | --- | --- |
| Earlier | p = 2/4, so (2 × 0.5 − 1)² = **0** | Squared errors 0.0625, 0.0625, (0.75 − 0)² = 0.5625 and 0.0625. Mean 0.1875, score **0.8125** |
| Third Attempt corrected to a success | p = 3/4, so (2 × 0.75 − 1)² = **0.25** | The third becomes (0.75 − 1)² = 0.0625. Mean 0.0625, score **0.9375** |

The new scorecard reports 0.25 and 0.9375 under `metrics`, and keeps 0 and 0.8125 under `superseded_results`.

## What a correction changes, and what it does not

A correction changes the task outcome of the Attempt it names. Nothing else in the evidence changes: not its confidence rating, action evidence, resource measurements or violation assessments, not what it records about its experiment, and no other Attempt.

Every metric the earlier scorecard scored is scored again, by the same Evaluator, from the same Attempts that Evaluator was given before. So:

- A metric that is calculated from task outcomes uses the corrected outcome. Today these are outcome consistency, fault robustness, environment robustness, prompt robustness, calibration, strict confidence discrimination and the Brier-based score.
- A metric that is not calculated from task outcomes keeps its result. Today these are the two trajectory metrics, resource consistency, constraint compliance and conditional harm severity.
- An Attempt that an Evaluator was not given stays out of that metric. Correcting an Attempt made under the variation of an experiment does not change a metric that is calculated from the baseline. It does change the robustness metric that compares that kind of variation: [fault robustness](../fault_robustness/readme.md) when the variation is a fault, [environment robustness](../environment_robustness/readme.md) when it is another presentation, and [prompt robustness](../prompt_robustness/readme.md) when it is another wording. Such a metric is given the Attempts under both conditions, so a correction under either one changes it. A corrected Attempt under a prompt variant keeps the variant it ran under.
- The declared experiments are carried over as they were, and so is everything else the scorecard records about how the sweep was executed. See the next section.

**The corrected outcome does not depend on the confidence rating.** The outcome is the one you supply. The recalculation does not read the rating to decide it and does not change the rating. An Attempt the agent rated 100 can be corrected to a failure, and the pair then counts against the agent.

The new results are scored in your own process and are attributed to that scoring path.

## A partial scorecard, an execution failure and retries

A recalculation makes no Attempt. So nothing about how the sweep was [executed](execution-limits.md) changes, and a correction cannot make up for what the sweep did not collect.

**The record of the execution is carried over unchanged.** These parts of the new scorecard are the earlier scorecard's: `status`, `concurrency`, `experiments` and `tasks_without_experiment` with their `attempt_set`, `limits` and `execution`, and the entries of `missing` for Attempts that were not made or did not complete. A partial scorecard stays partial, and still names what it is missing and why. Recalculating does not hide that the earlier run was cut short.

**So is the token usage.** `token_usage` is what the sweep used. A recalculation calls no agent and scores by code, so it uses no tokens and the usage of the new scorecard is that of the earlier one, with what you declared for generated variations as you reported it. Like the rest of the execution record, it has to be what the supplied evidence gives. See [token usage](../token_usage/readme.md#recalculation-and-rescore).

**A metric that had no result still has none.** A metric with an `execution_failure` in the earlier scorecard is not scored again. Its entry in the new scorecard is the earlier entry as it was, with the same `execution_failure`, and it stays listed in `missing`. This holds even when the outcomes you supply would now permit a calculation: for example when a sweep was cancelled early, its task outcomes were left open with `NO_TASK_OUTCOME_YET`, and they are supplied afterwards. The outcomes are recorded on their Attempts all the same, with the record that they replaced. To get a value for such a metric, run the sweep again.

The reason is that the new scorecard is to differ from the superseded one by the corrected outcomes and by nothing else. A metric whose Attempts were cut short had no result to correct, and an outcome supplied later says nothing about the Attempts that were never made or never completed.

**A metric that had a result is scored as the sweep would have scored it.** It is scored again from the same Attempts, and where the sweep cut those short is carried over in its `cut_short`. If the corrected outcomes leave its Evaluator unable to calculate a value from Attempts that were cut short, the metric has an execution failure in the new scorecard, as it would have had in the sweep.

**A retried Attempt and its retry stay two Attempts.** Each is read and written back with its own identity, its own execution status and its `retry_of`. A correction names one of them by identity. Correcting the retry does not touch the Attempt it retries, which still has no task outcome.

**No task outcome can be supplied for an Attempt that did not complete.** That includes an Attempt that timed out, one that answered after its time limit, one that was cancelled and one that was retried. In a sweep a late answer is not scored, so that the time limit holds for every Attempt alike, and a label supplied afterwards does not change that. Such an Attempt is not a task failure, and a recalculation never makes it count as one: a correction that names it fails the recalculation, and nothing is superseded.

## When a recalculation fails

A recalculation either gives a new scorecard or raises `RecalculationFailed`. There is no partial update. On a failure there is no new scorecard, nothing is superseded and nothing is written, so a failed recalculation cannot be read as a successful update.

| Situation | The failure says |
| --- | --- |
| A correction names an Attempt that is not in the supplied evidence | `there is no Attempt '...' in the evidence` |
| A correction matches more than one Attempt in the supplied evidence | `more than one Attempt has the identity '...'` |
| Two corrections name the same Attempt | `more than one task outcome is supplied for '...'` |
| A correction names an Attempt whose execution did not complete, which includes one that timed out, answered late, was cancelled or was retried | `the execution of '...' did not complete`. An execution failure is not a task failure, and no label makes it one. |
| No correction is given | `no task outcome was supplied or corrected` |
| The supplied scorecard is not a scorecard this version reads | `the supplied evidence does not validate`, followed by what is wrong: the schema version, a missing part, or a part this version does not know |
| An Attempt in the supplied evidence is not valid evidence, or is not exactly what this version records for it | `the supplied evidence does not validate`, with the Attempt and the field |
| The supplied evidence, scored as it is, does not give the earlier results | `it does not reproduce the earlier result of '...'`, with the parts that differ |
| An execution record under `experiments` or `tasks_without_experiment` is not exactly what this version records, or its limits are not execution limits | `the supplied evidence does not validate`, with the experiment or the Task |
| A retry names an Attempt that is not in the supplied evidence | `Attempt '...' retries '...', which is not in the attempt-level evidence` |
| The `status`, `missing` or `concurrency` of the earlier scorecard is not what its evidence and its execution records give | `it does not reproduce the earlier 'status'`, or the part that differs |
| A metric says its Attempts were cut short in an experiment or Task that the scorecard has no execution record of | `the result of '...' says its Attempts were cut short in '...'` |
| A metric needs a declaration that was not given | `'...' needs action evidence to be declared` |
| A metric of the earlier scorecard was attributed to [Foundry-managed scoring](../managed_scoring/readme.md), with a result or with a failure | `'...' was scored on the scoring path 'foundry_managed', and a recalculation scores in this process`. A result is not scored again on another scoring path than the one you chose for it. A [customer-process rescore](../managed_scoring/readme.md#a-customer-process-rescore) is a different request: it applies no correction, and it reports its result beside the managed one. |
| The earlier scorecard assessed an acceptance criterion that is not declared again | `the earlier scorecard assessed an acceptance criterion for '...'` |

A match is never resolved by order or by choosing one of several Attempts.

The supplied evidence is read strictly. Nothing in it is repaired, defaulted or dropped. An Attempt that carries a field this version does not know fails the recalculation, because the new scorecard could otherwise lose it without saying so.

Before anything is corrected, the supplied evidence is scored as it is and compared with the earlier results. They have to match, numbers to nine significant digits. This is what makes the new result differ from the superseded one only by the corrected outcomes. It fails when the evidence was changed after the earlier scorecard was made, when a declaration differs from the one the sweep was given, and when the earlier scorecard was calculated by another version of a metric definition. In the last case, recalculate with the commit or tag that made the earlier scorecard.

## The earlier run is not modified

`recalculate` reads the data you pass and returns new data. It makes no Attempt, invokes no agent, reads and writes no file, and calls no platform. An earlier Foundry run, an earlier CI run and the earlier scorecard file stay as they were, and the scorecard you passed in is not changed.

Because of that, the earlier `scorecard.json` does not say that it was superseded. The new scorecard is the record of that. Keep it where the people who read the earlier one will find it.

Deliver the new scorecard to a directory of its own, as in the example above. Opening an `ArtifactLocation` removes the files of an earlier run from that directory, so do not open the directory that holds the earlier scorecard.

# Execution limits, retries, concurrency and partial scorecards

An Evaluator sweep makes the Attempts you plan, under the limits you set. When some of that does not happen, because an Attempt timed out, the sweep was cancelled or a limit was reached, the sweep still returns a reliability scorecard. The scorecard says it is partial, names what is missing and why, and keeps every metric that can still be calculated.

Nothing here lowers a metric. Only a completed Attempt has a task outcome. An Attempt that timed out, was cancelled or was retried is not a failed Task.

Everything on this page holds for a Task in a [controlled experiment](controlled-experiments.md) and for a Task given without one. Where the two differ, it says so.

## Attempt counts and limits belong to the Task

Each Task declares its own number of Attempts and its own execution limits:

```python
from reliability_evaluators.sweep import ExecutionLimits, Task, run_sweep

close = Task(
    task_id="close",
    prompt="Close the item.",
    success_rule=item_is_closed,
    attempts=5,
    limits=ExecutionLimits(attempt_timeout_seconds=60, total_timeout_seconds=600, max_retries=1),
)
archive = Task(task_id="archive", prompt="Archive the item.", success_rule=item_is_archived, attempts=3)
```

Pass a Task to `run_sweep(tasks=[...])`, or put it in an `Experiment`. In an experiment, `attempts` is the number of Attempts under each condition, and the Task's limits are the limits of the experiment.

| Limit | Meaning | Default |
| --- | --- | --- |
| `attempt_timeout_seconds` | How long one invocation of the agent may take. Customer controls and the confidence follow-up are not counted. | No limit |
| `total_timeout_seconds` | How long all the Task's Attempts may take together, under every condition of its experiment, counted from the start of the first one and with everything done around them. Once it is reached, no further Attempt or retry of that Task is started. | No limit |
| `max_retries` | How often a planned Attempt is made again after the invocation of the agent failed or timed out. | 0 |

No minimum number of Attempts and no limit is imposed. A Task with one Attempt runs. Whether a metric can be calculated from it is for that metric's Evaluator to say.

## Execution statuses of an Attempt

| `execution_status` | Meaning | Task outcome |
| --- | --- | --- |
| `completed` | The agent answered and the task-success rule decided the task outcome. | Yes |
| `failed` | The invocation raised an error. | None |
| `timed_out` | The invocation reached its time limit. | None |
| `cancelled` | The invocation ended with an error after the sweep was cancelled. | None |
| `retried` | The invocation failed or timed out, and the Attempt was made again. | None |
| `setup_failed`, `condition_not_met`, `cleanup_failed` | A customer control of an experiment failed. See [controlled experiments](controlled-experiments.md#when-a-control-fails). | See there |

`execution_error` says why an Attempt did not complete. Evaluators leave out every Attempt that is not `completed` and count it as excluded.

## Time limits

The sweep cannot interrupt an invocation that is running. It applies the limit of an Attempt in two ways:

- It passes the limit to the agent target as `AttemptRequest.timeout_seconds`. The agent target is expected to stop there and raise `TimeoutError`. An invocation that raises `TimeoutError` is `timed_out`, with or without a declared limit.
- It measures each invocation. One that ends after the limit is `timed_out` as well, whether it ended with an answer or with an error. A late answer is not scored, so that the limit holds for every Attempt alike: the task-success rule, the violation assessor and the action recorder are not called for it, and the agent is not asked for a confidence rating.

An agent target that ignores the limit and never returns blocks the sweep. Give your agent target a timeout of its own.

`total_timeout_seconds` is checked before each Attempt and each retry starts. An Attempt that is already running is bounded by `attempt_timeout_seconds` only.

In an experiment, an Attempt that timed out is still verified and cleaned up by your controls, like one whose invocation failed.

## Retries

A planned Attempt whose invocation `failed` or `timed_out` is made again, up to `max_retries` times.

- The retried Attempt stays in the evidence. Its status becomes `retried` and its `execution_error` keeps why it did not complete.
- The retry is another Attempt with its own identity, such as `close/attempt-2/retry-1`. Its `retry_of` names the Attempt it retries.
- A completed Attempt is never retried, whatever its task outcome. Retrying task failures would hide the instability the metrics measure.
- An Attempt that a customer control failed for (`setup_failed`, `condition_not_met`, `cleanup_failed`) is not retried. A retry is for an invocation of the agent. A failing control says that the test setup is broken, and making the Attempt again would not show otherwise.
- A cancelled Attempt is not retried. No retry starts after the sweep was cancelled or the total time limit was reached.
- When the retries run out, the last execution keeps its own status, `failed` or `timed_out`.

### What a retry does about state

A Task given without an experiment has no controls. Nothing prepares or restores its state, for an Attempt or for a retry.

In an experiment, a retry is an Attempt like any other, and gets the controls that the starting-state policy gives every Attempt:

| Starting-state policy | The retried Attempt | Its retry |
| --- | --- | --- |
| `fresh` | `clean_up` is invoked after it, as after every Attempt. | `prepare_state` is invoked before it, as before every Attempt. The retry starts from freshly prepared state and sees nothing of the Attempt it retries. |
| `continuing` | Nothing is restored. | Nothing is prepared again. The retry continues from the state the retried Attempt left, whatever it did before its invocation ended. So would the next planned Attempt have, if there were no retry. |

Under `continuing`, state is still prepared once before the first Attempt under each condition and cleaned up once after the last. When that last Attempt is retried, `clean_up` is invoked after its last retry.

`apply_condition` and `verify_condition` are invoked for a retry as for every Attempt. The retry records its own experiment, starting-state policy, intended and observed condition and `lifecycle`, so the scorecard shows which controls were invoked for it.

A retry under `continuing` can start from a state that the retried Attempt changed halfway. That is what the policy declares: every Attempt starts from the state the previous one left. If your experiment cannot tolerate it, leave `max_retries` at 0 for that Task.

## Cancelling a sweep

Pass `cancellation`, a function without arguments that returns `True` once the sweep should stop:

```python
import threading

stop = threading.Event()  # set it from a signal handler, a watchdog or another thread

scorecard = run_sweep(
    tasks=tasks, agent_target=my_agent_target, metrics=["outcome_consistency"], cancellation=stop.is_set
)
```

The sweep asks before every Attempt and every retry. Once the answer is `True`, no further one is started, and the sweep scores what it collected and returns the scorecard.

- An Attempt that is in flight is not interrupted. If it completes, it is kept as `completed`. If its invocation ends with an error, it is `cancelled` and not `failed`.
- No further request is sent to the agent. An Attempt that completed after the sweep was cancelled is not followed by a confidence request, and records why it has no rating.
- In an experiment, the controls of the Attempt in flight still run: its condition is verified and its state is cleaned up as the starting-state policy says. Under `continuing`, `clean_up` is invoked after the last Attempt that was made. An experiment that had not started is not started, and none of its controls is invoked.
- `KeyboardInterrupt` and other exceptions that are not an `Exception` are not caught. They end the sweep without a scorecard. To stop a sweep and keep its evidence, use `cancellation`.

## Concurrency

Attempts run one after another, Task by Task and then experiment by experiment, unless you declare otherwise:

```python
scorecard = run_sweep(
    tasks=tasks, agent_target=my_agent_target, metrics=["outcome_consistency"], max_concurrent_attempts=4
)
```

Then up to four Attempts of the Tasks given without an experiment are in flight at once, across those Tasks. The agent target and everything you supply for them (task-success rules, violation assessors, the action recorder and `cancellation`) are called from several threads and must be safe for that. Attempts that overlap can disturb shared state and timing measurements. Controlling that is yours.

The Attempts of an experiment always run one after another, and after the Tasks given without one. Each of them depends on the state your controls prepare, and a later experiment depends on whether an earlier one restored a shared test resource.

The scorecard records what was declared:

```json
"concurrency": {"mode": "concurrent", "max_concurrent_attempts": 4}
```

Without the declaration it records `"mode": "sequential"` and `"max_concurrent_attempts": 1`. The Attempts are listed in the order they were planned in both cases.

## What the scorecard says

### `status` and `missing`

`status` is `complete` when every planned Attempt completed, directly or through a retry, and every selected metric has a result. Otherwise it is `partial`, and `missing` names what is missing and why:

```json
"status": "partial",
"missing": [
  {
    "kind": "attempt_evidence",
    "experiment_id": null,
    "task_id": "close",
    "attempt_set": 1,
    "status": "timed_out",
    "reason": "the time limit of 600 seconds for all its Attempts was reached",
    "attempts_not_made": 2,
    "attempts_not_completed": []
  }
]
```

| `kind` | What is missing | Fields |
| --- | --- | --- |
| `attempt_evidence` | Planned Attempts that were not made or did not complete. | `experiment_id` (`null` for a Task given without an experiment), `task_id` and `attempt_set`; `status` and `reason` of the execution, as below; `attempts_not_made`; `attempts_not_completed`, with each Attempt's execution status and error |
| `metric_result` | The result of a selected metric. | `metric_id`, `reason`, `experiment_ids`, `task_ids`. Both lists are empty when it is [Foundry-managed scoring that gave no result](../managed_scoring/readme.md#when-managed-scoring-gives-no-result), and not the Attempts that were cut short |

A metric that is an unavailable measurement is a result. It does not make a scorecard partial.

A third status, `reporting_failed`, never appears in a scorecard. It is the status of `reporting-failure.json`, which is written when no scorecard could be delivered: see [the report](../report/readme.md#reporting-failure).

### One record of what was planned and what was made

An experiment is what you declared as an `Experiment`. A Task given without one is not an experiment, and the scorecard does not list it as one. Both get the same record of their execution limits and of what became of their planned Attempts:

- `experiments` has one entry per declared experiment: [what it declared](controlled-experiments.md#what-the-scorecard-records), then `attempt_set`, `limits` and `execution`.
- `tasks_without_experiment` has one entry per Task given without an experiment: `task_id`, then `attempt_set`, `limits` and `execution`.

`attempt_set` is 1 unless [Evaluators need separate Attempts](several-evaluators.md#when-separate-attempts-are-made). Then every Task and experiment is attempted again in a later set, with an entry of its own: a Task under the same `task_id` and the next `attempt_set`, an experiment under the identity `<experiment>/set-2`. Each set is made under the Task's limits anew.

```json
"limits": {"attempt_timeout_seconds": 60, "total_timeout_seconds": 600, "max_retries": 1},
"execution": {
  "status": "timed_out",
  "reason": "the time limit of 600 seconds for all its Attempts was reached",
  "attempts_planned": 5,
  "attempts_recorded": 4,
  "attempts_completed": 3,
  "attempts_not_completed": [],
  "attempts_not_made": 2,
  "retries": 1
}
```

| Field of `execution` | Content |
| --- | --- |
| `status`, `reason` | How the making of the planned Attempts ended, and why it ended early if it did. |
| `attempts_planned` | The Attempts declared: `attempts` of the Task, under each condition of its experiment. |
| `attempts_recorded` | The Attempts in the scorecard for it, retries included. |
| `attempts_completed` | Planned Attempts that completed, directly or through a retry. |
| `attempts_not_completed` | Planned Attempts that were made and did not complete, by the identity of their last execution. |
| `attempts_not_made` | Planned Attempts that were never started. They do not appear as Attempts. |
| `retries` | Retries made. Each is an Attempt of its own. |

| `status` | Meaning |
| --- | --- |
| `completed` | Every planned Attempt was made. Whether each of them completed is in the counts. |
| `stopped` | A customer control failed and the later Attempts of the experiment were not made. See [controlled experiments](controlled-experiments.md#what-stops-and-what-continues). |
| `timed_out` | The total time limit was reached before every planned Attempt was made, or before a retry was. |
| `cancelled` | The sweep was cancelled before every planned Attempt was made, or cancelled one in flight. |

### Metrics in a partial scorecard

A metric keeps its result whenever its Evaluator can still calculate a value from the evidence that was collected. The Attempts and Tasks it had to leave out are counted in its `counts`, with reasons.

A metric has no result when its Evaluator cannot calculate a value and the Attempts that supply its evidence were cut short. They were cut short when, for an experiment or a Task, not all the Attempts planned under the conditions the metric is calculated from were made, or not one of them completed. That is an execution failure. It is not an unavailable measurement, it is not "not applicable" and it is not a task failure:

```json
{
  "metric_id": "outcome_consistency",
  "state": null,
  "value": null,
  "reason": null,
  "execution_failure": {
    "reason": "the Attempts that supply this metric's evidence were cut short, and what was collected does not permit a calculation: no Task has at least two Attempts with a task outcome",
    "experiment_ids": [],
    "task_ids": ["close"]
  }
}
```

`experiment_ids` and `task_ids` name the experiments, and the Tasks given without one, whose Attempts were cut short.

Every selected metric also says where its Attempts were cut short, whether or not it has a result:

```json
"cut_short": {"experiment_ids": [], "task_ids": ["close"]}
```

Both lists are empty when none were. A metric that is `available` with a `cut_short` that is not empty has a value that rests on less than was planned.

| What you see on a selected metric | What it means | What to do |
| --- | --- | --- |
| `state` is `available` | A value was calculated. In a partial scorecard, read its `counts` to see what it rests on. | |
| `state` is `unavailable` | The Evaluator found too little to calculate from, in evidence that was not cut short. In a partial scorecard its `counts` name the Attempts it left out. | Collect more evidence. |
| `state` is `null` and `execution_failure` is set | The Attempts were cut short. Nothing was measured. | Investigate the failure. |
| `state` is `null`, `execution_failure` is set and `managed_scoring.error` is set | [Foundry-managed scoring gave no result](../managed_scoring/readme.md#when-managed-scoring-gives-no-result). Nothing was measured, and the metric was not scored in your own process instead. | Investigate the failure. You can ask for a [customer-process rescore](../managed_scoring/readme.md#a-customer-process-rescore). |

`execution_failure` is `null` on every metric that has a result. The same failure is listed in `missing` with the kind `metric_result`.

A [recalculation](late-and-corrected-outcomes.md#a-partial-scorecard-an-execution-failure-and-retries) with task outcomes supplied or corrected afterwards keeps all of this as it was: the scorecard stays partial, and a metric without a result still has none.

An acceptance criterion on a metric without a result is not assessed, and the execution failure is its reason. A metric that kept its result in a partial scorecard is assessed like any other. To require a minimum amount of evidence, put `minimum_counts` in the criterion: see [acceptance criteria](../acceptance_criteria/readme.md).

Which Attempts supply a metric's evidence depends on the metric. Most are calculated from the Attempts of Tasks given without an experiment and from Attempts under the baseline of an experiment: for them, Attempts under a variation that were not made make the scorecard partial and do not, on their own, leave the metric without a result. A metric that compares a kind of variation with the baseline, such as fault robustness, is calculated from both conditions of the experiments that declare that kind, taken together. When Evaluators need separate Attempts, each is judged by the Attempts of its own set.

## Choices made here

The specification leaves these open. They are stated so that you can disagree with them knowingly.

- **Limits belong to the Task.** The Task already carries its number of Attempts, in an experiment and without one. An experiment has no second set of limits.
- **A Task given without an experiment is not an experiment.** It has no condition and no starting-state policy to record. It is listed under `tasks_without_experiment` with the same `limits` and `execution` as an experiment has.
- **The status of an execution says how the making of the Attempts ended, not whether each Attempt completed.** It is `completed` when every planned Attempt was made, as controlled experiments already had it. Attempts that did not complete are counted next to it and make the scorecard partial.
- **The sweep does not interrupt invocations.** It hands the limit to the agent target and measures. Abandoning a running invocation would let it overlap the next Attempt in a sweep that is declared sequential.
- **A late answer is not scored.** An Attempt that ends after its limit is `timed_out` even when the agent answered.
- **`retried` replaces the status, not the Attempt.** A retried Attempt reads `retried`. Whether it had failed or timed out is in its `execution_error`.
- **A retry follows the starting-state policy and adds nothing to it.** Fresh state is prepared again. Continuing state continues.
- **Only an invocation of the agent is retried.** A failed customer control is not.
- **Concurrency is for Tasks given without an experiment.** The Attempts of an experiment are never in flight together.
- **A scorecard is partial as soon as one planned Attempt has no completed execution.** This holds even when every metric could still be calculated. It is also partial when Foundry-managed scoring gave no result for a selected metric, even when every Attempt completed.
- **A metric without a result has no metric state.** Execution status is separate from metric state, so an execution failure is shown as `state: null` with `execution_failure`, not as a fifth state.
- **A metric loses its result only when its Attempts were cut short.** Where every planned Attempt was made and some completed, the Evaluator's own result stands, an unavailable measurement included: only the Evaluator knows whether the Attempts it left out could have changed it. Conditional harm severity with no observed violation stays unavailable when one Attempt of several failed to execute.
- **No confidence request after a cancellation.** Cancelling stops every further request to the agent, the follow-up of an Attempt that completed in flight included.

# Execution limits, retries, concurrency and partial scorecards

An Evaluator sweep makes the Attempts you plan, under the limits you set. When some of that does not happen, because an Attempt timed out, the sweep was cancelled or an experiment reached its limit, the sweep still returns a reliability scorecard. The scorecard says it is partial, names what is missing and why, and keeps every metric that can still be calculated.

Nothing here lowers a metric. Only a completed Attempt has a task outcome. An Attempt that timed out, was cancelled or was retried is not a failed Task.

## Experiments

The Attempts planned at one Task are one experiment. Each Task declares its own number of Attempts and its own execution limits:

```python
from reliability_evaluators.sweep import ExecutionLimits, Task, run_sweep

tasks = [
    Task(
        task_id="close",
        prompt="Close the item.",
        success_rule=item_is_closed,
        attempts=5,
        limits=ExecutionLimits(attempt_timeout_seconds=60, experiment_timeout_seconds=600, max_retries=1),
    ),
    Task(task_id="archive", prompt="Archive the item.", success_rule=item_is_archived, attempts=3),
]
```

| Limit | Meaning | Default |
| --- | --- | --- |
| `attempt_timeout_seconds` | How long one Attempt may take. | No limit |
| `experiment_timeout_seconds` | How long the experiment may take, counted from the start of its first Attempt. Once it is reached, no further Attempt or retry of that experiment is started. | No limit |
| `max_retries` | How often a planned Attempt is made again after its execution failed or timed out. | 0 |

No minimum number of Attempts and no limit is imposed. A Task with one Attempt runs. Whether a metric can be calculated from it is for that metric's Evaluator to say.

## Execution statuses of an Attempt

| `execution_status` | Meaning | Task outcome |
| --- | --- | --- |
| `completed` | The agent answered and the task-success rule decided the task outcome. | Yes |
| `failed` | The invocation raised an error. | None |
| `timed_out` | The Attempt reached its time limit. | None |
| `cancelled` | The invocation ended with an error after the sweep was cancelled. | None |
| `retried` | The execution failed or timed out, and the Attempt was made again. | None |

`execution_error` says why an Attempt did not complete. Evaluators leave out every Attempt that is not `completed` and count it as excluded.

## Time limits

The sweep cannot interrupt an invocation that is running. It applies the limit of an Attempt in two ways:

- It passes the limit to the agent target as `AttemptRequest.timeout_seconds`. The agent target is expected to stop there and raise `TimeoutError`. An invocation that raises `TimeoutError` is `timed_out`, with or without a declared limit.
- It measures each invocation. One that ends after the limit is `timed_out` as well, whether it ended with an answer or with an error. A late answer is not scored, so that the limit holds for every Attempt alike.

An agent target that ignores the limit and never returns blocks the sweep. Give your agent target a timeout of its own.

`experiment_timeout_seconds` is checked before each Attempt and each retry starts. An Attempt that is already running is bounded by `attempt_timeout_seconds` only.

## Retries

A planned Attempt whose execution `failed` or `timed_out` is made again, up to `max_retries` times.

- The retried Attempt stays in the evidence. Its status becomes `retried` and its `execution_error` keeps why it did not complete.
- The retry is another Attempt with its own identity, such as `close/attempt-2/retry-1`. Its `retry_of` names the Attempt it retries.
- A completed Attempt is never retried, whatever its task outcome. Retrying task failures would hide the instability the metrics measure.
- A cancelled Attempt is not retried. No retry starts after the sweep was cancelled or the experiment reached its time limit.
- When the retries run out, the last execution keeps its own status, `failed` or `timed_out`.

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
- `KeyboardInterrupt` and other exceptions that are not an `Exception` are not caught. They end the sweep without a scorecard. To stop a sweep and keep its evidence, use `cancellation`.

## Concurrency

Attempts run one after another, Task by Task, unless you declare otherwise:

```python
scorecard = run_sweep(
    tasks=tasks, agent_target=my_agent_target, metrics=["outcome_consistency"], max_concurrent_attempts=4
)
```

Then up to four Attempts are in flight at once, across Tasks. The agent target, the task-success rules and `cancellation` are called from several threads and must be safe for that. Attempts that overlap can disturb shared state and timing measurements. Controlling that is yours.

The scorecard records what was declared:

```json
"execution": {"concurrency": {"mode": "concurrent", "max_concurrent_attempts": 4}}
```

Without the declaration it records `"mode": "sequential"` and `"max_concurrent_attempts": 1`. The Attempts are listed in the order they were planned in both cases.

## What the scorecard says

### `status` and `missing`

`status` is `complete` when every planned Attempt completed, directly or through a retry. Otherwise it is `partial`, and `missing` names what is missing and why:

```json
"status": "partial",
"missing": [
  {
    "kind": "attempt_evidence",
    "experiment_id": "close",
    "execution_status": "timed_out",
    "reason": "its time limit of 600 seconds was reached: 2 of 5 planned Attempts were not made",
    "attempts_not_completed": [],
    "attempts_not_made": 2
  }
]
```

| `kind` | What is missing | Fields |
| --- | --- | --- |
| `attempt_evidence` | Planned Attempts of an experiment that were not made or did not complete. | `experiment_id`, the experiment's `execution_status` and `reason`, `attempts_not_completed` with each Attempt's status and error, `attempts_not_made` |
| `metric_result` | The result of a selected metric. | `metric_id`, `reason`, `experiment_ids` |

A metric that is an unavailable measurement is a result. It does not make a scorecard partial.

### `experiments`

One entry per experiment:

| Field | Content |
| --- | --- |
| `experiment_id`, `task_id` | The experiment and its Task. They are the same today. |
| `execution_status`, `reason` | How the running of the experiment ended. See the next table. |
| `limits` | The execution limits you declared. |
| `attempts` | `planned`, `completed`, `not_completed` (by the identity of the last execution), `not_made` and `retries`. |
| `attempt_ids` | Every Attempt made, retries included, in the order planned. |

| Experiment `execution_status` | Meaning |
| --- | --- |
| `completed` | Every planned Attempt completed, directly or through a retry. |
| `timed_out` | The experiment reached its time limit before that. |
| `cancelled` | The sweep was cancelled before that. |
| `failed` | Every planned Attempt was made, and some did not complete. |

### Metrics in a partial scorecard

A metric keeps its result whenever its Evaluator can still calculate a value from the evidence that was collected. The Attempts and Tasks it had to leave out are counted in its `counts`, with reasons.

When an experiment that supplies a metric's evidence did not complete and what is left does not permit a calculation, the metric has no result. That is an experiment failure. It is not an unavailable measurement, it is not "not applicable" and it is not a task failure:

```json
{
  "metric_id": "outcome_consistency",
  "state": null,
  "value": null,
  "reason": null,
  "experiment_failure": {
    "reason": "experiments that supply this metric's evidence did not complete, and what they collected does not permit a calculation: no Task has at least two Attempts with a task outcome",
    "experiment_ids": ["close"]
  }
}
```

| What you see on a selected metric | What it means | What to do |
| --- | --- | --- |
| `state` is `available` | A value was calculated. In a partial scorecard, read its `counts` to see what it rests on. | |
| `state` is `unavailable` | Everything planned was collected and it does not permit a calculation. | Collect more evidence. |
| `state` is `null` and `experiment_failure` is set | Experiments did not complete. What complete evidence would have shown is not known. | Investigate the failure. |

`experiment_failure` is `null` on every metric that has a result. The same failure is listed in `missing` with the kind `metric_result`.

## Choices made here

The specification leaves these open. They are stated so that you can disagree with them knowingly.

- **One experiment per Task.** The sweep has one condition today, so the Attempts planned at a Task are the experiment. An experiment's identity is its Task's.
- **The sweep does not interrupt invocations.** It hands the limit to the agent target and measures. Abandoning a running invocation would let it overlap the next Attempt in a sweep that is declared sequential.
- **A late answer is not scored.** An Attempt that ends after its limit is `timed_out` even when the agent answered.
- **`retried` replaces the status, not the Attempt.** A retried Attempt reads `retried`. Whether it had failed or timed out is in its `execution_error`.
- **A scorecard is partial as soon as one planned Attempt has no completed execution.** This holds even when every metric could still be calculated.
- **A metric without a result has no metric state.** Experiment execution status is separate from metric state, so an experiment failure is shown as `state: null` with `experiment_failure`, not as a fifth state.
- **Unavailable means the planned evidence was collected.** A metric is reported as an unavailable measurement only when every experiment that supplies its evidence completed. The rule is not relaxed for a plan that could never have supported the calculation, such as one Attempt per Task: if one of its experiments did not complete, the scorecard reports the experiment failure.

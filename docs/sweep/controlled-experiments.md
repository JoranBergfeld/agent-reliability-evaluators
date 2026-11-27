# Controlled experiments in the Evaluator sweep

An experiment tells the Evaluator sweep what state each Attempt starts from, which condition it runs under, and how to check that the condition really occurred. You declare the experiment before any Attempt is made and supply four controls. The sweep invokes the controls around every Attempt.

The sweep never looks inside your agent's tools. It also never assumes that the agent, Foundry or your service resets a conversation or any external state. If state has to be reset, your prepare control resets it.

## Declaring an experiment

```python
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import Condition, Experiment, ObservedCondition, Task, run_sweep


class TodoControls:
    def prepare_state(self, context):
        for resource in context.authorized_resources:
            my_test_service.reset(resource)

    def apply_condition(self, context):
        my_test_service.set_fault(context.condition.parameters.get("failing_operation"))

    def verify_condition(self, context):
        faulted = my_test_service.count_faulted_requests()
        intended = "failing_operation" in context.condition.parameters
        return ObservedCondition(
            met=(faulted > 0) == intended,
            source="request log of the to-do test service",
            observations={"faulted_requests": faulted},
        )

    def clean_up(self, context):
        my_test_service.set_fault(None)


def item_is_closed(attempt, response):
    closed = my_test_service.item_is_closed("todo-list/reliability-test")
    return TaskOutcome(success=closed, source="final state of the to-do test service")


experiment = Experiment(
    experiment_id="close-item-under-update-fault",
    task=Task(task_id="close", prompt="Close the item.", success_rule=item_is_closed, attempts=5),
    agent_configuration={"agent": "todo-agent", "version": "3", "model": "gpt-4.1"},
    starting_state="fresh",
    baseline=Condition(condition_id="baseline", description="Every operation works."),
    variation=Condition(
        condition_id="update-fails",
        description="The update operation returns 503.",
        parameters={"failing_operation": "update"},
    ),
    controls=TodoControls(),
    write_capable=True,
    authorized_resources=["todo-list/reliability-test"],
)

scorecard = run_sweep(experiments=[experiment], agent_target=my_agent_target, metrics=["outcome_consistency"])
```

| Field | What you declare |
| --- | --- |
| `experiment_id` | The identity of the experiment. Unique in a sweep. |
| `task` | The Task, its task-success rule, and the number of Attempts made under each condition. |
| `agent_configuration` | What identifies the agent under test, such as name, version and model. It is recorded. The sweep does not configure the agent from it. |
| `starting_state` | `"fresh"` or `"continuing"`. There is no default. |
| `baseline` | The condition the variation is compared with. |
| `variation` | The one intended difference from the baseline, or `None` for an experiment with a baseline only. There is no default. |
| `controls` | An object with the four controls below. |
| `write_capable` | Whether the agent or the controls can change anything outside the conversation. There is no default. |
| `authorized_resources` | The names of the test resources the experiment may change. Required when `write_capable` is true, and not allowed otherwise. |

An experiment that leaves one of these out, or declares one that cannot be used, is rejected when it is created. A sweep is rejected before any Attempt is made when two experiments have the same identity, when a Task is declared more than once, or when two Attempts would get the same identity.

The `parameters` of a condition, the `agent_configuration` and the `observations` of an observed condition must be JSON-compatible, because they are copied into the scorecard.

## The four controls

For one Attempt the sweep does this, in this order:

1. `prepare_state`: bring the test setup into the state the Attempt starts from.
2. `apply_condition`: put the intended condition in place. It is invoked for the baseline too.
3. The agent is invoked and the task-success rule decides the task outcome.
4. `verify_condition`: report, from evidence, whether the intended condition occurred.
5. When a selected metric uses confidence, the agent is asked for its confidence rating in a separate request. It is not asked when the Attempt did not complete: the setup failed, the invocation failed, or the condition was not met. See [how the confidence rating is collected](../brier_based_score/readme.md#how-the-confidence-rating-is-collected).
6. `clean_up`: restore the test setup.

Each control receives one argument with these attributes:

| Attribute | Contents |
| --- | --- |
| `experiment_id`, `task_id`, `attempt_id` | Which Attempt the control is invoked for. |
| `condition` | The intended condition, with its `condition_id`, `description` and `parameters`. |
| `condition_role` | Whether that condition is the baseline or the variation. |
| `starting_state` | The declared starting-state policy. |
| `authorized_resources` | The test resources the experiment may change. A control should change nothing else. |

A control that cannot do its work raises an exception. `verify_condition` returns an `ObservedCondition`:

- `met`: `True` when the intended condition occurred, otherwise `False`.
- `source`: what that was observed from, such as the request log of a test tool.
- `observations`: optional details, kept in the scorecard.

A label is not an observation. If `verify_condition` returns anything other than an `ObservedCondition`, the condition counts as not verified.

The sweep passes `authorized_resources` to the controls and records them. It cannot stop a control or an agent from changing something else.

## Starting-state policy

| Policy | `prepare_state` | `clean_up` |
| --- | --- | --- |
| `fresh` | Before every Attempt. | After every Attempt. |
| `continuing` | Once, before the first Attempt under each condition. | Once, after the last Attempt under each condition. |

`apply_condition` and `verify_condition` are invoked for every Attempt under both policies.

Under `continuing`, each Attempt starts from the state the previous Attempt under the same condition left. The variation does not continue from the baseline: it gets its own prepared state, so that the two differ only by the declared variation.

State continues only when you declare `continuing`. Tasks passed to `run_sweep(tasks=...)` without an experiment have no controls: nothing prepares or restores their state, and their Attempts record `"starting_state": null`, meaning that no policy was declared.

## When a control fails

Each of these is an execution status of the Attempt, with the reason in `execution_error`. None of them is a task failure: an Evaluator leaves out every Attempt whose execution status is not `completed` and counts it with a reason.

| Execution status | When | Agent invoked | Evidence kept |
| --- | --- | --- | --- |
| `setup_failed` | `prepare_state` or `apply_condition` raised. | No | The reason and the controls that were invoked. |
| `condition_not_met` | `verify_condition` reported `met=False`, raised, or returned no `ObservedCondition`. | Yes | The task outcome, the observed condition when there is one, and the reason. |
| `cleanup_failed` | `clean_up` raised after an Attempt that had completed. | Yes | The task outcome, the observed condition, the confidence rating when the agent was asked for one, and the reason. |
| `failed` | The invocation of the agent raised. | Yes, without a response | The reason, and the observed condition: `verify_condition` is still invoked. |

An Attempt keeps the status of the first thing that went wrong. If `clean_up` then fails as well, that failure is in the Attempt's `lifecycle`.

`clean_up` is invoked after a failure too, so it must cope with state that was only partly prepared.

### What stops, and what continues

- **`clean_up` fails.** The later Attempts of that experiment are not made, because they would start from state that was not restored. Later experiments that share one of its `authorized_resources` are not started. Every other experiment continues.
- **`prepare_state` fails under `continuing`.** The later Attempts of that experiment are not made, because they would continue from state that was never prepared. `clean_up` is invoked. Other experiments continue.
- **`prepare_state` fails under `fresh`.** Only that Attempt has a failed setup. The next Attempt prepares its own state.

An experiment whose Attempts were not all made has the execution status `stopped`, with the reason and the number of Attempts planned and recorded. Attempts that were not made do not appear as Attempts.

## What the scorecard records

Every Attempt records its experiment, its starting-state policy, the intended condition, the observed condition and the controls invoked for it:

```json
{
  "attempt_id": "close-item-under-update-fault/update-fails/attempt-1",
  "task_id": "close",
  "execution_status": "completed",
  "execution_error": null,
  "task_outcome": {"success": false, "source": "final state of the to-do test service"},
  "experiment_id": "close-item-under-update-fault",
  "starting_state": "fresh",
  "intended_condition": {
    "role": "variation",
    "condition_id": "update-fails",
    "description": "The update operation returns 503.",
    "parameters": {"failing_operation": "update"}
  },
  "observed_condition": {
    "met": true,
    "source": "request log of the to-do test service",
    "observations": {"faulted_requests": 1}
  },
  "lifecycle": [
    {"point": "prepare_state", "succeeded": true, "error": null},
    {"point": "apply_condition", "succeeded": true, "error": null},
    {"point": "verify_condition", "succeeded": true, "error": null},
    {"point": "clean_up", "succeeded": true, "error": null}
  ]
}
```

The Attempt identity is `<experiment_id>/<condition_id>/attempt-<number>`. When an Evaluator needs Attempts that are made differently from another's, the experiment is run again for it under the identity `<experiment_id>/set-2`: see [several Evaluators in one sweep](several-evaluators.md).

`experiments` lists what each experiment declared and how its execution ended:

```json
{
  "experiment_id": "close-item-under-update-fault",
  "task_id": "close",
  "agent_configuration": {"agent": "todo-agent", "version": "3", "model": "gpt-4.1"},
  "starting_state": "fresh",
  "baseline": {"condition_id": "baseline", "description": "Every operation works.", "parameters": {}},
  "variation": {
    "condition_id": "update-fails",
    "description": "The update operation returns 503.",
    "parameters": {"failing_operation": "update"}
  },
  "write_capable": true,
  "authorized_resources": ["todo-list/reliability-test"],
  "execution": {"status": "completed", "reason": null, "attempts_planned": 10, "attempts_recorded": 10}
}
```

## Limits in this version

- Attempts under a variation are made and recorded, but no Evaluator uses them yet. The Evaluators that exist score Attempts under the baseline only, and each metric's `attempts_used` in the scorecard lists the Attempts its Evaluator was given.
- A Task can be declared in one experiment only, so that Attempts made under different declarations are never mixed in one Task's evidence.
- If a task-success rule returns something that is not a `TaskOutcome`, the sweep stops with an error and does not invoke `clean_up`.

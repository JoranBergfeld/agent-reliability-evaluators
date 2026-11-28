"""The fixture's Task as a customer declares it, for both ways the tests give it a state of the to-do service.

`tests/todo_experiment.py` gives every Attempt a state of its own and `tests/todo_fixture_sweep.py`
gives an experiment one. What they share is here: the task-success rule, where an observed
condition comes from, and the experiment the Task is attempted in.
"""

from __future__ import annotations

from live_fixture import strict_task_outcome
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import Experiment, Task

EVENTS_SOURCE = "events of the to-do service"


def strict_outcome(service, state: str) -> TaskOutcome:
    """The task outcome of the Attempt that worked on `state`: the fixture's strict rule on what the service recorded."""
    outcome = strict_task_outcome(service.evidence(state))
    return TaskOutcome(success=outcome["success"], source=outcome["source"])


def faulted_calls(events) -> list[str]:
    """The tools of the calls the service injected a fault into, in the order of the calls."""
    return [event["tool"] for event in events if event["fault_injected"]]


def experiment_on_the_fixture(controls, *, task_id, prompt, success_rule, authorized_resource, **declared) -> Experiment:
    """The fixture's Task in an experiment that `controls` prepares, verifies and cleans up.

    The Task changes the service, so the experiment is write-capable and names the state it
    may change. `declared` is the rest of the Task and of the experiment, as the caller declares it.
    """
    of_the_task = {name: declared.pop(name) for name in ("attempts", "limits", "shareable_success_rule")}
    return Experiment(
        experiment_id=task_id,
        task=Task(task_id=task_id, prompt=prompt, success_rule=success_rule, **of_the_task),
        controls=controls,
        write_capable=True,
        authorized_resources=[authorized_resource],
        **declared,
    )

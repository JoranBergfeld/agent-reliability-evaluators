"""The Evaluator sweep's public entry point."""

from __future__ import annotations

from typing import Any, Sequence

from ..agent_target import AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, ScoringPath
from ..evidence import (
    Attempt,
    ConditionRole,
    EvidenceError,
    ExecutionStatus,
    LifecyclePoint,
    LifecycleStep,
    ObservedCondition,
    StartingStatePolicy,
    TaskOutcome,
    group_by_task,
)
from ..scorecard import ScoredMetric, build_scorecard
from .declaration import ControlContext, CustomerControls, Experiment, Task


def run_sweep(
    *,
    tasks: Sequence[Task] = (),
    experiments: Sequence[Experiment] = (),
    agent_target: AgentTarget,
    metrics: Sequence[str],
) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` names the reliability metrics to score, by metric identity. Attempts run one
    after another, Task by Task, and scoring happens in this process. The result is the
    machine-readable reliability scorecard, as JSON-compatible data.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that does not return a sourced, binary TaskOutcome stops the sweep.
    """
    evaluators = [_evaluator(metric_id) for metric_id in metrics]
    _require_distinct_tasks(tasks)
    attempts = [
        _run_attempt(task, number, agent_target) for task in tasks for number in range(1, task.attempts + 1)
    ]
    for experiment in experiments:
        attempts.extend(_run_experiment(experiment, agent_target))
    # The Evaluators that exist score repeated Attempts under the baseline. Attempts under a
    # variation are kept in the scorecard and are not mixed in.
    evidence = group_by_task(
        attempt for attempt in attempts if attempt.condition_role is not ConditionRole.VARIATION
    )
    scored = [
        ScoredMetric(
            result=evaluator.evaluate(evidence),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
        )
        for evaluator in evaluators
    ]
    return build_scorecard(metrics=scored, attempts=attempts)


def _evaluator(metric_id: str) -> Evaluator:
    if metric_id in EVALUATORS:
        return EVALUATORS[metric_id]
    if any(metric.metric_id == metric_id for metric in RELIABILITY_METRICS):
        raise ValueError(f"{metric_id!r} has no Evaluator in this version")
    raise ValueError(f"{metric_id!r} is not a reliability metric")


def _require_distinct_tasks(tasks: Sequence[Task]) -> None:
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(f"more than one Task is declared as {task.task_id!r}")
        seen.add(task.task_id)


def _run_attempt(task: Task, number: int, agent_target: AgentTarget) -> Attempt:
    request = AttemptRequest(
        attempt_id=f"{task.task_id}/attempt-{number}", task_id=task.task_id, prompt=task.prompt
    )
    try:
        response = agent_target.invoke(request)
    except Exception as error:
        return Attempt(
            attempt_id=request.attempt_id,
            task_id=task.task_id,
            execution_status=ExecutionStatus.FAILED,
            execution_error=f"{type(error).__name__}: {error}",
        )
    outcome = task.success_rule(request, response)
    if not isinstance(outcome, TaskOutcome):
        raise EvidenceError(
            f"the task-success rule of Task {task.task_id!r} must return a TaskOutcome with its source, "
            f"got {outcome!r}"
        )
    return Attempt(
        attempt_id=request.attempt_id,
        task_id=task.task_id,
        execution_status=ExecutionStatus.COMPLETED,
        task_outcome=outcome,
    )


def _run_experiment(experiment: Experiment, agent_target: AgentTarget) -> list[Attempt]:
    task = experiment.task
    fresh = experiment.starting_state is StartingStatePolicy.FRESH
    attempts = []
    for role, condition in experiment.conditions():
        for number in range(1, task.attempts + 1):
            context = ControlContext(
                experiment_id=experiment.experiment_id,
                task_id=task.task_id,
                attempt_id=f"{experiment.experiment_id}/{condition.condition_id}/attempt-{number}",
                condition=condition,
                condition_role=role,
                starting_state=experiment.starting_state,
                authorized_resources=experiment.authorized_resources,
            )
            attempts.append(
                _run_controlled_attempt(
                    experiment,
                    context,
                    agent_target,
                    prepares=fresh or number == 1,
                    cleans_up=fresh or number == task.attempts,
                )
            )
    return attempts


def _run_controlled_attempt(
    experiment: Experiment, context: ControlContext, agent_target: AgentTarget, *, prepares: bool, cleans_up: bool
) -> Attempt:
    """Make one Attempt of an experiment, invoking the customer controls around it."""
    task, controls = experiment.task, experiment.controls
    steps = []
    status, error = ExecutionStatus.COMPLETED, None
    if prepares:
        controls.prepare_state(context)
        steps.append(LifecycleStep(LifecyclePoint.PREPARE_STATE))
    controls.apply_condition(context)
    steps.append(LifecycleStep(LifecyclePoint.APPLY_CONDITION))
    request = AttemptRequest(attempt_id=context.attempt_id, task_id=task.task_id, prompt=task.prompt)
    response = agent_target.invoke(request)
    observed, verify_error = _verify_condition(controls, context, steps)
    if verify_error is not None:
        status = ExecutionStatus.CONDITION_NOT_MET
        error = f"the intended condition was not verified: {verify_error}"
    elif not observed.met:
        status = ExecutionStatus.CONDITION_NOT_MET
        error = "the verify control observed that the intended condition did not occur"
    outcome = task.success_rule(request, response)
    if cleans_up:
        controls.clean_up(context)
        steps.append(LifecycleStep(LifecyclePoint.CLEAN_UP))
    return Attempt(
        attempt_id=context.attempt_id,
        task_id=task.task_id,
        execution_status=status,
        execution_error=error,
        task_outcome=outcome,
        experiment_id=experiment.experiment_id,
        starting_state=experiment.starting_state,
        intended_condition=context.condition,
        condition_role=context.condition_role,
        observed_condition=observed,
        lifecycle=tuple(steps),
    )


def _verify_condition(
    controls: CustomerControls, context: ControlContext, steps: list[LifecycleStep]
) -> tuple[ObservedCondition | None, str | None]:
    """The condition the verify control observed, or why it observed none."""
    observed, error = _invoke_control(LifecyclePoint.VERIFY_CONDITION, controls, context, steps)
    if error is None and not isinstance(observed, ObservedCondition):
        error = f"the verify control returned {observed!r}, not an ObservedCondition with its source"
        steps[-1] = LifecycleStep(LifecyclePoint.VERIFY_CONDITION, error)
    return (None, error) if error is not None else (observed, None)


def _invoke_control(
    point: LifecyclePoint, controls: CustomerControls, context: ControlContext, steps: list[LifecycleStep]
) -> tuple[Any, str | None]:
    """Invoke a customer control, record it in `steps`, and return its result and why it failed."""
    try:
        result = getattr(controls, point.value)(context)
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"
        steps.append(LifecycleStep(point, error))
        return None, error
    steps.append(LifecycleStep(point))
    return result, None

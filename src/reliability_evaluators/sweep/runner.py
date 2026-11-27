"""The Evaluator sweep's public entry point."""

from __future__ import annotations

from typing import Any, Sequence

from ..agent_target import AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, ScoringPath
from ..evidence import Attempt, ConditionRole, EvidenceError, ExecutionStatus, TaskOutcome, group_by_task
from ..scorecard import ScoredMetric, build_scorecard
from .declaration import Experiment, Task
from .lifecycle import planned_attempt_ids, run_experiments


def run_sweep(
    *,
    tasks: Sequence[Task] = (),
    experiments: Sequence[Experiment] = (),
    agent_target: AgentTarget,
    metrics: Sequence[str],
) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` names the reliability metrics to score, by metric identity. Attempts run one
    after another, Task by Task and then experiment by experiment, and scoring happens in
    this process. The result is the machine-readable reliability scorecard, as
    JSON-compatible data.

    `experiments` are controlled experiments: the sweep invokes their customer controls
    around every Attempt and follows their starting-state policy. `tasks` are Tasks given
    without an experiment: nothing prepares or restores their state, and their Attempts
    record no starting-state policy and no condition. The sweep never assumes that the
    agent target resets conversation or external state.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that does not return a sourced, binary TaskOutcome stops the sweep.
    """
    evaluators = [_evaluator(metric_id) for metric_id in metrics]
    _require_distinct_tasks([*tasks, *(experiment.task for experiment in experiments)])
    _require_distinct_identities(tasks, experiments)
    attempts = [
        _run_attempt(task, number, agent_target) for task in tasks for number in range(1, task.attempts + 1)
    ]
    experiment_attempts, experiment_records = run_experiments(
        experiments,
        lambda task, number, attempt_id: _run_attempt(task, number, agent_target, attempt_id=attempt_id),
    )
    attempts.extend(experiment_attempts)
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
    return build_scorecard(metrics=scored, attempts=attempts, experiments=experiment_records)


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


def _require_distinct_identities(tasks: Sequence[Task], experiments: Sequence[Experiment]) -> None:
    seen: set[str] = set()
    for experiment in experiments:
        if experiment.experiment_id in seen:
            raise ValueError(f"more than one experiment is declared as {experiment.experiment_id!r}")
        seen.add(experiment.experiment_id)
    planned: set[str] = set()
    for attempt_id in (
        *(_attempt_id(task, number) for task in tasks for number in range(1, task.attempts + 1)),
        *(attempt_id for experiment in experiments for attempt_id in planned_attempt_ids(experiment)),
    ):
        if attempt_id in planned:
            raise ValueError(f"more than one Attempt would have the identity {attempt_id!r}")
        planned.add(attempt_id)


def _attempt_id(task: Task, number: int) -> str:
    return f"{task.task_id}/attempt-{number}"


def _run_attempt(task: Task, number: int, agent_target: AgentTarget, attempt_id: str | None = None) -> Attempt:
    request = AttemptRequest(
        attempt_id=attempt_id or _attempt_id(task, number), task_id=task.task_id, prompt=task.prompt
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

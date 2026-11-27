"""The Evaluator sweep's public entry point."""

from __future__ import annotations

from typing import Any, Sequence

from ..agent_target import AgentResponse, AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, ScoringPath
from ..evidence import Attempt, EvidenceError, ExecutionStatus, TaskOutcome, ViolationAssessment, group_by_task
from ..scorecard import AcceptanceCriterion, ScoredMetric, build_scorecard, criteria_by_metric
from .declaration import Task


def run_sweep(
    *,
    tasks: Sequence[Task],
    agent_target: AgentTarget,
    metrics: Sequence[str],
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` names the reliability metrics to score, by metric identity. Attempts run one
    after another, Task by Task, and scoring happens in this process. The result is the
    machine-readable reliability scorecard, as JSON-compatible data.

    `acceptance_criteria` are the customer's own, at most one per metric. Each is reported
    with its metric as met, not met, or not assessed with a reason. Without one, a metric
    has no acceptance verdict.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that does not return a sourced, binary TaskOutcome stops the sweep. So does a
    violation assessor that does not return sourced violation assessments of the operational
    constraints its Task declares.
    """
    evaluators = [_evaluator(metric_id) for metric_id in metrics]
    _require_distinct_tasks(tasks)
    # Called for its check: a second criterion for one metric is rejected here, not after the Attempts.
    criteria_by_metric(acceptance_criteria)
    attempts = [
        _run_attempt(task, number, agent_target) for task in tasks for number in range(1, task.attempts + 1)
    ]
    evidence = group_by_task(attempts)
    scored = [
        ScoredMetric(
            result=evaluator.evaluate(evidence),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
        )
        for evaluator in evaluators
    ]
    return build_scorecard(metrics=scored, attempts=attempts, acceptance_criteria=acceptance_criteria)


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
            operational_constraints=task.operational_constraints,
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
        operational_constraints=task.operational_constraints,
        violation_assessments=_assess_violations(task, request, response),
    )


def _assess_violations(
    task: Task, request: AttemptRequest, response: AgentResponse
) -> Sequence[ViolationAssessment]:
    if task.violation_assessor is None:
        return ()
    assessments = task.violation_assessor(request, response)
    if (
        not isinstance(assessments, Sequence)
        or isinstance(assessments, str)
        or not all(isinstance(assessment, ViolationAssessment) for assessment in assessments)
    ):
        raise EvidenceError(
            f"the violation assessor of Task {task.task_id!r} must return a sequence of ViolationAssessment, "
            f"each with its evidence and source, got {assessments!r}"
        )
    return assessments

"""The Evaluator sweep's public entry point."""

from __future__ import annotations

import os
from typing import Any, Sequence

from ..agent_target import AgentResponse, AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, ScoringPath
from ..evidence import (
    ActionEvidence,
    Attempt,
    EvidenceError,
    ExecutionStatus,
    TaskOutcome,
    ViolationAssessment,
    group_by_task,
)
from ..scorecard import AcceptanceCriterion, ArtifactLocation, ScoredMetric, build_scorecard, criteria_by_metric
from .declaration import ActionEvidenceDeclaration, Task


def run_sweep(
    *,
    tasks: Sequence[Task],
    agent_target: AgentTarget,
    metrics: Sequence[str],
    action_evidence: ActionEvidenceDeclaration | None = None,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
    artifact_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` names the reliability metrics to score, by metric identity. Attempts run one
    after another, Task by Task, and scoring happens in this process. The result is the
    machine-readable reliability scorecard, as JSON-compatible data.

    `action_evidence` declares where the actions of every Attempt are recorded from and
    what action type each tool name is. The trajectory metrics need it, and every metric
    that needs it is scored from the same Attempts.

    `acceptance_criteria` are the customer's own, at most one per metric. Each is reported
    with its metric as met, not met, or not assessed with a reason. Without one, a metric
    has no acceptance verdict.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that does not return a sourced, binary TaskOutcome stops the sweep. So does a
    violation assessor that does not return sourced violation assessments of the operational
    constraints its Task declares, and an action recording that is not a sequence of ActionEvent.

    With `artifact_dir`, the attempt-level evidence, the scorecard and the human-readable
    report rendered from it are also written to that directory. The caller chooses it in the
    run or artifact context of the platform it runs on. The sweep then either delivers all
    three or raises ReportingFailure: whatever stops it, a rejected declaration included, is
    raised as that, and the evidence collected until then is kept at the location.
    """
    location = None if artifact_dir is None else ArtifactLocation(artifact_dir)
    attempts: list[Attempt] = []
    try:
        scorecard = _sweep(
            attempts,
            tasks=tasks,
            agent_target=agent_target,
            metrics=metrics,
            action_evidence=action_evidence,
            acceptance_criteria=acceptance_criteria,
        )
    except Exception as error:
        if location is None:
            raise
        raise location.failed(
            "the Evaluator sweep stopped before it produced a scorecard",
            error,
            evidence=[attempt.to_dict() for attempt in attempts],
        ) from error
    if location is not None:
        location.deliver(scorecard)
    return scorecard


def _sweep(
    attempts: list[Attempt],
    *,
    tasks: Sequence[Task],
    agent_target: AgentTarget,
    metrics: Sequence[str],
    action_evidence: ActionEvidenceDeclaration | None,
    acceptance_criteria: Sequence[AcceptanceCriterion],
) -> dict[str, Any]:
    """Make the Attempts and score them. Each Attempt is added to `attempts` as it is made,
    so that `run_sweep` still has the evidence if this stops.
    """
    evaluators = [_evaluator(metric_id) for metric_id in metrics]
    declared = {"action_types": None if action_evidence is None else action_evidence.mapping}
    for evaluator in evaluators:
        _require_declarations(evaluator, declared)
    _require_distinct_tasks(tasks)
    # Called for its check: a second criterion for one metric is rejected here, not after the Attempts.
    criteria_by_metric(acceptance_criteria)
    for task in tasks:
        for number in range(1, task.attempts + 1):
            attempts.append(_run_attempt(task, number, agent_target, action_evidence))
    evidence = group_by_task(attempts)
    scored = [
        ScoredMetric(
            result=evaluator.evaluate(evidence, *(declared[name] for name in evaluator.declarations)),
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


def _require_declarations(evaluator: Evaluator, declared: dict[str, Any]) -> None:
    undeclared = {
        "action_types": "action evidence to be declared: the evidence source and the action types have no default",
    }
    for name in evaluator.declarations:
        if declared[name] is None:
            raise ValueError(f"{evaluator.definition.metric.metric_id!r} needs {undeclared[name]}")


def _require_distinct_tasks(tasks: Sequence[Task]) -> None:
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(f"more than one Task is declared as {task.task_id!r}")
        seen.add(task.task_id)


def _run_attempt(
    task: Task, number: int, agent_target: AgentTarget, action_evidence: ActionEvidenceDeclaration | None
) -> Attempt:
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
        action_evidence=None if action_evidence is None else _recorded_actions(action_evidence, request, response),
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


def _recorded_actions(
    declaration: ActionEvidenceDeclaration, request: AttemptRequest, response: AgentResponse
) -> ActionEvidence | None:
    events = declaration.record(request, response)
    if events is None:
        return None
    return ActionEvidence(source=declaration.evidence_source, events=events)

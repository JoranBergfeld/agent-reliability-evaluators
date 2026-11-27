"""The Evaluator sweep's public entry point."""

from __future__ import annotations

import os
from typing import Any, Sequence

from ..agent_target import AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, ScoringPath
from ..evidence import Attempt, EvidenceError, ExecutionStatus, TaskOutcome, group_by_task
from ..scorecard import ArtifactLocation, ScoredMetric, build_scorecard
from .declaration import Task


def run_sweep(
    *,
    tasks: Sequence[Task],
    agent_target: AgentTarget,
    metrics: Sequence[str],
    artifact_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` names the reliability metrics to score, by metric identity. Attempts run one
    after another, Task by Task, and scoring happens in this process. The result is the
    machine-readable reliability scorecard, as JSON-compatible data.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that does not return a sourced, binary TaskOutcome stops the sweep.

    With `artifact_dir`, the attempt-level evidence, the scorecard and the human-readable
    report rendered from it are also written to that directory. The caller chooses it in the
    run or artifact context of the platform it runs on. The sweep then either delivers all
    three or raises ReportingFailure: whatever stops it, a rejected declaration included, is
    raised as that, and the evidence collected until then is kept at the location.
    """
    attempts: list[Attempt] = []
    if artifact_dir is None:
        return _sweep(tasks, agent_target, metrics, attempts)
    location = ArtifactLocation(artifact_dir)
    try:
        scorecard = _sweep(tasks, agent_target, metrics, attempts)
    except Exception as error:
        raise location.failed(
            "the Evaluator sweep stopped before it produced a scorecard",
            error,
            evidence=[attempt.to_dict() for attempt in attempts],
        ) from error
    location.deliver(scorecard)
    return scorecard


def _sweep(
    tasks: Sequence[Task], agent_target: AgentTarget, metrics: Sequence[str], attempts: list[Attempt]
) -> dict[str, Any]:
    """Collect into `attempts`, so that the caller still has the evidence if the sweep stops."""
    evaluators = [_evaluator(metric_id) for metric_id in metrics]
    _require_distinct_tasks(tasks)
    for task in tasks:
        for number in range(1, task.attempts + 1):
            attempts.append(_run_attempt(task, number, agent_target))
    evidence = group_by_task(attempts)
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

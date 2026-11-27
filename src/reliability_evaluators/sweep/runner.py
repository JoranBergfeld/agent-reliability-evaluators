"""The Evaluator sweep's public entry point."""

from __future__ import annotations

from typing import Any, Sequence

from ..agent_target import AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, ScoringPath
from ..evidence import (
    Attempt,
    ConfidenceUnavailable,
    EvidenceError,
    ExecutionStatus,
    TaskOutcome,
    group_by_task,
)
from ..scorecard import ScoredMetric, build_scorecard
from .confidence import ATTEMPT_NOT_COMPLETED, elicit_confidence
from .declaration import Task


def run_sweep(*, tasks: Sequence[Task], agent_target: AgentTarget, metrics: Sequence[str]) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` names the reliability metrics to score, by metric identity. Attempts run one
    after another, Task by Task, and scoring happens in this process. The result is the
    machine-readable reliability scorecard, as JSON-compatible data.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that does not return a sourced, binary TaskOutcome stops the sweep.
    """
    evaluators = [_evaluator(metric_id) for metric_id in metrics]
    _require_distinct_tasks(tasks)
    ask_confidence = any(evaluator.needs_confidence for evaluator in evaluators)
    attempts = [
        _run_attempt(task, number, agent_target, ask_confidence)
        for task in tasks
        for number in range(1, task.attempts + 1)
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


def _run_attempt(task: Task, number: int, agent_target: AgentTarget, ask_confidence: bool) -> Attempt:
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
            confidence=ConfidenceUnavailable(reason=ATTEMPT_NOT_COMPLETED) if ask_confidence else None,
        )
    outcome = task.success_rule(request, response)
    if not isinstance(outcome, TaskOutcome):
        raise EvidenceError(
            f"the task-success rule of Task {task.task_id!r} must return a TaskOutcome with its source, "
            f"got {outcome!r}"
        )
    # The task outcome is decided before the agent is asked for its confidence rating, so the
    # rating cannot enter the outcome, and the follow-up cannot disturb what the outcome is decided from.
    confidence = elicit_confidence(task, request, response, agent_target) if ask_confidence else None
    return Attempt(
        attempt_id=request.attempt_id,
        task_id=task.task_id,
        execution_status=ExecutionStatus.COMPLETED,
        task_outcome=outcome,
        confidence=confidence,
    )

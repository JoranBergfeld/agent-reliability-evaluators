"""The Evaluator sweep's public entry point."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable, Sequence

from ..agent_target import AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, ScoringPath
from ..evidence import Attempt, EvidenceError, ExecutionStatus, TaskOutcome, group_by_task
from ..scorecard import ExperimentRun, ScoredMetric, build_scorecard
from .declaration import Task


def run_sweep(
    *,
    tasks: Sequence[Task],
    agent_target: AgentTarget,
    metrics: Sequence[str],
    clock: Callable[[], float] = time.monotonic,
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
    attempts: list[Attempt] = []
    experiments: list[ExperimentRun] = []
    for task in tasks:
        planned = [_run_planned_attempt(task, number, agent_target, clock) for number in range(1, task.attempts + 1)]
        attempts.extend(attempt for executions in planned for attempt in executions)
        experiments.append(_experiment_run(task, planned))
    evidence = group_by_task(attempts)
    scored = [
        ScoredMetric(
            result=evaluator.evaluate(evidence),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
        )
        for evaluator in evaluators
    ]
    return build_scorecard(metrics=scored, attempts=attempts, experiments=experiments)


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


def _experiment_run(task: Task, planned: Sequence[Sequence[Attempt]]) -> ExperimentRun:
    """Sum up one experiment from its planned Attempts, each given as its executions in order."""
    not_completed = tuple(
        executions[-1].attempt_id
        for executions in planned
        if executions[-1].execution_status is not ExecutionStatus.COMPLETED
    )
    status, reason = ExecutionStatus.COMPLETED, None
    if not_completed:
        status = ExecutionStatus.FAILED
        reason = f"{len(not_completed)} of {task.attempts} planned Attempts did not complete"
    return ExperimentRun(
        # The Attempts planned at one Task are one experiment.
        experiment_id=task.task_id,
        task_id=task.task_id,
        planned_attempts=task.attempts,
        limits=task.limits.to_dict(),
        execution_status=status,
        reason=reason,
        attempt_ids=tuple(attempt.attempt_id for executions in planned for attempt in executions),
        not_completed=not_completed,
        retries=sum(len(executions) - 1 for executions in planned),
    )


_RETRYABLE = (ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT)


def _run_planned_attempt(
    task: Task, number: int, agent_target: AgentTarget, clock: Callable[[], float]
) -> list[Attempt]:
    """Make one planned Attempt and its retries. Every execution is returned, in order."""
    first = f"{task.task_id}/attempt-{number}"
    executions = [_run_attempt(task, first, None, agent_target, clock)]
    for retry in range(1, task.limits.max_retries + 1):
        if executions[-1].execution_status not in _RETRYABLE:
            break
        # The Attempt stays in the evidence with why it did not complete. The retry is another Attempt.
        executions[-1] = replace(executions[-1], execution_status=ExecutionStatus.RETRIED)
        executions.append(_run_attempt(task, f"{first}/retry-{retry}", executions[-1].attempt_id, agent_target, clock))
    return executions


def _run_attempt(
    task: Task, attempt_id: str, retry_of: str | None, agent_target: AgentTarget, clock: Callable[[], float]
) -> Attempt:
    limit = task.limits.attempt_timeout_seconds
    request = AttemptRequest(attempt_id=attempt_id, task_id=task.task_id, prompt=task.prompt, timeout_seconds=limit)

    def not_completed(status: ExecutionStatus, why: str) -> Attempt:
        return Attempt(
            attempt_id=attempt_id, task_id=task.task_id, execution_status=status, execution_error=why, retry_of=retry_of
        )

    started = clock()
    try:
        response = agent_target.invoke(request)
    except Exception as error:
        why = f"{type(error).__name__}: {error}"
        if isinstance(error, TimeoutError):
            return not_completed(ExecutionStatus.TIMED_OUT, why)
        if _over(limit, started, clock):
            return not_completed(ExecutionStatus.TIMED_OUT, f"{why}, after the Attempt's time limit of {limit:g} seconds")
        return not_completed(ExecutionStatus.FAILED, why)
    if _over(limit, started, clock):
        # The limit applies to every Attempt alike, so a late answer is not scored.
        return not_completed(
            ExecutionStatus.TIMED_OUT, f"the agent target answered after the Attempt's time limit of {limit:g} seconds"
        )
    outcome = task.success_rule(request, response)
    if not isinstance(outcome, TaskOutcome):
        raise EvidenceError(
            f"the task-success rule of Task {task.task_id!r} must return a TaskOutcome with its source, "
            f"got {outcome!r}"
        )
    return Attempt(
        attempt_id=attempt_id,
        task_id=task.task_id,
        execution_status=ExecutionStatus.COMPLETED,
        task_outcome=outcome,
        retry_of=retry_of,
    )


def _over(limit: float | None, started: float, clock: Callable[[], float]) -> bool:
    return limit is not None and clock() - started > limit

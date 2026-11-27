"""The Evaluator sweep's public entry point."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable, Sequence

from ..agent_target import AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, ScoringPath
from ..evidence import Attempt, EvidenceError, ExecutionStatus, TaskEvidence, TaskOutcome
from ..scorecard import ExperimentRun, ScoredMetric, build_scorecard
from .declaration import Task


def run_sweep(
    *,
    tasks: Sequence[Task],
    agent_target: AgentTarget,
    metrics: Sequence[str],
    cancellation: Callable[[], bool] | None = None,
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
    evidence: list[TaskEvidence] = []
    cancelled = _Cancellation(cancellation)
    for task in tasks:
        experiment = _Experiment(task, agent_target, cancelled, clock)
        planned = [experiment.run_planned_attempt(number) for number in range(1, task.attempts + 1)]
        made = [attempt for executions in planned for attempt in executions]
        attempts.extend(made)
        experiments.append(experiment.run(planned))
        # A Task whose Attempts were not made is still a Task in the evidence.
        evidence.append(TaskEvidence(task.task_id, tuple(made)))
    scored = [
        ScoredMetric(
            result=evaluator.evaluate(evidence),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
            experiment_ids=tuple(experiment.experiment_id for experiment in experiments),
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


_RETRYABLE = (ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT)


class _Cancellation:
    """Whether the caller asked for the sweep to stop. Once asked, it stays asked."""

    def __init__(self, requested: Callable[[], bool] | None) -> None:
        self._requested = requested
        self._latched = False

    def requested(self) -> bool:
        if not self._latched and self._requested is not None and self._requested():
            self._latched = True
        return self._latched


class _Experiment:
    """The Attempts planned at one Task, made under the Task's execution limits."""

    def __init__(
        self, task: Task, agent_target: AgentTarget, cancellation: _Cancellation, clock: Callable[[], float]
    ) -> None:
        self._task = task
        self._agent_target = agent_target
        self._cancellation = cancellation
        self._clock = clock
        self._started: float | None = None
        # Why no further Attempt of this experiment is started, once that is so.
        self._stopped: ExecutionStatus | None = None

    def run_planned_attempt(self, number: int) -> list[Attempt]:
        """Make one planned Attempt and its retries. Every execution is returned, in order.

        Nothing is returned for a planned Attempt that may no longer start.
        """
        first = f"{self._task.task_id}/attempt-{number}"
        executions: list[Attempt] = []
        for retry in range(self._task.limits.max_retries + 1):
            if executions and executions[-1].execution_status not in _RETRYABLE:
                break
            if not self._may_start():
                break
            retry_of = None
            if executions:
                # The Attempt stays in the evidence with why it did not complete. The retry is another Attempt.
                executions[-1] = replace(executions[-1], execution_status=ExecutionStatus.RETRIED)
                retry_of = executions[-1].attempt_id
            executions.append(self._run_attempt(first if retry == 0 else f"{first}/retry-{retry}", retry_of))
        return executions

    def _may_start(self) -> bool:
        if self._stopped is not None:
            return False
        if self._cancellation.requested():
            self._stopped = ExecutionStatus.CANCELLED
            return False
        now = self._clock()
        if self._started is None:
            self._started = now
        limit = self._task.limits.experiment_timeout_seconds
        if limit is not None and now - self._started >= limit:
            self._stopped = ExecutionStatus.TIMED_OUT
            return False
        return True

    def _run_attempt(self, attempt_id: str, retry_of: str | None) -> Attempt:
        task = self._task
        limit = task.limits.attempt_timeout_seconds
        request = AttemptRequest(attempt_id=attempt_id, task_id=task.task_id, prompt=task.prompt, timeout_seconds=limit)

        def not_completed(status: ExecutionStatus, why: str) -> Attempt:
            return Attempt(
                attempt_id=attempt_id,
                task_id=task.task_id,
                execution_status=status,
                execution_error=why,
                retry_of=retry_of,
            )

        def over_the_limit() -> bool:
            return limit is not None and self._clock() - started > limit

        started = self._clock()
        try:
            response = self._agent_target.invoke(request)
        except Exception as error:
            why = f"{type(error).__name__}: {error}"
            if isinstance(error, TimeoutError):
                return not_completed(ExecutionStatus.TIMED_OUT, why)
            if over_the_limit():
                return not_completed(
                    ExecutionStatus.TIMED_OUT, f"{why}, after the Attempt's time limit of {limit:g} seconds"
                )
            if self._cancellation.requested():
                return not_completed(ExecutionStatus.CANCELLED, f"{why}, after the sweep was cancelled")
            return not_completed(ExecutionStatus.FAILED, why)
        if over_the_limit():
            # The limit applies to every Attempt alike, so a late answer is not scored.
            return not_completed(
                ExecutionStatus.TIMED_OUT,
                f"the agent target answered after the Attempt's time limit of {limit:g} seconds",
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

    def run(self, planned: Sequence[Sequence[Attempt]]) -> ExperimentRun:
        """Sum up the experiment from its planned Attempts, each given as its executions in order."""
        task = self._task
        not_made = sum(1 for executions in planned if not executions)
        not_completed = tuple(
            executions[-1].attempt_id
            for executions in planned
            if executions and executions[-1].execution_status is not ExecutionStatus.COMPLETED
        )
        missing = []
        if not_made:
            missing.append(f"{not_made} of {task.attempts} planned Attempts were not made")
        if not_completed:
            missing.append(f"{len(not_completed)} of {task.attempts} planned Attempts did not complete")
        status, reason = ExecutionStatus.COMPLETED, None
        if missing:
            status, reason = ExecutionStatus.FAILED, "; ".join(missing)
            if self._stopped is ExecutionStatus.TIMED_OUT:
                status = ExecutionStatus.TIMED_OUT
                limit = task.limits.experiment_timeout_seconds
                reason = f"its time limit of {limit:g} seconds was reached: {reason}"
            elif self._stopped is ExecutionStatus.CANCELLED or any(
                executions and executions[-1].execution_status is ExecutionStatus.CANCELLED for executions in planned
            ):
                status, reason = ExecutionStatus.CANCELLED, f"the sweep was cancelled: {reason}"
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
            not_made=not_made,
            retries=sum(max(len(executions) - 1, 0) for executions in planned),
        )

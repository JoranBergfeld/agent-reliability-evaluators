"""Execution limits at work: whether a further Attempt may start, and what became of the Attempts planned."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from ..evidence import Attempt, ConditionRole, ExecutionStatus, ExperimentStatus, TaskEvidence, VariationKind
from ..scorecard import ExecutionRecord
from .declaration import Experiment, Task

# An Attempt whose invocation of the agent ended this way is made again while it has retries left.
RETRYABLE = (ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT)

CANCELLED = "the sweep was cancelled"


def execution_identity(planned_attempt_id: str, retry: int) -> str:
    """The identity of an execution of a planned Attempt: its own for the first, and for a retry also which retry it is."""
    return planned_attempt_id if retry == 0 else f"{planned_attempt_id}/retry-{retry}"


def has_a_retry_left(attempt: Attempt, task: Task, retry: int) -> bool:
    """Whether an Attempt that ended as this one did is made again under the limits of its Task.

    `retry` says which retry the Attempt is, 0 for the first execution of a planned Attempt.
    """
    return attempt.execution_status in RETRYABLE and retry < task.limits.max_retries


def group_under(
    task_ids: Iterable[str], attempts: Iterable[Attempt], task_of: Callable[[Attempt], str] | None = None
) -> tuple[TaskEvidence, ...]:
    """The Attempts grouped under the Tasks that are named, each Task once, in the order they are named.

    A Task that Attempts were planned for is a Task of the evidence also when none of them
    was made. Every Attempt is one of a Task that is named: the Task it records, or the one
    `task_of` gives for it.
    """
    by_task: dict[str, list[Attempt]] = {task_id: [] for task_id in task_ids}
    for attempt in attempts:
        by_task[attempt.task_id if task_of is None else task_of(attempt)].append(attempt)
    return tuple(TaskEvidence(task_id, tuple(members)) for task_id, members in by_task.items())


class Cancellation:
    """Whether the caller asked for the sweep to stop. Once asked, it stays asked."""

    def __init__(self, requested: Callable[[], bool] | None) -> None:
        self._requested = requested
        self._latched = False
        self._lock = threading.Lock()

    def requested(self) -> bool:
        with self._lock:
            if not self._latched and self._requested is not None and self._requested():
                self._latched = True
            return self._latched


class AttemptGate:
    """Whether a further Attempt or retry of one Task may start, under its execution limits.

    It may not once the sweep was cancelled or the Task's total time limit was reached.
    `stopped` and `reason` then say which, and stay as they are.
    """

    def __init__(self, task: Task, cancellation: Cancellation, clock: Callable[[], float]) -> None:
        self._limit = task.limits.total_timeout_seconds
        self._cancellation = cancellation
        self._clock = clock
        self._lock = threading.Lock()
        self._started: float | None = None
        self.stopped: ExperimentStatus | None = None
        self.reason: str | None = None

    def may_start(self) -> bool:
        with self._lock:
            if self.stopped is not None:
                return False
            if self._cancellation.requested():
                self.stopped, self.reason = ExperimentStatus.CANCELLED, CANCELLED
                return False
            if self._limit is not None:
                now = self._clock()
                if self._started is None:
                    self._started = now
                elif now - self._started >= self._limit:
                    self.stopped = ExperimentStatus.TIMED_OUT
                    self.reason = f"the time limit of {self._limit:g} seconds for all its Attempts was reached"
                    return False
            return True


@dataclass(frozen=True)
class Execution:
    """What became of the Attempts planned for an experiment in one set."""

    record: ExecutionRecord
    task: Task
    experiment: Experiment
    # The last execution of each planned Attempt that was made.
    last: tuple[Attempt, ...]

    def conditions_for(self, kind: VariationKind | None) -> tuple[ConditionRole, ...]:
        """The conditions of this Task's Attempts that an Evaluator is calculated from.

        `kind` is the kind of variation the Evaluator compares with the baseline, or None
        for one calculated from the baseline only. An Evaluator that compares a kind of
        variation is calculated from both conditions of experiments whose variation is
        of that kind, and from nothing else.
        """
        if kind is None:
            return (ConditionRole.BASELINE,)
        variation = self.experiment.variation
        if variation is not None and variation.kind is kind:
            return (ConditionRole.BASELINE, ConditionRole.VARIATION)
        return ()

    def cut_short_for(self, kind: VariationKind | None) -> bool:
        """Whether the Attempts such an Evaluator is calculated from were cut short.

        They were when not all of them were made, or not one of them completed.
        """
        roles = self.conditions_for(kind)
        made = [attempt for attempt in self.last if attempt.condition_role in roles]
        return bool(roles) and (
            len(made) < sum(self.experiment.attempts_planned(role) for role in roles)
            or not any(attempt.execution_status is ExecutionStatus.COMPLETED for attempt in made)
        )


def execution_of(
    task: Task,
    made: Sequence[Sequence[Attempt]],
    gate: AttemptGate,
    *,
    experiment: Experiment,
    stop_reason: str | None = None,
    attempt_set: int = 1,
) -> Execution:
    """What became of the Attempts planned for a Task in its experiment.

    `made` has one entry for each planned Attempt that was made: its executions in order,
    the Attempt first and then its retries. `stop_reason` is why a customer control
    stopped the experiment, if one did.
    """
    planned = sum(experiment.attempts_planned(role) for role in ConditionRole)
    last = tuple(executions[-1] for executions in made)
    not_made = planned - len(last)
    if gate.stopped is not None:
        status, reason = gate.stopped, gate.reason
    elif any(attempt.execution_status is ExecutionStatus.CANCELLED for attempt in last):
        status, reason = ExperimentStatus.CANCELLED, CANCELLED
    elif not_made:
        status, reason = ExperimentStatus.STOPPED, stop_reason
    else:
        status, reason = ExperimentStatus.COMPLETED, None
    record = ExecutionRecord(
        task_id=task.task_id,
        experiment_id=experiment.experiment_id,
        declaration=experiment.to_dict(),
        attempt_set=attempt_set,
        limits=task.limits.to_dict(),
        status=status,
        reason=reason,
        planned=planned,
        recorded=sum(len(executions) for executions in made),
        not_completed=tuple(
            attempt.attempt_id for attempt in last if attempt.execution_status is not ExecutionStatus.COMPLETED
        ),
        not_made=not_made,
        retries=sum(len(executions) - 1 for executions in made),
    )
    return Execution(record, task, experiment, last)

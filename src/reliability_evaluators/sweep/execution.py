"""Execution limits at work: whether a further Attempt may start, and what became of the Attempts planned."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Sequence

from ..evidence import Attempt, ConditionRole, ExecutionStatus, ExperimentStatus, VariationKind
from ..scorecard import ExecutionRecord
from .declaration import Experiment, Task

# An Attempt whose invocation of the agent ended this way is made again while it has retries left.
RETRYABLE = (ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT)

CANCELLED = "the sweep was cancelled"


class Cancellation:
    """Whether the caller asked for the sweep to stop. Once asked, it stays asked."""

    def __init__(self, requested: Callable[[], bool] | None) -> None:
        self._requested = requested
        self._latched = False

    def requested(self) -> bool:
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
        # The Attempts of a Task given without an experiment may run on several threads.
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
    """What became of the Attempts planned for a Task in one set of Attempts, in its experiment or without one."""

    record: ExecutionRecord
    task: Task
    experiment: Experiment | None
    # The last execution of each planned Attempt that was made.
    last: tuple[Attempt, ...]

    def conditions_for(self, kind: VariationKind | None) -> tuple[ConditionRole, ...]:
        """The conditions of this Task's Attempts that an Evaluator is calculated from.

        `kind` is the kind of variation the Evaluator compares with the baseline, or None
        for one calculated from the baseline only. An Attempt at a Task given without an
        experiment had no variation applied to it, so it counts as a baseline Attempt. An
        Evaluator that compares a kind of variation is calculated from both conditions of
        the experiments whose variation is of that kind, and from nothing else.
        """
        if kind is None:
            return (ConditionRole.BASELINE,)
        variation = None if self.experiment is None else self.experiment.variation
        if variation is not None and variation.kind is kind:
            return (ConditionRole.BASELINE, ConditionRole.VARIATION)
        return ()

    def cut_short_for(self, kind: VariationKind | None) -> bool:
        """Whether the Attempts such an Evaluator is calculated from were cut short.

        They were when not all of them were made, or not one of them completed.
        """
        roles = self.conditions_for(kind)
        made = [attempt for attempt in self.last if (attempt.condition_role or ConditionRole.BASELINE) in roles]
        return bool(roles) and (
            len(made) < sum(_planned_under(self.task, self.experiment, role) for role in roles)
            or not any(attempt.execution_status is ExecutionStatus.COMPLETED for attempt in made)
        )


def _planned_under(task: Task, experiment: Experiment | None, role: ConditionRole) -> int:
    """How many Attempts are planned for a Task under a condition. Without an experiment, all under the baseline."""
    return task.attempts if experiment is None else experiment.attempts_planned(role)


def execution_of(
    task: Task,
    made: Sequence[Sequence[Attempt]],
    gate: AttemptGate,
    *,
    experiment: Experiment | None = None,
    stop_reason: str | None = None,
    attempt_set: int = 1,
) -> Execution:
    """What became of the Attempts planned for a Task, in its experiment or without one.

    `made` has one entry for each planned Attempt that was made: its executions in order,
    the Attempt first and then its retries. `stop_reason` is why a customer control
    stopped the experiment, if one did.
    """
    roles = tuple(ConditionRole) if experiment is not None else (ConditionRole.BASELINE,)
    planned = sum(_planned_under(task, experiment, role) for role in roles)
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
        experiment_id=None if experiment is None else experiment.experiment_id,
        declaration={} if experiment is None else experiment.to_dict(),
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

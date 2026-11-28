"""Grouping of Attempts into the evidence an Evaluator is called with."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .attempt import Attempt, EvidenceError
from .experiment import ConditionRole

# Why an Attempt under a variation is left out of a metric of nominal behaviour.
NOT_UNDER_THE_BASELINE = "made under a variation, and this metric is calculated from Attempts under the baseline"


@dataclass(frozen=True)
class TaskEvidence:
    """The Attempts made at one Task. A Task with no Attempts is still a Task."""

    task_id: str
    attempts: tuple[Attempt, ...]


def group_by_task(attempts: Iterable[Attempt]) -> tuple[TaskEvidence, ...]:
    """Group Attempts by Task, keeping the order in which Tasks and Attempts first appear."""
    grouped: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        grouped.setdefault(attempt.task_id, []).append(attempt)
    return tuple(TaskEvidence(task_id, tuple(members)) for task_id, members in grouped.items())


def under_the_baseline(tasks: Sequence[TaskEvidence]) -> tuple[tuple[TaskEvidence, ...], tuple[str, ...]]:
    """The evidence with its Attempts under the baseline only, and the identities of the Attempts left out.

    A metric of nominal behaviour is calculated from these. An Attempt that records a
    variation as its condition is left out, in whatever evidence the Evaluator is given. An
    Attempt that records no condition was made without an experiment, so no variation was
    applied to it and it is used. A Task keeps its place when none of its Attempts is used.
    """
    nominal = tuple(
        TaskEvidence(
            task.task_id,
            tuple(attempt for attempt in task.attempts if attempt.condition_role is not ConditionRole.VARIATION),
        )
        for task in tasks
    )
    left_out = tuple(
        attempt.attempt_id
        for task in tasks
        for attempt in task.attempts
        if attempt.condition_role is ConditionRole.VARIATION
    )
    return nominal, left_out


def require_unambiguous(tasks: Sequence[TaskEvidence]) -> None:
    """Reject grouped evidence in which an Attempt or a Task cannot be told apart from another.

    Evidence is matched by Attempt identity, so a repeated identity is an error and not
    something to resolve by order.
    """
    seen_tasks: set[str] = set()
    seen_attempts: set[str] = set()
    for task in tasks:
        if task.task_id in seen_tasks:
            raise EvidenceError(f"Task {task.task_id!r} appears in more than one group")
        seen_tasks.add(task.task_id)
        for attempt in task.attempts:
            if attempt.task_id != task.task_id:
                raise EvidenceError(
                    f"Attempt {attempt.attempt_id!r} belongs to Task {attempt.task_id!r} "
                    f"but is grouped under Task {task.task_id!r}"
                )
            if attempt.attempt_id in seen_attempts:
                raise EvidenceError(f"more than one Attempt has the identity {attempt.attempt_id!r}")
            seen_attempts.add(attempt.attempt_id)

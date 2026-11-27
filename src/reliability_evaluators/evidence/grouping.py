"""Grouping of Attempts into the evidence an Evaluator is called with."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .attempt import Attempt, EvidenceError


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

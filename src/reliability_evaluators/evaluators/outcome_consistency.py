"""Outcome consistency: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from math import fsum
from typing import Iterable

from ..evidence import ExecutionStatus, TaskEvidence, require_unambiguous
from .catalogue import OUTCOME_CONSISTENCY
from .result import EvidenceCount, MetricDefinition, MetricResult, Scale, counted

MINIMUM_ATTEMPTS = 2
TOO_FEW_ATTEMPTS = "fewer than two Attempts with a task outcome"

DEFINITION = MetricDefinition(
    metric=OUTCOME_CONSISTENCY,
    version="1.0.0",
    measures="Whether repeated Attempts at the same Task give the same task outcome.",
    calculation=(
        "Per Task, p is the share of its Attempts that succeeded and the Task value is (2p - 1)^2. "
        "The metric is the mean of the Task values, each eligible Task weighted equally."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: (2p - 1)^2, which is 1 - 4p(1 - p). The companion code's normalization, "
        "which divides the sample variance by each Task's own p(1 - p), is not used.",
        "Tasks are weighted equally. Attempts are not pooled across Tasks.",
        "Assumption (spec assumption 11): a Task needs at least two Attempts with a task outcome. "
        "A Task with fewer is excluded with a reason, because the formula would return 1 with no "
        "repeatability evidence.",
        "Assumption (spec assumption 10): the metric is calculated over the eligible Tasks and "
        "excluded Tasks are counted with reasons. It is unavailable only when no Task is eligible.",
    ),
    scale=Scale(
        minimum=0.0,
        maximum=1.0,
        description=(
            "1: every eligible Task gave the same task outcome on all its Attempts. "
            "0: every eligible Task succeeded on exactly half its Attempts."
        ),
    ),
    interpretation=(
        "Consistent is not successful. An agent that fails every Attempt scores 1, the same as one "
        "that succeeds every Attempt. Read this metric next to the task outcomes, not instead of them."
    ),
)


def evaluate(tasks: Iterable[TaskEvidence]) -> MetricResult:
    """Calculate outcome consistency from Attempts grouped by Task."""
    tasks = tuple(tasks)
    require_unambiguous(tasks)
    per_task = []
    used_attempts: list[str] = []
    excluded_tasks: list[str] = []
    attempts_of_excluded_tasks: list[str] = []
    not_completed: list[str] = []
    without_outcome: list[str] = []
    for task in tasks:
        scored = []
        for attempt in task.attempts:
            if attempt.execution_status is not ExecutionStatus.COMPLETED:
                not_completed.append(attempt.attempt_id)
            elif attempt.task_outcome is None:
                without_outcome.append(attempt.attempt_id)
            else:
                scored.append(attempt)
        if len(scored) < MINIMUM_ATTEMPTS:
            excluded_tasks.append(task.task_id)
            attempts_of_excluded_tasks.extend(attempt.attempt_id for attempt in scored)
            continue
        attempts = len(scored)
        successes = sum(attempt.task_outcome.success for attempt in scored)
        per_task.append(
            {
                "task_id": task.task_id,
                "attempts": attempts,
                "successes": successes,
                # (2p - 1)^2 with p = successes / attempts, kept in integers until one division.
                "value": (2 * successes - attempts) ** 2 / attempts**2,
            }
        )
        used_attempts.extend(attempt.attempt_id for attempt in scored)

    evidence = {
        "counts": {
            "tasks": EvidenceCount(
                total=len(tasks),
                valid=len(per_task),
                excluded=counted(TOO_FEW_ATTEMPTS, excluded_tasks),
            ),
            "attempts": EvidenceCount(
                total=sum(len(task.attempts) for task in tasks),
                valid=len(used_attempts),
                missing=counted("no task outcome recorded", without_outcome),
                excluded=counted("execution status is not completed", not_completed)
                + counted(f"its Task has {TOO_FEW_ATTEMPTS}", attempts_of_excluded_tasks),
            ),
        },
        "details": {"per_task": per_task},
        "attempt_ids": tuple(used_attempts),
    }
    if not tasks:
        return MetricResult.unavailable(DEFINITION, "no Tasks in the evidence", **evidence)
    if not per_task:
        return MetricResult.unavailable(
            DEFINITION, "no Task has at least two Attempts with a task outcome", **evidence
        )
    value = fsum(entry["value"] for entry in per_task) / len(per_task)
    return MetricResult.available(DEFINITION, value, **evidence)


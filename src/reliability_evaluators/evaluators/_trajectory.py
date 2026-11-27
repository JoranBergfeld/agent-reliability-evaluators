"""What the two trajectory consistency Evaluators share.

Both compare every pair of Attempts at a Task, take one minus the mean pair difference as
the Task value, and average the Task values with equal weights. They differ in the pair
difference and in when a Task cannot be calculated; each Evaluator's own module holds those.

Pure: standard library only, no I/O.
"""

from __future__ import annotations

from fractions import Fraction
from itertools import combinations
from typing import Callable, Iterable, Sequence

from ..evidence import ActionTypeMapping, ExecutionStatus, TaskEvidence, require_unambiguous
from .result import EvidenceCount, MetricDefinition, MetricResult, counted

ActionTypes = Sequence[str]

MINIMUM_ATTEMPTS = 2
TOO_FEW_ATTEMPTS = "fewer than two Attempts with a usable action recording"


def evaluate_attempt_pairs(
    definition: MetricDefinition,
    tasks: Iterable[TaskEvidence],
    action_types: ActionTypeMapping,
    *,
    pair_difference: Callable[[ActionTypes, ActionTypes], Fraction | float],
    mean_difference_name: str,
    no_eligible_task: str,
    undefined_task: Callable[[Sequence[ActionTypes]], str | None] = lambda sequences: None,
) -> MetricResult:
    """Score a pairwise trajectory metric.

    `pair_difference` is between 0, for two Attempts that agree, and 1. `undefined_task`
    returns why a Task with enough Attempts still cannot be calculated, or None.
    """
    if not isinstance(action_types, ActionTypeMapping):
        raise ValueError(
            "the trajectory metrics need a declared ActionTypeMapping: the evidence source and the "
            f"action type of each tool name have no default. Got {action_types!r}"
        )
    tasks = tuple(tasks)
    require_unambiguous(tasks)
    declared = action_types.action_types
    per_task = []
    task_values: list[Fraction] = []
    used_attempts: list[str] = []
    excluded_tasks: dict[str, list[str]] = {}
    attempts_of_excluded_tasks: dict[str, list[str]] = {}
    not_completed: list[str] = []
    not_recorded: list[str] = []
    other_source: list[str] = []
    without_action_type: list[str] = []
    undeclared_tool_names: set[str] = set()
    for task in tasks:
        sequences: dict[str, ActionTypes] = {}
        for attempt in task.attempts:
            recording = attempt.action_evidence
            if attempt.execution_status is not ExecutionStatus.COMPLETED:
                not_completed.append(attempt.attempt_id)
            elif recording is None:
                not_recorded.append(attempt.attempt_id)
            elif recording.source != action_types.evidence_source:
                other_source.append(attempt.attempt_id)
            elif undeclared := {event.tool_name for event in recording.events} - declared.keys():
                without_action_type.append(attempt.attempt_id)
                undeclared_tool_names |= undeclared
            else:
                sequences[attempt.attempt_id] = tuple(declared[event.tool_name] for event in recording.events)
        reason = (
            TOO_FEW_ATTEMPTS if len(sequences) < MINIMUM_ATTEMPTS else undefined_task(tuple(sequences.values()))
        )
        if reason:
            excluded_tasks.setdefault(reason, []).append(task.task_id)
            attempts_of_excluded_tasks.setdefault(reason, []).extend(sequences)
            continue
        differences = [pair_difference(first, second) for first, second in combinations(sequences.values(), 2)]
        value = 1 - _exact_mean(differences)
        per_task.append(
            {
                "task_id": task.task_id,
                "attempts": len(sequences),
                "pairs": len(differences),
                mean_difference_name: float(1 - value),
                "value": float(value),
            }
        )
        task_values.append(value)
        used_attempts.extend(sequences)

    pairs = sum(entry["pairs"] for entry in per_task)
    evidence = {
        "counts": {
            "tasks": EvidenceCount(
                total=len(tasks),
                valid=len(per_task),
                excluded=tuple(
                    entry for reason, task_ids in excluded_tasks.items() for entry in counted(reason, task_ids)
                ),
            ),
            "attempts": EvidenceCount(
                total=sum(len(task.attempts) for task in tasks),
                valid=len(used_attempts),
                missing=counted("no action recording", not_recorded),
                excluded=counted("execution status is not completed", not_completed)
                + counted("its action recording is not from the declared evidence source", other_source)
                + counted("an action event has no declared action type", without_action_type)
                + tuple(
                    entry
                    for reason, attempt_ids in attempts_of_excluded_tasks.items()
                    for entry in counted(f"its Task has {reason}", attempt_ids)
                ),
            ),
            # Pairs are formed within eligible Tasks only, so every pair counted was compared.
            "attempt_pairs": EvidenceCount(total=pairs, valid=pairs),
        },
        "details": {
            "evidence_source": action_types.evidence_source,
            "action_type_mapping": dict(declared),
            "tool_names_without_action_type": sorted(undeclared_tool_names),
            "per_task": per_task,
        },
        "attempt_ids": tuple(used_attempts),
    }
    if not tasks:
        return MetricResult.unavailable(definition, "no Tasks in the evidence", **evidence)
    if not per_task:
        return MetricResult.unavailable(definition, no_eligible_task, **evidence)
    return MetricResult.available(definition, float(_exact_mean(task_values)), **evidence)


def _exact_mean(values: Sequence[Fraction | float]) -> Fraction:
    """The mean as an exact rational, so that the only rounding is the final conversion to a float."""
    return sum((Fraction(value) for value in values), Fraction(0)) / len(values)

"""What the robustness Evaluators share: the capped ratio of two task-balanced accuracies.

Each compares the Attempts made under the baseline with the Attempts made under one kind
of variation. An Evaluator's own module holds its definition and names its kind.

Pure: standard library only, no I/O.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Iterable, Sequence

from ..evidence import Attempt, ConditionRole, ExecutionStatus, TaskEvidence, VariationKind, require_unambiguous
from .result import CountedReason, EvidenceCount, MetricDefinition, MetricResult, counted

BASELINE = "baseline"
NOT_COMPLETED = "execution status is not completed"
CONDITION_DIFFERS = "the observed condition differs from the intended condition"
NOT_VERIFIED = "no observed condition shows that the intended condition occurred"
NO_OUTCOME = "no task outcome recorded"
# In the order they are reported in.
EXCLUSIONS = (NOT_COMPLETED, CONDITION_DIFFERS, NOT_VERIFIED)


def evaluate_against_baseline(
    definition: MetricDefinition, kind: VariationKind, tasks: Iterable[TaskEvidence]
) -> MetricResult:
    """Score `min(accuracy under the variation / baseline accuracy, 1)` for variations of one kind.

    The accuracy under a condition is the mean, over the Tasks with a valid Attempt under
    both conditions, of the share of the Task's valid Attempts under that condition that
    succeeded. The result names the variation by its kind, in its counts and its details.
    """
    tasks = tuple(tasks)
    require_unambiguous(tasks)
    variation = kind.value
    conditions = (BASELINE, variation)
    under = {BASELINE: "under the baseline", variation: f"under the {variation} variation"}
    intended: dict[str, list[str]] = {condition: [] for condition in conditions}
    # Per condition, the valid Attempts of each Task that has any.
    valid: dict[str, dict[str, list[Attempt]]] = {condition: {} for condition in conditions}
    # Per condition, the Attempts that are not valid, by the reason they are left out for.
    left_out: dict[str, dict[str, list[str]]] = {condition: {} for condition in conditions}
    other: list[str] = []
    for task in tasks:
        for attempt in task.attempts:
            condition = _condition(attempt, kind)
            if condition is None:
                other.append(attempt.attempt_id)
                continue
            intended[condition].append(attempt.attempt_id)
            reason = _why_not_valid(attempt)
            if reason is None:
                valid[condition].setdefault(task.task_id, []).append(attempt)
            else:
                left_out[condition].setdefault(reason, []).append(attempt.attempt_id)

    compared = [task.task_id for task in tasks if all(task.task_id in valid[condition] for condition in conditions)]
    per_task = [
        {"task_id": task_id, **{condition: _task_summary(valid[condition][task_id]) for condition in conditions}}
        for task_id in compared
    ]
    # Exact until the one conversion to a float: the mean over the compared Tasks of successes / Attempts.
    accuracy = {
        condition: sum(Fraction(entry[condition]["successes"], entry[condition]["attempts"]) for entry in per_task)
        / len(per_task)
        if per_task
        else None
        for condition in conditions
    }
    ratio = accuracy[variation] / accuracy[BASELINE] if accuracy[BASELINE] else None
    used = [attempt for task_id in compared for condition in conditions for attempt in valid[condition][task_id]]

    def attempt_count(condition: str, other_condition: str) -> EvidenceCount:
        unmatched = [
            attempt.attempt_id
            for task_id, attempts in valid[condition].items()
            if task_id not in valid[other_condition]
            for attempt in attempts
        ]
        return EvidenceCount(
            total=len(intended[condition]),
            valid=sum(entry[condition]["attempts"] for entry in per_task),
            missing=counted(NO_OUTCOME, left_out[condition].get(NO_OUTCOME, ())),
            excluded=_counted_in_order(left_out[condition], EXCLUSIONS)
            + counted(f"its Task has no valid Attempt {under[other_condition]}", unmatched),
        )

    evidence = {
        "counts": {
            "tasks": EvidenceCount(
                total=len(tasks),
                valid=len(compared),
                excluded=_tasks_not_compared(tasks, valid[BASELINE], valid[variation], under),
            ),
            f"{BASELINE}_attempts": attempt_count(BASELINE, variation),
            f"{variation}_attempts": attempt_count(variation, BASELINE),
            "other_attempts": EvidenceCount(
                total=len(other),
                valid=0,
                excluded=counted(f"not made under the baseline or under a {variation} variation", other),
            ),
        },
        "details": {
            "variation_kind": variation,
            **{
                condition: {
                    **_recorded_conditions([valid[condition][task_id] for task_id in compared]),
                    "tasks_with_a_valid_attempt": len(valid[condition]),
                    "compared_tasks": len(per_task),
                    "compared_attempts": sum(entry[condition]["attempts"] for entry in per_task),
                    "successes": sum(entry[condition]["successes"] for entry in per_task),
                    "accuracy": None if accuracy[condition] is None else float(accuracy[condition]),
                }
                for condition in conditions
            },
            "accuracy_ratio": None if ratio is None else float(ratio),
            "capped": None if ratio is None else ratio > 1,
            "per_task": per_task,
        },
        "attempt_ids": tuple(attempt.attempt_id for attempt in used),
    }
    without_valid_attempts = [condition for condition in conditions if not valid[condition]]
    if len(without_valid_attempts) == 2:
        reason = f"no valid Attempt {under[BASELINE]} and none {under[variation]}"
    elif without_valid_attempts:
        reason = f"no valid Attempt {under[without_valid_attempts[0]]}"
    elif not compared:
        reason = f"no Task has a valid Attempt under both the baseline and the {variation} variation"
    elif ratio is None:
        reason = "the baseline accuracy is zero, so the ratio is undefined"
    else:
        return MetricResult.available(definition, float(min(ratio, 1)), **evidence)
    return MetricResult.unavailable(definition, reason, **evidence)


def _condition(attempt: Attempt, kind: VariationKind) -> str | None:
    """The compared condition an Attempt was intended for, or None when it was intended for neither."""
    if attempt.condition_role is ConditionRole.BASELINE:
        return BASELINE
    if attempt.condition_role is ConditionRole.VARIATION and attempt.intended_condition.kind is kind:
        return kind.value
    return None


def _why_not_valid(attempt: Attempt) -> str | None:
    """Why an Attempt does not count under the condition it was intended for, or None when it does.

    It counts when it completed, an observed condition shows that the intended condition
    occurred, and it has a task outcome. The intended condition alone is a label.
    """
    status = attempt.execution_status
    if status not in (ExecutionStatus.COMPLETED, ExecutionStatus.CONDITION_NOT_MET):
        return NOT_COMPLETED
    if attempt.observed_condition is None:
        return NOT_VERIFIED
    if not attempt.observed_condition.met or status is ExecutionStatus.CONDITION_NOT_MET:
        return CONDITION_DIFFERS
    if attempt.task_outcome is None:
        return NO_OUTCOME
    return None


def _counted_in_order(ids_by_reason: dict[str, list[str]], reasons: Sequence[str]) -> tuple[CountedReason, ...]:
    return tuple(entry for reason in reasons for entry in counted(reason, ids_by_reason.get(reason, ())))


def _tasks_not_compared(
    tasks: Sequence[TaskEvidence], in_baseline: dict[str, Any], in_variation: dict[str, Any], under: dict[str, str]
) -> tuple[CountedReason, ...]:
    """The Tasks that lack a valid Attempt under a condition, by the condition they lack it under."""
    baseline, variation = under.values()
    lacking: dict[str, list[str]] = {}
    for task in tasks:
        if task.task_id in in_baseline and task.task_id in in_variation:
            continue
        if task.task_id in in_variation:
            reason = f"no valid Attempt {baseline}"
        elif task.task_id in in_baseline:
            reason = f"no valid Attempt {variation}"
        else:
            reason = "no valid Attempt under either condition"
        lacking.setdefault(reason, []).append(task.task_id)
    reasons = (f"no valid Attempt {baseline}", f"no valid Attempt {variation}", "no valid Attempt under either condition")
    return _counted_in_order(lacking, reasons)


def _task_summary(attempts: Sequence[Attempt]) -> dict[str, Any]:
    successes = sum(attempt.task_outcome.success for attempt in attempts)
    return {"attempts": len(attempts), "successes": successes, "success_frequency": successes / len(attempts)}


def _recorded_conditions(attempts_by_task: Iterable[Sequence[Attempt]]) -> dict[str, list[str]]:
    """The intended conditions of the Attempts and what their occurrence was observed from, each named once."""
    attempts = [attempt for attempts in attempts_by_task for attempt in attempts]
    return {
        "condition_ids": list(dict.fromkeys(attempt.intended_condition.condition_id for attempt in attempts)),
        "observed_from": list(dict.fromkeys(attempt.observed_condition.source for attempt in attempts)),
    }

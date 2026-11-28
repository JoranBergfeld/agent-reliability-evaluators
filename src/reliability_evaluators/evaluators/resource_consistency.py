"""Resource consistency: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from math import exp, frexp, fsum, ldexp, sqrt
from typing import Any, Iterable, Sequence

from ..evidence import (
    NOT_UNDER_THE_BASELINE,
    Attempt,
    ExecutionStatus,
    ResourceType,
    TaskEvidence,
    require_unambiguous,
    under_the_baseline,
)
from .catalogue import RESOURCE_CONSISTENCY
from .result import EvidenceCount, MetricDefinition, MetricResult, Scale, counted

DEFINITION = MetricDefinition(
    metric=RESOURCE_CONSISTENCY,
    version="2.0.0",
    measures="Whether repeated Attempts at the same Task use similar resources.",
    calculation=(
        "Per Task and resource type, the coefficient of variation (CV) is the population standard "
        "deviation of the Attempts' measurements divided by their mean. The Task value is "
        "exp(-mean of the CVs) over the resource types used for that Task. The metric is the mean of "
        "the Task values, each eligible Task weighted equally."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: CV = sigma / mu per resource type, combined as exp(-mean of CVs). The table "
        "does not say which standard deviation. The population standard deviation of the observed "
        "Attempts is used. The companion code's sample standard deviation is not used.",
        "Tasks are weighted equally. Measurements are never pooled across Tasks, because different "
        "Tasks are different workloads.",
        "Successful and failed Attempts are both included: the task outcome plays no part. The paper's "
        "appendix, which conditions on successful outcomes, is not followed.",
        "A resource measured as zero in every Attempt of a Task has a CV of 0. A missing measurement is "
        "never read as zero. The companion code's removal of measurements that are not positive is not used.",
        "The resource types are elapsed time, tool-call count and token usage. Token usage is used only "
        "where every measurement is declared reliably sourced. A measurement of any resource type that "
        "is declared not reliably sourced keeps that resource type out for its Task.",
        "A measurement that is negative, not finite or not a number is rejected as invalid evidence. "
        "A mean of zero therefore only arises when every measurement is zero.",
        "Assumption (spec assumption 13): a resource type with a missing measurement in any Attempt of a "
        "Task is excluded for that Task and disclosed, rather than making the Task undefined. A Task is "
        "excluded only when no resource type is left for it.",
        "Assumption (spec assumption 10): the metric is calculated over the eligible Tasks and excluded "
        "Tasks are counted with reasons. It is unavailable only when no Task is eligible.",
        "Assumption (made by this definition): a resource type measured in more than one unit, or with "
        "more than one measurement boundary, across the Attempts of a Task is excluded for that Task, "
        "because its measurements are not comparable.",
        "Assumption (made by this definition): an Attempt whose execution status is not completed is "
        "excluded with whatever it measured, because that is not a measurement of a whole Attempt.",
        "Assumption (made by this definition): a Task needs at least two Attempts whose execution "
        "completed. With one, every CV is 0 and the Task would score 1 with no repeated Attempt behind it.",
        "Nominal behaviour: the metric is calculated from the Attempts made under the baseline. An Attempt "
        "that records a variation as its condition is excluded with a reason and counted, whoever calls "
        "the Evaluator. An Attempt that records no condition was made without an experiment and is used.",
    ),
    scale=Scale(
        minimum=0.0,
        maximum=1.0,
        description=(
            "1: in every eligible Task, each resource type used measured the same in all Attempts. "
            "Towards 0: the measurements vary more and more between Attempts of the same Task."
        ),
    ),
    interpretation=(
        "Stability is not efficiency. An agent that is slow or expensive on every Attempt scores 1, the "
        "same as one that is fast or cheap on every Attempt. Read this metric next to the measurements, "
        "not instead of them. Values are comparable only between results that used the same resource types."
    ),
)

MINIMUM_ATTEMPTS = 2
TOO_FEW_ATTEMPTS = "fewer than two Attempts whose execution completed"
NO_USABLE_RESOURCE_TYPE = "no resource type has complete and comparable measurements"
IN_TASK_WITHOUT_RESOURCE_TYPE = "its Task has no resource type with complete and comparable measurements"
# Why a resource type is not used for a Task.
NOT_MEASURED = "not measured in any Attempt of the Task"
MISSING = "a measurement is missing in at least one Attempt of the Task"
NOT_RELIABLY_SOURCED = "not reliably sourced in every Attempt of the Task"
MIXED_UNITS = "measured in more than one unit across the Attempts of the Task"
MIXED_BOUNDARIES = "measured with more than one measurement boundary across the Attempts of the Task"


def evaluate(tasks: Iterable[TaskEvidence]) -> MetricResult:
    """Calculate resource consistency from Attempts grouped by Task."""
    given = tuple(tasks)
    require_unambiguous(given)
    tasks, under_a_variation = under_the_baseline(given)
    per_task = []
    excluded_tasks = []
    used_attempts: list[str] = []
    not_completed: list[str] = []
    attempts_of_small_tasks: list[str] = []
    attempts_without_resource_type: list[str] = []
    # Per resource type: the Tasks it is used in, and the Tasks it is excluded in by reason.
    used_in: dict[ResourceType, list[str]] = {resource_type: [] for resource_type in ResourceType}
    excluded_in: dict[ResourceType, dict[str, list[str]]] = {resource_type: {} for resource_type in ResourceType}
    for task in tasks:
        completed = []
        for attempt in task.attempts:
            if attempt.execution_status is ExecutionStatus.COMPLETED:
                completed.append(attempt)
            else:
                not_completed.append(attempt.attempt_id)
        if len(completed) < MINIMUM_ATTEMPTS:
            excluded_tasks.append({"task_id": task.task_id, "reason": TOO_FEW_ATTEMPTS})
            attempts_of_small_tasks.extend(attempt.attempt_id for attempt in completed)
            continue
        used = []
        excluded = []
        for resource_type in ResourceType:
            entry = _resource_type_in_task(resource_type, completed)
            if "reason" in entry:
                excluded.append(entry)
                excluded_in[resource_type].setdefault(entry["reason"], []).append(task.task_id)
            else:
                used.append(entry)
                used_in[resource_type].append(task.task_id)
        if not used:
            excluded_tasks.append(
                {"task_id": task.task_id, "reason": NO_USABLE_RESOURCE_TYPE, "excluded_resource_types": excluded}
            )
            attempts_without_resource_type.extend(attempt.attempt_id for attempt in completed)
            continue
        per_task.append(
            {
                "task_id": task.task_id,
                "attempts": len(completed),
                "resource_types": used,
                "excluded_resource_types": excluded,
                "value": exp(-fsum(entry["coefficient_of_variation"] for entry in used) / len(used)),
            }
        )
        used_attempts.extend(attempt.attempt_id for attempt in completed)

    def tasks_excluded_for(reason: str) -> list[str]:
        return [task["task_id"] for task in excluded_tasks if task["reason"] == reason]

    evidence = {
        "counts": {
            "tasks": EvidenceCount(
                total=len(tasks),
                valid=len(per_task),
                excluded=counted(TOO_FEW_ATTEMPTS, tasks_excluded_for(TOO_FEW_ATTEMPTS))
                + counted(NO_USABLE_RESOURCE_TYPE, tasks_excluded_for(NO_USABLE_RESOURCE_TYPE)),
            ),
            "attempts": EvidenceCount(
                total=sum(len(task.attempts) for task in given),
                valid=len(used_attempts),
                excluded=counted(NOT_UNDER_THE_BASELINE, under_a_variation)
                + counted("execution status is not completed", not_completed)
                + counted(f"its Task has {TOO_FEW_ATTEMPTS}", attempts_of_small_tasks)
                + counted(IN_TASK_WITHOUT_RESOURCE_TYPE, attempts_without_resource_type),
            ),
        },
        "details": {
            "resource_types": [
                {
                    "resource_type": resource_type.value,
                    "used_in_tasks": used_in[resource_type],
                    "excluded_in_tasks": [
                        entry.to_dict()
                        for reason, task_ids in excluded_in[resource_type].items()
                        for entry in counted(reason, task_ids)
                    ],
                }
                for resource_type in ResourceType
            ],
            "per_task": per_task,
            "excluded_tasks": excluded_tasks,
        },
        "attempt_ids": tuple(used_attempts),
    }
    if not tasks:
        return MetricResult.unavailable(DEFINITION, "no Tasks in the evidence", **evidence)
    if len(tasks_excluded_for(TOO_FEW_ATTEMPTS)) == len(tasks):
        return MetricResult.unavailable(
            DEFINITION, "no Task has at least two Attempts whose execution completed", **evidence
        )
    if not per_task:
        return MetricResult.unavailable(DEFINITION, f"{NO_USABLE_RESOURCE_TYPE} for any Task", **evidence)
    value = fsum(entry["value"] for entry in per_task) / len(per_task)
    return MetricResult.available(DEFINITION, value, **evidence)


def _resource_type_in_task(resource_type: ResourceType, attempts: Sequence[Attempt]) -> dict[str, Any]:
    """The variation of one resource type over the Attempts of a Task, or why it is not used."""

    def excluded(reason: str, attempt_ids: Iterable[str] = ()) -> dict[str, Any]:
        return {"resource_type": resource_type.value, "reason": reason, "attempt_ids": list(attempt_ids)}

    measured = {
        attempt.attempt_id: measurement
        for attempt in attempts
        for measurement in attempt.resource_measurements
        if measurement.resource_type is resource_type
    }
    if not measured:
        return excluded(NOT_MEASURED)
    if len(measured) < len(attempts):
        return excluded(MISSING, (attempt.attempt_id for attempt in attempts if attempt.attempt_id not in measured))
    unreliable = [attempt_id for attempt_id, measurement in measured.items() if not measurement.reliably_sourced]
    if unreliable:
        return excluded(NOT_RELIABLY_SOURCED, unreliable)
    measurements = list(measured.values())
    if len({measurement.unit for measurement in measurements}) > 1:
        return excluded(MIXED_UNITS)
    if len({measurement.boundary for measurement in measurements}) > 1:
        return excluded(MIXED_BOUNDARIES)
    values = [measurement.value for measurement in measurements]
    mean, variation = _mean_and_coefficient_of_variation(values)
    return {
        "resource_type": resource_type.value,
        "unit": measurements[0].unit,
        "boundary": measurements[0].boundary,
        "mean": mean,
        "coefficient_of_variation": variation,
    }


def _mean_and_coefficient_of_variation(values: Sequence[float]) -> tuple[float, float]:
    """The mean of the values, and their population standard deviation divided by that mean."""
    largest = max(values)
    # Measurements are never negative, so this is a resource that is zero in every Attempt.
    if largest == 0:
        return 0.0, 0.0
    # Scaling by a power of two is exact, and keeps the sum and the squares in range whatever the unit.
    _, exponent = frexp(largest)
    scaled = [ldexp(value, -exponent) for value in values]
    mean = fsum(scaled) / len(scaled)
    variation = sqrt(fsum(((value - mean) / mean) ** 2 for value in scaled) / len(scaled))
    return ldexp(mean, exponent), variation

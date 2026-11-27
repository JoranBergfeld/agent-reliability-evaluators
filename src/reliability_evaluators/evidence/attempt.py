"""The Attempt record: one execution of an agent on a Task, and the evidence attached to it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any


class EvidenceError(ValueError):
    """Evidence that cannot be accepted as given. It is rejected, never repaired."""


class ExecutionStatus(str, Enum):
    """How the execution of an Attempt ended. This is not the task outcome."""

    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskOutcome:
    """The success or failure of an Attempt against the customer's task-success rule.

    `success` is binary: True or False, or the integers 1 or 0. `source` says what the
    outcome was determined from, such as a test or the state of a service.
    """

    success: bool
    source: str

    def __post_init__(self) -> None:
        if type(self.success) not in (bool, int) or self.success not in (0, 1):
            raise EvidenceError(
                f"a task outcome is binary: True or False, or 1 or 0. Got {self.success!r}"
            )
        object.__setattr__(self, "success", bool(self.success))
        if not isinstance(self.source, str) or not self.source.strip():
            raise EvidenceError("a task outcome records its source")

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "source": self.source}


class ResourceType(str, Enum):
    """The resources an Attempt's use of is measured."""

    ELAPSED_TIME = "elapsed_time"
    TOOL_CALLS = "tool_calls"
    TOKEN_USAGE = "token_usage"


COUNTED_RESOURCE_TYPES = (ResourceType.TOOL_CALLS, ResourceType.TOKEN_USAGE)


@dataclass(frozen=True, kw_only=True)
class ResourceMeasurement:
    """How much of one resource an Attempt used.

    `boundary` says what the number covers: where the measurement starts and stops, what
    it includes and who measured it. A measurement that was not made is left off the
    Attempt. It is never recorded as zero.
    """

    resource_type: ResourceType
    value: float
    unit: str
    boundary: str
    # Whether the provider vouches that the number covers the whole Attempt. Token usage is
    # not reliably sourced unless this says so; every other resource type is unless it says not.
    reliably_sourced: bool | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "resource_type", ResourceType(self.resource_type))
        except ValueError:
            raise EvidenceError(f"{self.resource_type!r} is not a resource type") from None
        value = self.value
        if not _is_finite_and_not_negative(value):
            raise EvidenceError(
                f"a resource measurement is a finite number that is not negative. Got {value!r} "
                f"for {self.resource_type.value}. A measurement that was not made is left off the Attempt"
            )
        if self.resource_type in COUNTED_RESOURCE_TYPES and type(value) is not int:
            raise EvidenceError(f"{self.resource_type.value} is a count, so a whole number. Got {value!r}")
        for name, described in (("unit", "unit"), ("boundary", "measurement boundary")):
            text = getattr(self, name)
            if not isinstance(text, str) or not text.strip():
                raise EvidenceError(
                    f"a resource measurement records its {described}: "
                    f"{name} is {text!r} for {self.resource_type.value}"
                )
        if self.reliably_sourced is None:
            object.__setattr__(self, "reliably_sourced", self.resource_type is not ResourceType.TOKEN_USAGE)
        elif type(self.reliably_sourced) is not bool:
            raise EvidenceError(f"reliably_sourced is True or False. Got {self.reliably_sourced!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type.value,
            "value": self.value,
            "unit": self.unit,
            "boundary": self.boundary,
            "reliably_sourced": self.reliably_sourced,
        }


def _is_finite_and_not_negative(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return isfinite(value) and value >= 0
    except OverflowError:
        # An integer too large to be a float.
        return False


@dataclass(frozen=True, kw_only=True)
class Attempt:
    """One execution of an agent on a Task.

    The execution status and the task outcome are separate: an Attempt whose execution
    did not complete has no task outcome, and is not a failed Task.
    """

    attempt_id: str
    task_id: str
    execution_status: ExecutionStatus
    task_outcome: TaskOutcome | None = None
    # Why the execution did not complete, when it did not.
    execution_error: str | None = None
    # At most one measurement per resource type.
    resource_measurements: tuple[ResourceMeasurement, ...] = ()

    def __post_init__(self) -> None:
        for name in ("attempt_id", "task_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise EvidenceError(f"an Attempt identifies itself and its Task: {name} is {value!r}")
        try:
            object.__setattr__(self, "execution_status", ExecutionStatus(self.execution_status))
        except ValueError:
            raise EvidenceError(f"{self.execution_status!r} is not an execution status") from None
        if self.task_outcome is not None and not isinstance(self.task_outcome, TaskOutcome):
            raise EvidenceError(
                f"a task outcome is a TaskOutcome with its source. Got {self.task_outcome!r}"
            )
        measurements = tuple(self.resource_measurements)
        measured: set[ResourceType] = set()
        for measurement in measurements:
            if not isinstance(measurement, ResourceMeasurement):
                raise EvidenceError(
                    f"a resource measurement is a ResourceMeasurement with its unit and measurement "
                    f"boundary. Got {measurement!r}"
                )
            if measurement.resource_type in measured:
                raise EvidenceError(
                    f"Attempt {self.attempt_id!r} has more than one measurement of "
                    f"{measurement.resource_type.value}"
                )
            measured.add(measurement.resource_type)
        object.__setattr__(self, "resource_measurements", measurements)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "execution_status": self.execution_status.value,
            "execution_error": self.execution_error,
            "task_outcome": None if self.task_outcome is None else self.task_outcome.to_dict(),
            "resource_measurements": [measurement.to_dict() for measurement in self.resource_measurements],
        }

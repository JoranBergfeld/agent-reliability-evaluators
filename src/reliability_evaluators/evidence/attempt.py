"""The Attempt record: one execution of an agent on a Task, and the evidence attached to it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .condition import Condition, ConditionRole, LifecycleStep, ObservedCondition, StartingStatePolicy
from .error import EvidenceError


class ExecutionStatus(str, Enum):
    """How the execution of an Attempt ended. This is not the task outcome."""

    COMPLETED = "completed"
    FAILED = "failed"
    # The verify control did not show that the intended condition occurred.
    CONDITION_NOT_MET = "condition_not_met"


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
    # The experiment the Attempt was made for. None: its Task was given without an experiment,
    # so no starting-state policy and no condition were declared for it.
    experiment_id: str | None = None
    starting_state: StartingStatePolicy | None = None
    # The condition the experiment intended for this Attempt, and its part in the experiment.
    intended_condition: Condition | None = None
    condition_role: ConditionRole | None = None
    # What the verify control observed. None: nothing was observed.
    observed_condition: ObservedCondition | None = None
    # The customer controls invoked for this Attempt, in order.
    lifecycle: tuple[LifecycleStep, ...] = ()

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "execution_status": self.execution_status.value,
            "execution_error": self.execution_error,
            "task_outcome": None if self.task_outcome is None else self.task_outcome.to_dict(),
            "experiment_id": self.experiment_id,
            "starting_state": None if self.starting_state is None else self.starting_state.value,
            "intended_condition": self._intended_condition(),
            "observed_condition": None if self.observed_condition is None else self.observed_condition.to_dict(),
            "lifecycle": [step.to_dict() for step in self.lifecycle],
        }

    def _intended_condition(self) -> dict[str, Any] | None:
        if self.intended_condition is None:
            return None
        role = None if self.condition_role is None else self.condition_role.value
        return {"role": role, **self.intended_condition.to_dict()}

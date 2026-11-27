"""The Attempt record: one execution of an agent on a Task, and the evidence attached to it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .actions import ActionEvidence
from .errors import EvidenceError


def _require_text(owner: str, **fields: Any) -> None:
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise EvidenceError(f"{owner} records its {name}. Got {value!r}")


class ExecutionStatus(str, Enum):
    """How the running of an Attempt or an experiment ended. This is not the task outcome.

    Only a completed Attempt has a task outcome. Every other status is an execution that
    did not complete, and is never a failed Task.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    # The execution did not complete and the Attempt was made again. The retry is another Attempt.
    RETRIED = "retried"


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
class OperationalConstraint:
    """A customer-defined safety rule that applies to a Task. `description` is the rule itself."""

    constraint_id: str
    description: str

    def __post_init__(self) -> None:
        _require_text("an operational constraint", constraint_id=self.constraint_id, description=self.description)

    def to_dict(self) -> dict[str, Any]:
        return {"constraint_id": self.constraint_id, "description": self.description}


@dataclass(frozen=True, kw_only=True)
class ViolationAssessment:
    """Whether one Attempt violated one of its applicable operational constraints.

    `evidence` is what the determination rests on, and `source` says what made it, such as
    a deterministic check or a label.
    """

    constraint_id: str
    violated: bool
    evidence: str
    source: str

    def __post_init__(self) -> None:
        if type(self.violated) not in (bool, int) or self.violated not in (0, 1):
            raise EvidenceError(
                "a violation assessment says whether the constraint was violated: True or False, "
                f"or 1 or 0. Got {self.violated!r}"
            )
        object.__setattr__(self, "violated", bool(self.violated))
        _require_text(
            "a violation assessment", constraint_id=self.constraint_id, evidence=self.evidence, source=self.source
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "violated": self.violated,
            "evidence": self.evidence,
            "source": self.source,
        }


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
    # The identity of the Attempt this one retries. A retry never replaces that Attempt.
    retry_of: str | None = None
    # The actions the agent took, in order. None means they were not recorded, which is
    # not the same as a recording with no events.
    action_evidence: ActionEvidence | None = None
    # The operational constraints that apply to this Attempt, and the assessments made of them.
    operational_constraints: tuple[OperationalConstraint, ...] = ()
    violation_assessments: tuple[ViolationAssessment, ...] = ()

    def __post_init__(self) -> None:
        for name in ("attempt_id", "task_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise EvidenceError(f"an Attempt identifies itself and its Task: {name} is {value!r}")
        try:
            object.__setattr__(self, "execution_status", ExecutionStatus(self.execution_status))
        except ValueError:
            raise EvidenceError(f"{self.execution_status!r} is not an execution status") from None
        retry_of = self.retry_of
        if retry_of is not None and (
            not isinstance(retry_of, str) or not retry_of.strip() or retry_of == self.attempt_id
        ):
            raise EvidenceError(
                f"a retry identifies the other Attempt it retries: retry_of is {retry_of!r} "
                f"for Attempt {self.attempt_id!r}"
            )
        if self.task_outcome is not None and not isinstance(self.task_outcome, TaskOutcome):
            raise EvidenceError(
                f"a task outcome is a TaskOutcome with its source. Got {self.task_outcome!r}"
            )
        if self.action_evidence is not None and not isinstance(self.action_evidence, ActionEvidence):
            raise EvidenceError(
                f"action evidence is an ActionEvidence with its source. Got {self.action_evidence!r}"
            )
        self._require_assessments_of_applicable_constraints()

    def _require_assessments_of_applicable_constraints(self) -> None:
        object.__setattr__(self, "operational_constraints", tuple(self.operational_constraints))
        object.__setattr__(self, "violation_assessments", tuple(self.violation_assessments))
        applicable: set[str] = set()
        for constraint in self.operational_constraints:
            if not isinstance(constraint, OperationalConstraint):
                raise EvidenceError(f"an operational constraint is an OperationalConstraint. Got {constraint!r}")
            if constraint.constraint_id in applicable:
                raise EvidenceError(
                    f"Attempt {self.attempt_id!r} has more than one operational constraint "
                    f"with the identity {constraint.constraint_id!r}"
                )
            applicable.add(constraint.constraint_id)
        assessed: set[str] = set()
        for assessment in self.violation_assessments:
            if not isinstance(assessment, ViolationAssessment):
                raise EvidenceError(
                    f"a violation assessment is a ViolationAssessment with its evidence and source. Got {assessment!r}"
                )
            if assessment.constraint_id not in applicable:
                raise EvidenceError(
                    f"Attempt {self.attempt_id!r} has a violation assessment of {assessment.constraint_id!r}, "
                    "which is not one of its applicable operational constraints"
                )
            if assessment.constraint_id in assessed:
                raise EvidenceError(
                    f"Attempt {self.attempt_id!r} has more than one violation assessment "
                    f"of the operational constraint {assessment.constraint_id!r}"
                )
            assessed.add(assessment.constraint_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "execution_status": self.execution_status.value,
            "execution_error": self.execution_error,
            "retry_of": self.retry_of,
            "task_outcome": None if self.task_outcome is None else self.task_outcome.to_dict(),
            "action_evidence": None if self.action_evidence is None else self.action_evidence.to_dict(),
            "operational_constraints": [constraint.to_dict() for constraint in self.operational_constraints],
            "violation_assessments": [assessment.to_dict() for assessment in self.violation_assessments],
        }

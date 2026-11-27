"""The Attempt record: one execution of an agent on a Task, and the evidence attached to it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvidenceError(ValueError):
    """Evidence that cannot be accepted as given. It is rejected, never repaired."""


def _require_text(owner: str, **fields: Any) -> None:
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise EvidenceError(f"{owner} records its {name}. Got {value!r}")


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


@dataclass(frozen=True, kw_only=True)
class OperationalConstraint:
    """A customer-defined safety rule that applies to a Task. `description` is the rule itself."""

    constraint_id: str
    description: str

    def __post_init__(self) -> None:
        _require_text("an operational constraint", constraint_id=self.constraint_id, description=self.description)

    def to_dict(self) -> dict[str, Any]:
        return {"constraint_id": self.constraint_id, "description": self.description}


class SeverityLevel(str, Enum):
    """The three levels of severity that a reliability metric gives a weight to."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, kw_only=True)
class SeverityRating:
    """How serious one violation is.

    `label` is the rating as its source gave it. `level` is the severity level it stands for.
    The labels low, medium and high are the levels themselves. Any other label, such as
    critical, has a level only when the customer maps it to one explicitly, by giving `level`.
    Without that mapping the label is kept and has no level: none is assumed.
    """

    label: str
    level: SeverityLevel | None = None

    def __post_init__(self) -> None:
        _require_text("a severity rating", label=self.label)
        levels = [level.value for level in SeverityLevel]
        if self.level is None:
            mapped = SeverityLevel(self.label) if self.label in levels else None
        elif isinstance(self.level, str) and self.level in levels:
            mapped = SeverityLevel(self.level)
        else:
            raise EvidenceError(
                f"a severity label can only be mapped to low, medium or high. {self.label!r} is mapped to {self.level!r}"
            )
        object.__setattr__(self, "level", mapped)

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "level": None if self.level is None else self.level.value}


@dataclass(frozen=True, kw_only=True)
class ViolationAssessment:
    """Whether one Attempt violated one of its applicable operational constraints.

    `evidence` is what the determination rests on, and `source` says what made it, such as
    a deterministic check or a label. `severity` is the severity rating of the violation
    this assessment found, so a rating always belongs to one violation.
    """

    constraint_id: str
    violated: bool
    evidence: str
    source: str
    severity: SeverityRating | None = None

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
        if self.severity is None:
            return
        if not isinstance(self.severity, SeverityRating):
            raise EvidenceError(f"the severity of a violation is a SeverityRating. Got {self.severity!r}")
        if not self.violated:
            raise EvidenceError(
                f"a severity rating describes a violation, and the assessment of {self.constraint_id!r} found none"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "violated": self.violated,
            "evidence": self.evidence,
            "source": self.source,
            "severity": None if self.severity is None else self.severity.to_dict(),
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
        if self.task_outcome is not None and not isinstance(self.task_outcome, TaskOutcome):
            raise EvidenceError(
                f"a task outcome is a TaskOutcome with its source. Got {self.task_outcome!r}"
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
            "task_outcome": None if self.task_outcome is None else self.task_outcome.to_dict(),
            "operational_constraints": [constraint.to_dict() for constraint in self.operational_constraints],
            "violation_assessments": [assessment.to_dict() for assessment in self.violation_assessments],
        }

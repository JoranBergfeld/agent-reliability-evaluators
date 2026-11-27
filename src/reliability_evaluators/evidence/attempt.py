"""The Attempt record: one execution of an agent on a Task, and the evidence attached to it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any

from .actions import ActionEvidence
from .confidence import ConfidenceRating, ConfidenceUnavailable
from .errors import EvidenceError
from .experiment import Condition, ConditionRole, LifecycleStep, ObservedCondition, StartingStatePolicy


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
    # A customer control could not prepare the state or apply the condition. The agent was not invoked.
    SETUP_FAILED = "setup_failed"
    # The verify control did not show that the intended condition occurred.
    CONDITION_NOT_MET = "condition_not_met"
    # The Attempt ran, and the clean-up control then failed to restore the state.
    CLEANUP_FAILED = "cleanup_failed"


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
                "a severity label can only be mapped to low, medium or high. "
                f"{self.label!r} is mapped to {self.level!r}"
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
    # The identity of the Attempt this one retries. A retry never replaces that Attempt.
    retry_of: str | None = None
    # The actions the agent took, in order. None means they were not recorded, which is
    # not the same as a recording with no events.
    action_evidence: ActionEvidence | None = None
    # At most one measurement per resource type.
    resource_measurements: tuple[ResourceMeasurement, ...] = ()
    # None when the agent was not asked for a confidence rating for this Attempt.
    confidence: ConfidenceRating | ConfidenceUnavailable | None = None
    # The operational constraints that apply to this Attempt, and the assessments made of them.
    operational_constraints: tuple[OperationalConstraint, ...] = ()
    violation_assessments: tuple[ViolationAssessment, ...] = ()
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
        self._require_one_measurement_per_resource_type()
        confidence = self.confidence
        if confidence is not None and not isinstance(confidence, (ConfidenceRating, ConfidenceUnavailable)):
            raise EvidenceError(
                "confidence evidence is a ConfidenceRating or a ConfidenceUnavailable with its reason. "
                f"Got {self.confidence!r}"
            )
        self._require_assessments_of_applicable_constraints()
        self._require_recorded_experiment()

    def _require_recorded_experiment(self) -> None:
        if self.starting_state is not None:
            try:
                object.__setattr__(self, "starting_state", StartingStatePolicy(self.starting_state))
            except ValueError:
                raise EvidenceError(f"{self.starting_state!r} is not a starting-state policy") from None
        if self.intended_condition is not None or self.condition_role is not None:
            if not isinstance(self.intended_condition, Condition):
                raise EvidenceError(
                    f"an intended condition is a Condition, not a label. Got {self.intended_condition!r}"
                )
            try:
                object.__setattr__(self, "condition_role", ConditionRole(self.condition_role))
            except ValueError:
                raise EvidenceError(
                    f"a condition is the baseline or the variation of its experiment. Got {self.condition_role!r}"
                ) from None
        if self.observed_condition is not None and not isinstance(self.observed_condition, ObservedCondition):
            raise EvidenceError(
                f"an observed condition is an ObservedCondition with its source, not a label. "
                f"Got {self.observed_condition!r}"
            )

    def _require_one_measurement_per_resource_type(self) -> None:
        try:
            measurements = tuple(self.resource_measurements)
        except TypeError:
            raise EvidenceError(
                f"resource measurements are a sequence of ResourceMeasurement. Got {self.resource_measurements!r}"
            ) from None
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
            "resource_measurements": [measurement.to_dict() for measurement in self.resource_measurements],
            "confidence": None if self.confidence is None else self.confidence.to_dict(),
            "operational_constraints": [constraint.to_dict() for constraint in self.operational_constraints],
            "violation_assessments": [assessment.to_dict() for assessment in self.violation_assessments],
            "experiment_id": self.experiment_id,
            "starting_state": None if self.starting_state is None else self.starting_state.value,
            "intended_condition": self._intended_condition(),
            "observed_condition": None if self.observed_condition is None else self.observed_condition.to_dict(),
            "lifecycle": [step.to_dict() for step in self.lifecycle],
        }

    def _intended_condition(self) -> dict[str, Any] | None:
        if self.intended_condition is None:
            return None
        return {"role": self.condition_role.value, **self.intended_condition.to_dict()}

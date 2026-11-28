"""Evidence model: the Attempt record, what it records about its experiment, its validation,
and the grouping Evaluators are called with.

Also task outcomes supplied or corrected after the run, and the reading of evidence that a
customer supplies again.

Pure: standard library only, no I/O. The canonical metric definitions depend on it.
"""

from .actions import ActionEvent, ActionEvidence, ActionTypeMapping
from .attempt import (
    Attempt,
    ExecutionStatus,
    OperationalConstraint,
    ResourceMeasurement,
    ResourceType,
    SeverityLevel,
    SeverityRating,
    TaskOutcome,
    ViolationAssessment,
)
from .confidence import ConfidenceRating, ConfidenceUnavailable, read_confidence_response
from .corrections import OutcomeCorrection, apply_outcome_corrections
from .errors import EvidenceError
from .experiment import (
    Condition,
    ConditionRole,
    ExperimentStatus,
    LifecyclePoint,
    LifecycleStep,
    ObservedCondition,
    PromptVariant,
    StartingStatePolicy,
    VariationKind,
    json_data,
)
from .grouping import NOT_UNDER_THE_BASELINE, TaskEvidence, group_by_task, require_unambiguous, under_the_baseline
from .invocation import InvocationEvidence
from .reading import read_attempts

__all__ = [
    "NOT_UNDER_THE_BASELINE",
    "ActionEvent",
    "ActionEvidence",
    "ActionTypeMapping",
    "Attempt",
    "Condition",
    "ConditionRole",
    "ConfidenceRating",
    "ConfidenceUnavailable",
    "EvidenceError",
    "ExecutionStatus",
    "ExperimentStatus",
    "InvocationEvidence",
    "LifecyclePoint",
    "LifecycleStep",
    "ObservedCondition",
    "OperationalConstraint",
    "OutcomeCorrection",
    "PromptVariant",
    "ResourceMeasurement",
    "ResourceType",
    "SeverityLevel",
    "SeverityRating",
    "StartingStatePolicy",
    "TaskEvidence",
    "TaskOutcome",
    "VariationKind",
    "ViolationAssessment",
    "apply_outcome_corrections",
    "group_by_task",
    "json_data",
    "read_attempts",
    "read_confidence_response",
    "require_unambiguous",
    "under_the_baseline",
]

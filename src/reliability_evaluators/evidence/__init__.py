"""Evidence model: the Attempt record, what it records about its experiment, its validation,
and the grouping Evaluators are called with.

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
from .errors import EvidenceError
from .experiment import (
    Condition,
    ConditionRole,
    ExperimentStatus,
    LifecyclePoint,
    LifecycleStep,
    ObservedCondition,
    StartingStatePolicy,
    json_data,
)
from .grouping import TaskEvidence, group_by_task, require_unambiguous

__all__ = [
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
    "LifecyclePoint",
    "LifecycleStep",
    "ObservedCondition",
    "OperationalConstraint",
    "ResourceMeasurement",
    "ResourceType",
    "SeverityLevel",
    "SeverityRating",
    "StartingStatePolicy",
    "TaskEvidence",
    "TaskOutcome",
    "ViolationAssessment",
    "group_by_task",
    "json_data",
    "read_confidence_response",
    "require_unambiguous",
]

"""Evidence model: the Attempt record, its validation, and the grouping Evaluators are called with.

Pure: standard library only, no I/O. The canonical metric definitions depend on it.
"""

from .actions import ActionEvent, ActionEvidence, ActionTypeMapping
from .attempt import (
    Attempt,
    ExecutionStatus,
    OperationalConstraint,
    TaskOutcome,
    ViolationAssessment,
)
from .confidence import ConfidenceRating, ConfidenceUnavailable, read_confidence_response
from .errors import EvidenceError
from .grouping import TaskEvidence, group_by_task, require_unambiguous

__all__ = [
    "ActionEvent",
    "ActionEvidence",
    "ActionTypeMapping",
    "Attempt",
    "ConfidenceRating",
    "ConfidenceUnavailable",
    "EvidenceError",
    "ExecutionStatus",
    "OperationalConstraint",
    "TaskEvidence",
    "TaskOutcome",
    "ViolationAssessment",
    "group_by_task",
    "read_confidence_response",
    "require_unambiguous",
]

"""Evidence model: the Attempt record, its validation, and the grouping Evaluators are called with.

Pure: standard library only, no I/O. The canonical metric definitions depend on it.
"""

from .attempt import Attempt, EvidenceError, ExecutionStatus, TaskOutcome
from .confidence import ConfidenceRating, ConfidenceUnavailable, read_confidence_response
from .grouping import TaskEvidence, group_by_task, require_unambiguous

__all__ = [
    "Attempt",
    "ConfidenceRating",
    "ConfidenceUnavailable",
    "EvidenceError",
    "ExecutionStatus",
    "TaskEvidence",
    "TaskOutcome",
    "group_by_task",
    "read_confidence_response",
    "require_unambiguous",
]

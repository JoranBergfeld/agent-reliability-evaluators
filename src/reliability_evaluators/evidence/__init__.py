"""Evidence model: the Attempt record, its validation, and the grouping Evaluators are called with.

Pure: standard library only, no I/O. The canonical metric definitions depend on it.
"""

from .attempt import (
    Attempt,
    EvidenceError,
    ExecutionStatus,
    OperationalConstraint,
    TaskOutcome,
    ViolationAssessment,
)
from .grouping import TaskEvidence, group_by_task, require_unambiguous

__all__ = [
    "Attempt",
    "EvidenceError",
    "ExecutionStatus",
    "OperationalConstraint",
    "TaskEvidence",
    "TaskOutcome",
    "ViolationAssessment",
    "group_by_task",
    "require_unambiguous",
]

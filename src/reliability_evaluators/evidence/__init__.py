"""Evidence model: the Attempt record, its validation, and the grouping Evaluators are called with.

Pure: standard library only, no I/O. The canonical metric definitions depend on it.
"""

from .attempt import Attempt, ExecutionStatus, TaskOutcome
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
from .error import EvidenceError
from .grouping import TaskEvidence, group_by_task, require_unambiguous

__all__ = [
    "Attempt",
    "Condition",
    "ConditionRole",
    "EvidenceError",
    "ExecutionStatus",
    "ExperimentStatus",
    "LifecyclePoint",
    "LifecycleStep",
    "ObservedCondition",
    "StartingStatePolicy",
    "TaskEvidence",
    "TaskOutcome",
    "group_by_task",
    "json_data",
    "require_unambiguous",
]

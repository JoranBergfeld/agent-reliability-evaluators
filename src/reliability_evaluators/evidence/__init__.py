"""Evidence model: the Attempt record, what it records about its experiment, its validation,
and the grouping Evaluators are called with.

Pure: standard library only, no I/O. The canonical metric definitions depend on it.
"""

from .attempt import (
    Attempt,
    ExecutionStatus,
    OperationalConstraint,
    TaskOutcome,
    ViolationAssessment,
)
from .error import EvidenceError
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
    "Attempt",
    "Condition",
    "ConditionRole",
    "EvidenceError",
    "ExecutionStatus",
    "ExperimentStatus",
    "LifecyclePoint",
    "LifecycleStep",
    "ObservedCondition",
    "OperationalConstraint",
    "StartingStatePolicy",
    "TaskEvidence",
    "TaskOutcome",
    "ViolationAssessment",
    "group_by_task",
    "json_data",
    "require_unambiguous",
]

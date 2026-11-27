"""Evaluator sweep: coordinates the Attempts the Evaluators need and returns the scorecard."""

from .declaration import ExecutionLimits, Task, TaskSuccessRule, ViolationAssessor
from .runner import run_sweep

__all__ = ["ExecutionLimits", "Task", "TaskSuccessRule", "ViolationAssessor", "run_sweep"]

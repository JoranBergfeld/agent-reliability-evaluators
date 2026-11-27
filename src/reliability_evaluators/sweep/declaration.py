"""What a customer declares before an Evaluator sweep collects any evidence."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Sequence

from ..agent_target import AgentResponse, AttemptRequest
from ..evidence import OperationalConstraint, TaskOutcome, ViolationAssessment

# Decides the task outcome of one Attempt and says what it was decided from. It should
# look at independent evidence, such as the state of a service, not only at the response.
TaskSuccessRule = Callable[[AttemptRequest, AgentResponse], TaskOutcome]

# Assesses one Attempt against the operational constraints of its Task: one violation
# assessment per constraint it could assess, from labels or a deterministic check. Like the
# task-success rule, it should look at independent evidence and not only at the response.
# A constraint it returns no assessment for is unassessed for that Attempt, not complied with.
ViolationAssessor = Callable[[AttemptRequest, AgentResponse], Sequence[ViolationAssessment]]


@dataclass(frozen=True, kw_only=True)
class ExecutionLimits:
    """The limits a customer sets on the running of one experiment. None is imposed by default.

    `attempt_timeout_seconds` is how long one Attempt may take. The agent target is given
    the limit and is expected to stop there; the sweep cannot interrupt an invocation.

    `experiment_timeout_seconds` is how long the experiment may take, counted from the start
    of its first Attempt. Once it is reached no further Attempt or retry is started. An
    Attempt that is already running is bounded by `attempt_timeout_seconds` only.

    `max_retries` is how often a planned Attempt is made again after its execution failed
    or timed out. A completed Attempt is never retried, whatever its task outcome.
    """

    attempt_timeout_seconds: float | None = None
    experiment_timeout_seconds: float | None = None
    max_retries: int = 0

    def __post_init__(self) -> None:
        for name in ("attempt_timeout_seconds", "experiment_timeout_seconds"):
            limit = getattr(self, name)
            if limit is None:
                continue
            if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not isfinite(limit) or limit <= 0:
                raise ValueError(f"{name} is a positive, finite number of seconds or None, got {limit!r}")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError(f"max_retries is a whole number of retries, 0 or more, got {self.max_retries!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_timeout_seconds": self.attempt_timeout_seconds,
            "experiment_timeout_seconds": self.experiment_timeout_seconds,
            "max_retries": self.max_retries,
        }


@dataclass(frozen=True, kw_only=True)
class Task:
    """A unit of work for the agent, its task-success rule, and how its experiment is run.

    The Attempts planned at one Task are one experiment. `attempts` is how many to make
    and `limits` are its execution limits. Both are the customer's choice per Task.

    `operational_constraints` are the safety rules that apply to the Task, and
    `violation_assessor` assesses each Attempt against them.
    """

    task_id: str
    prompt: str
    success_rule: TaskSuccessRule
    attempts: int
    limits: ExecutionLimits = ExecutionLimits()
    operational_constraints: Sequence[OperationalConstraint] = ()
    violation_assessor: ViolationAssessor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError(f"a Task is identified by a task_id, got {self.task_id!r}")
        if type(self.attempts) is not int or self.attempts < 1:
            raise ValueError(f"Task {self.task_id!r} needs at least one Attempt, got {self.attempts!r}")
        if not isinstance(self.limits, ExecutionLimits):
            raise ValueError(f"the limits of Task {self.task_id!r} are ExecutionLimits, got {self.limits!r}")
        object.__setattr__(self, "operational_constraints", tuple(self.operational_constraints))
        declared: set[str] = set()
        for constraint in self.operational_constraints:
            if not isinstance(constraint, OperationalConstraint):
                raise ValueError(
                    f"Task {self.task_id!r} declares its operational constraints as OperationalConstraint, "
                    f"got {constraint!r}"
                )
            if constraint.constraint_id in declared:
                raise ValueError(
                    f"Task {self.task_id!r} declares more than one operational constraint "
                    f"as {constraint.constraint_id!r}"
                )
            declared.add(constraint.constraint_id)

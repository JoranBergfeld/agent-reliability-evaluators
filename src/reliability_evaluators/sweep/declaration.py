"""What a customer declares before an Evaluator sweep collects any evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

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
class Task:
    """A unit of work for the agent, its task-success rule, and how many Attempts to make.

    `operational_constraints` are the safety rules that apply to the Task, and
    `violation_assessor` assesses each Attempt against them.
    """

    task_id: str
    prompt: str
    success_rule: TaskSuccessRule
    attempts: int
    operational_constraints: Sequence[OperationalConstraint] = ()
    violation_assessor: ViolationAssessor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError(f"a Task is identified by a task_id, got {self.task_id!r}")
        if type(self.attempts) is not int or self.attempts < 1:
            raise ValueError(f"Task {self.task_id!r} needs at least one Attempt, got {self.attempts!r}")
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

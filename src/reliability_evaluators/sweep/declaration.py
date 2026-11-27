"""What a customer declares before an Evaluator sweep collects any evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from ..agent_target import AgentResponse, AttemptRequest
from ..evidence import ActionEvent, ActionTypeMapping, OperationalConstraint, TaskOutcome, ViolationAssessment

# Decides the task outcome of one Attempt and says what it was decided from. It should
# look at independent evidence, such as the state of a service, not only at the response.
TaskSuccessRule = Callable[[AttemptRequest, AgentResponse], TaskOutcome]

# Returns the actions of one Attempt, in order, from the declared evidence source. An empty
# sequence confirms that the Attempt took no action; None says that nothing was recorded.
ActionRecorder = Callable[[AttemptRequest, AgentResponse], "Sequence[ActionEvent] | None"]

# Assesses one Attempt against the operational constraints of its Task: one violation
# assessment per constraint it could assess, from labels or a deterministic check. Like the
# task-success rule, it should look at independent evidence and not only at the response.
# A constraint it returns no assessment for is unassessed for that Attempt, not complied with.
ViolationAssessor = Callable[[AttemptRequest, AgentResponse], Sequence[ViolationAssessment]]


@dataclass(frozen=True, kw_only=True)
class Task:
    """A unit of work for the agent, its task-success rule, and how many Attempts to make.

    `shareable_success_rule` is the task-success rule in words, as the agent may be shown
    it when it is asked for its confidence rating. Leave it out when the rule cannot be
    shared with the agent: the Task's Attempts then have no confidence rating.

    `operational_constraints` are the safety rules that apply to the Task, and
    `violation_assessor` assesses each Attempt against them.
    """

    task_id: str
    prompt: str
    success_rule: TaskSuccessRule
    attempts: int
    shareable_success_rule: str | None = None
    operational_constraints: Sequence[OperationalConstraint] = ()
    violation_assessor: ViolationAssessor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError(f"a Task is identified by a task_id, got {self.task_id!r}")
        if type(self.attempts) is not int or self.attempts < 1:
            raise ValueError(f"Task {self.task_id!r} needs at least one Attempt, got {self.attempts!r}")
        rule = self.shareable_success_rule
        if rule is not None and (not isinstance(rule, str) or not rule.strip()):
            raise ValueError(
                f"the shareable success rule of Task {self.task_id!r} is the rule in words, or left out. "
                f"Got {rule!r}"
            )
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


@dataclass(frozen=True, kw_only=True)
class ActionEvidenceDeclaration:
    """Where the action evidence of every Attempt comes from, and what its events mean.

    Needed by the trajectory metrics. Nothing here has a default: `evidence_source` names
    the source, such as native tool calls or service-side effects, `record` reads the
    actions of an Attempt from it, and `action_types` gives the action type of each tool
    name. Different sources can give different metric values for the same Attempts.
    """

    evidence_source: str
    record: ActionRecorder
    action_types: Mapping[str, str]
    mapping: ActionTypeMapping = field(init=False)

    def __post_init__(self) -> None:
        if not callable(self.record):
            raise ValueError(f"action evidence is read by a callable, got {self.record!r}")
        mapping = ActionTypeMapping(evidence_source=self.evidence_source, action_types=self.action_types)
        object.__setattr__(self, "mapping", mapping)

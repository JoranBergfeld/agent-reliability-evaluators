"""What a customer declares before an Evaluator sweep collects any evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from ..agent_target import AgentResponse, AttemptRequest
from ..evidence import ActionEvent, ActionTypeMapping, TaskOutcome

# Decides the task outcome of one Attempt and says what it was decided from. It should
# look at independent evidence, such as the state of a service, not only at the response.
TaskSuccessRule = Callable[[AttemptRequest, AgentResponse], TaskOutcome]

# Returns the actions of one Attempt, in order, from the declared evidence source. An empty
# sequence confirms that the Attempt took no action; None says that nothing was recorded.
ActionRecorder = Callable[[AttemptRequest, AgentResponse], "Sequence[ActionEvent] | None"]


@dataclass(frozen=True, kw_only=True)
class Task:
    """A unit of work for the agent, its task-success rule, and how many Attempts to make."""

    task_id: str
    prompt: str
    success_rule: TaskSuccessRule
    attempts: int

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError(f"a Task is identified by a task_id, got {self.task_id!r}")
        if type(self.attempts) is not int or self.attempts < 1:
            raise ValueError(f"Task {self.task_id!r} needs at least one Attempt, got {self.attempts!r}")


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

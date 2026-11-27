"""What a customer declares before an Evaluator sweep collects any evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..agent_target import AgentResponse, AttemptRequest
from ..evidence import Condition, ConditionRole, ObservedCondition, StartingStatePolicy, TaskOutcome

# Decides the task outcome of one Attempt and says what it was decided from. It should
# look at independent evidence, such as the state of a service, not only at the response.
TaskSuccessRule = Callable[[AttemptRequest, AgentResponse], TaskOutcome]


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
class ControlContext:
    """What a customer control is told about the Attempt it is invoked for."""

    experiment_id: str
    task_id: str
    attempt_id: str
    # The condition the Attempt is intended to run under.
    condition: Condition
    condition_role: ConditionRole
    starting_state: StartingStatePolicy
    # The test resources the experiment may change. A control changes nothing else.
    authorized_resources: tuple[str, ...]


class CustomerControls(Protocol):
    """The controls a customer supplies so that the sweep can run a controlled experiment.

    The sweep invokes them at defined lifecycle points and never looks inside the agent's
    tools. A control that cannot do its work raises.
    """

    def prepare_state(self, context: ControlContext) -> None:
        """Bring the test setup into the state the Attempt starts from."""

    def apply_condition(self, context: ControlContext) -> None:
        """Put the intended condition in place, the baseline included."""

    def verify_condition(self, context: ControlContext) -> ObservedCondition:
        """Report, from evidence, the condition the Attempt actually ran under."""

    def clean_up(self, context: ControlContext) -> None:
        """Restore the test setup. It may find the state only partly prepared."""


@dataclass(frozen=True, kw_only=True)
class Experiment:
    """A controlled experiment on one Task, declared before any Attempt is made.

    The Task is attempted `task.attempts` times under the baseline and, when a variation
    is declared, as many times under the variation. Everything else is held fixed.
    """

    experiment_id: str
    task: Task
    # What identifies the agent under test, such as its name, version and model.
    agent_configuration: Mapping[str, Any]
    starting_state: StartingStatePolicy
    baseline: Condition
    # The one intended difference from the baseline. None: the baseline only.
    variation: Condition | None
    controls: CustomerControls
    # Whether the agent or the controls can change anything outside the conversation.
    write_capable: bool
    authorized_resources: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "starting_state", StartingStatePolicy(self.starting_state))
        object.__setattr__(self, "authorized_resources", tuple(self.authorized_resources))

    def conditions(self) -> tuple[tuple[ConditionRole, Condition], ...]:
        """The conditions Attempts are made under, the baseline first."""
        declared = ((ConditionRole.BASELINE, self.baseline), (ConditionRole.VARIATION, self.variation))
        return tuple((role, condition) for role, condition in declared if condition is not None)

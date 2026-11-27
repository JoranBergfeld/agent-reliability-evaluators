"""What a customer declares before an Evaluator sweep collects any evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..agent_target import AgentResponse, AttemptRequest
from ..evidence import (
    Condition,
    ConditionRole,
    LifecyclePoint,
    ObservedCondition,
    StartingStatePolicy,
    TaskOutcome,
    json_data,
)

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
    tools. They may act through the customer's own test tools. A control that cannot do
    its work raises.
    """

    def prepare_state(self, context: ControlContext) -> None:
        """Bring the test setup into the state the Attempt starts from.

        Invoked before every Attempt under fresh state. Under continuing state it is
        invoked once, before the first Attempt under each condition.
        """

    def apply_condition(self, context: ControlContext) -> None:
        """Put the intended condition in place, the baseline included. Invoked before every Attempt."""

    def verify_condition(self, context: ControlContext) -> ObservedCondition:
        """Report, from evidence, whether the Attempt ran under the intended condition.

        Invoked after every Attempt whose setup succeeded, also when the invocation failed.
        """

    def clean_up(self, context: ControlContext) -> None:
        """Restore the test setup. It may find the state only partly prepared.

        Invoked after every Attempt under fresh state. Under continuing state it is
        invoked once, after the last Attempt under each condition.
        """


@dataclass(frozen=True, kw_only=True)
class Experiment:
    """A controlled experiment on one Task, declared before any Attempt is made.

    The Task is attempted `task.attempts` times under the baseline and, when a variation
    is declared, as many times under the variation. Everything else is held fixed.

    Nothing here has a default except the test resources: the starting-state policy in
    particular is always declared, so that state never continues by accident.
    """

    experiment_id: str
    # The Task with its task-success rule and the number of Attempts under each condition.
    task: Task
    # What identifies the agent under test, such as its name, version and model. The sweep
    # records it and does not configure the agent from it.
    agent_configuration: Mapping[str, Any]
    # "fresh" or "continuing". See StartingStatePolicy.
    starting_state: StartingStatePolicy
    baseline: Condition
    # The one intended difference from the baseline. None: the baseline only.
    variation: Condition | None
    controls: CustomerControls
    # Whether the agent or the controls can change anything outside the conversation.
    write_capable: bool
    # The test resources a write-capable experiment is authorized to change, by name.
    authorized_resources: Sequence[str] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise ValueError(f"an experiment is identified by an experiment_id, got {self.experiment_id!r}")
        this = f"experiment {self.experiment_id!r}"
        if not isinstance(self.task, Task):
            raise ValueError(f"{this} declares a Task with its task-success rule, got {self.task!r}")
        if not isinstance(self.agent_configuration, Mapping) or not self.agent_configuration:
            raise ValueError(
                f"{this} declares the agent configuration it holds fixed, such as the agent's name, "
                f"version and model, got {self.agent_configuration!r}"
            )
        object.__setattr__(
            self, "agent_configuration", json_data(self.agent_configuration, f"the agent configuration of {this}")
        )
        try:
            object.__setattr__(self, "starting_state", StartingStatePolicy(self.starting_state))
        except ValueError:
            raise ValueError(
                f"the starting-state policy is 'fresh' or 'continuing', and {this} declares {self.starting_state!r}"
            ) from None
        if not isinstance(self.baseline, Condition):
            raise ValueError(f"{this} declares its baseline as a Condition, got {self.baseline!r}")
        if self.variation is not None:
            if not isinstance(self.variation, Condition):
                raise ValueError(f"{this} declares its variation as a Condition or as None, got {self.variation!r}")
            if self.variation.condition_id == self.baseline.condition_id:
                raise ValueError(
                    f"the variation of {this} differs from the baseline, and both are {self.baseline.condition_id!r}"
                )
        for point in LifecyclePoint:
            if not callable(getattr(self.controls, point.value, None)):
                raise ValueError(f"the controls of {this} have no {point.value} control")
        self._require_authorized_resources(this)

    def _require_authorized_resources(self, this: str) -> None:
        if type(self.write_capable) is not bool:
            raise ValueError(f"{this} declares write_capable as True or False, got {self.write_capable!r}")
        resources = self.authorized_resources
        if isinstance(resources, str) or any(
            not isinstance(resource, str) or not resource.strip() for resource in resources
        ):
            raise ValueError(
                f"{this} identifies the test resources it may change as a list of names, got {resources!r}"
            )
        object.__setattr__(self, "authorized_resources", tuple(resources))
        if self.write_capable and not resources:
            raise ValueError(
                f"{this} is write-capable, so it identifies the test resources it is authorized to change"
            )
        if resources and not self.write_capable:
            raise ValueError(f"{this} is not write-capable, so it is authorized to change nothing, got {resources!r}")

    def conditions(self) -> tuple[tuple[ConditionRole, Condition], ...]:
        """The conditions Attempts are made under, the baseline first."""
        declared = ((ConditionRole.BASELINE, self.baseline), (ConditionRole.VARIATION, self.variation))
        return tuple((role, condition) for role, condition in declared if condition is not None)

    def to_dict(self) -> dict[str, Any]:
        """The declaration as it is recorded in the scorecard."""
        return {
            "experiment_id": self.experiment_id,
            "task_id": self.task.task_id,
            "agent_configuration": self.agent_configuration,
            "starting_state": self.starting_state.value,
            "baseline": self.baseline.to_dict(),
            "variation": None if self.variation is None else self.variation.to_dict(),
            "write_capable": self.write_capable,
            "authorized_resources": list(self.authorized_resources),
        }

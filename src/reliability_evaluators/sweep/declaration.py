"""What a customer declares before an Evaluator sweep collects any evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..agent_target import AgentResponse, AttemptRequest
from ..evidence import (
    ActionEvent,
    ActionTypeMapping,
    Condition,
    ConditionRole,
    LifecyclePoint,
    ObservedCondition,
    OperationalConstraint,
    StartingStatePolicy,
    TaskOutcome,
    ViolationAssessment,
    json_data,
)

# Decides the task outcome of one Attempt and says what it was decided from. It should
# look at independent evidence, such as the state of a service, not only at the response.
# A rule that cannot decide during the sweep returns NO_TASK_OUTCOME_YET.
TaskSuccessRule = Callable[[AttemptRequest, AgentResponse], "TaskOutcome | NoTaskOutcomeYet"]


class NoTaskOutcomeYet:
    """What a task-success rule returns when the task outcome is not known during the sweep.

    The Attempt is then recorded without a task outcome, for example because it is
    labelled afterwards. It is not a task failure. The outcome can be supplied later,
    with `recalculate`.
    """

    def __repr__(self) -> str:
        return "NO_TASK_OUTCOME_YET"


NO_TASK_OUTCOME_YET = NoTaskOutcomeYet()

# Returns the actions of one Attempt, in order, from the declared evidence source. An empty
# sequence confirms that the Attempt took no action; None says that nothing was recorded.
ActionRecorder = Callable[[AttemptRequest, AgentResponse], "Sequence[ActionEvent] | None"]

# Assesses one Attempt against the operational constraints of its Task: one violation
# assessment per constraint it could assess, from labels or a deterministic check. Like the
# task-success rule, it should look at independent evidence and not only at the response.
# A constraint it returns no assessment for is unassessed for that Attempt, not complied with.
# An assessment that finds a violation can carry the severity rating of that violation.
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
    # The one intended difference from the baseline, with the kind that says what it changes.
    # None: the baseline only.
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
        if self.baseline.kind is not None:
            raise ValueError(
                f"the baseline of {this} is not a variation, so it declares no kind, "
                f"got {self.baseline.kind.value!r}"
            )
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


@dataclass(frozen=True, kw_only=True)
class ConfiguredEvaluator:
    """An Evaluator selected for a sweep by metric identity, with declarations of its own.

    A metric selected by its identity alone uses what the sweep declares for every
    Evaluator. `action_evidence` is used for this Evaluator instead of the sweep's.

    Evaluators share Attempts only when what they need the Attempts to be made with is the
    same. Separate Attempts are made for an Evaluator that needs something else.
    """

    metric_id: str
    action_evidence: ActionEvidenceDeclaration | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, str) or not self.metric_id.strip():
            raise ValueError(f"an Evaluator is selected by its metric identity, got {self.metric_id!r}")
        if self.action_evidence is not None and not isinstance(self.action_evidence, ActionEvidenceDeclaration):
            raise ValueError(
                f"the action evidence of {self.metric_id!r} is declared as an ActionEvidenceDeclaration, "
                f"got {self.action_evidence!r}"
            )
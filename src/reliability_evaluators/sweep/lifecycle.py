"""The lifecycle of an experiment: the customer controls invoked around each of its Attempts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Sequence

from ..evidence import (
    Attempt,
    Condition,
    ConditionRole,
    ConfidenceUnavailable,
    ExecutionStatus,
    LifecyclePoint,
    LifecycleStep,
    ObservedCondition,
    PromptVariant,
    StartingStatePolicy,
)
from .confidence import ATTEMPT_NOT_COMPLETED
from .declaration import ControlContext, CustomerControls, Experiment, Task
from .execution import AttemptGate, Execution, execution_identity, execution_of, has_a_retry_left

# Completes an Attempt with its confidence evidence. It asks the agent only when a selected
# metric uses confidence and the Attempt it is given completed.
ConfidenceFollowUp = Callable[[Attempt], Attempt]

# Invokes the agent for one Attempt of a Task, given the Attempt's identity and the identity
# of the Attempt it retries, if any, and decides its task outcome. Returns the Attempt and
# its confidence follow-up. The lifecycle knows nothing else about the agent.
MakeAttempt = Callable[[Task, str, "str | None"], tuple[Attempt, ConfidenceFollowUp]]


@dataclass(frozen=True)
class _Stop:
    """Why the remaining Attempts of an experiment are not made."""

    reason: str
    # Whether the experiment's test resources were left in a state that no control restored.
    unrestored: bool


def run_experiments(
    experiments: Sequence[Experiment],
    make_attempt: MakeAttempt,
    ask_confidence: bool,
    gate_for: Callable[[Task], AttemptGate],
    attempts: list[Attempt],
    unrestored: dict[str, str] | None = None,
    attempt_set: int = 1,
) -> list[Execution]:
    """Run the experiments in the order declared. Returns what became of the Attempts planned for each.

    Each Attempt is added to `attempts` as it is made, so that the caller still has the
    evidence if an experiment raises.

    An experiment is not started when it may change a test resource that an earlier
    experiment failed to restore. Experiments that share no such resource are unrelated.
    `unrestored` holds those test resources, each with why it was not restored. A caller
    that runs experiments more than once in a sweep passes the same one every time.

    `ask_confidence` says whether a selected metric uses confidence. An Attempt for which
    the agent was not invoked then records why it has no confidence rating.

    `gate_for` gives the gate of a Task: whether a further Attempt or retry of it may
    start under its execution limits and the cancellation of the sweep. `attempt_set` is
    which set of the sweep's Attempts these experiments are run for.
    """
    records = []
    unrestored = {} if unrestored is None else unrestored
    for experiment in experiments:
        gate = gate_for(experiment.task)
        made: list[list[Attempt]] = []
        stop_reason = next(
            (unrestored[resource] for resource in experiment.authorized_resources if resource in unrestored), None
        )
        if stop_reason is None:
            made, stop = _run_experiment(experiment, make_attempt, ask_confidence, gate, attempts)
            if stop is not None:
                stop_reason = stop.reason
                if stop.unrestored:
                    for resource in experiment.authorized_resources:
                        unrestored.setdefault(
                            resource,
                            f"experiment {experiment.experiment_id!r} did not restore the test resource "
                            f"{resource!r}: {stop.reason}",
                        )
        # An experiment whose every Attempt was made completed, even if its last clean-up failed.
        records.append(
            execution_of(
                experiment.task, made, gate, experiment=experiment, stop_reason=stop_reason, attempt_set=attempt_set
            )
        )
    return records


def _run_experiment(
    experiment: Experiment,
    make_attempt: MakeAttempt,
    ask_confidence: bool,
    gate: AttemptGate,
    attempts: list[Attempt],
) -> tuple[list[list[Attempt]], _Stop | None]:
    """Make the Attempts of an experiment until they are all made or something stops the experiment.

    Each Attempt is added to `attempts`. Returns the planned Attempts that were made, each
    as its executions in order, and why a customer control stopped the experiment, if one did.

    A retry is an Attempt of its own, with the controls its starting-state policy gives
    every Attempt. Under fresh state it is cleaned up after and prepared for like any
    other. Under continuing state nothing is prepared again: the retry continues from the
    state the Attempt it retries left, as the next Attempt would have.
    """
    fresh = experiment.starting_state is StartingStatePolicy.FRESH
    plan = _planned_attempts(experiment)
    made: list[list[Attempt]] = []
    stop = None
    proceeds = bool(plan) and gate.may_start()
    while proceeds:
        context, number = plan[len(made)]
        executions: list[Attempt] = []
        made.append(executions)
        retries = True
        while retries:
            retry = len(executions)
            controlled = _ControlledAttempt(
                experiment,
                replace(context, attempt_id=execution_identity(context.attempt_id, retry)),
            )
            controlled.make(
                make_attempt,
                ask_confidence,
                prepares=fresh or (number == 1 and retry == 0),
                retry_of=executions[-1].attempt_id if executions else None,
            )
            if fresh:
                controlled.clean_up()
            retryable = has_a_retry_left(controlled.attempt, experiment.task, retry)
            # What follows this Attempt, if anything does: its retry, or the next planned Attempt.
            proceeds = controlled.stop is None and (retryable or len(made) < len(plan)) and gate.may_start()
            retries = retryable and proceeds
            if not fresh and (not proceeds or (number == experiment.task.attempts and not retries)):
                # The last Attempt under this condition, or the last one that is made at all.
                controlled.clean_up()
                if controlled.stop is not None:
                    proceeds = retries = False
            attempt = controlled.recorded(retried=retries)
            executions.append(attempt)
            attempts.append(attempt)
            stop = stop or controlled.stop
    return made, stop


def planned_attempt_ids(experiment: Experiment) -> list[str]:
    """The identities of the Attempts an experiment declares, in order."""
    return [context.attempt_id for context, _ in _planned_attempts(experiment)]


def _planned_attempts(experiment: Experiment) -> list[tuple[ControlContext, int]]:
    """The Attempts an experiment declares, in order.

    Each has its number under its condition. Under a prompt variation each has its number
    under its prompt variant, so that every variant is attempted like the baseline is.
    """
    return [
        (
            ControlContext(
                experiment_id=experiment.experiment_id,
                task_id=experiment.task.task_id,
                attempt_id=f"{experiment.experiment_id}/{under}/attempt-{number}",
                condition=condition,
                condition_role=role,
                starting_state=experiment.starting_state,
                authorized_resources=experiment.authorized_resources,
                prompt_variant=variant,
            ),
            number,
        )
        for role, condition in experiment.conditions()
        for under, variant in _prompts_under(experiment, role, condition)
        for number in range(1, experiment.task.attempts + 1)
    ]


def _prompts_under(
    experiment: Experiment, role: ConditionRole, condition: Condition
) -> list[tuple[str, PromptVariant | None]]:
    """What the agent is given under a condition: the Task's own prompt, or each prompt variant in turn.

    Each comes with the part of the Attempt identity that says what the Attempt is made under.
    """
    if role is ConditionRole.VARIATION and experiment.varies_prompt():
        return [(f"{condition.condition_id}/{variant.variant_id}", variant) for variant in experiment.prompt_variants]
    return [(condition.condition_id, None)]


@dataclass
class _ControlledAttempt:
    """One Attempt of an experiment, and the customer controls invoked around it so far."""

    experiment: Experiment
    context: ControlContext
    attempt: Attempt | None = None
    observed: ObservedCondition | None = None
    steps: list[LifecycleStep] = field(default_factory=list)
    # Why the later Attempts of the experiment are not to be made, when they are not.
    stop: _Stop | None = None

    def make(
        self, make_attempt: MakeAttempt, ask_confidence: bool, *, prepares: bool, retry_of: str | None
    ) -> None:
        """Set the Attempt up, invoke the agent for it, verify its condition and ask for its confidence."""
        experiment, context = self.experiment, self.context
        task, controls = experiment.task, experiment.controls
        if context.prompt_variant is not None:
            # The same Task with the exact text of the variant as its prompt: the agent target is given
            # that text, and the Attempt is judged by the same task-success rule.
            task = replace(task, prompt=context.prompt_variant.text)
        setup_error = None
        if prepares:
            setup_error = _invoke_control(LifecyclePoint.PREPARE_STATE, controls, context, self.steps)[1]
            if setup_error is not None and experiment.starting_state is StartingStatePolicy.CONTINUING:
                # The later Attempts would continue from the state that was not prepared.
                self.stop = _Stop(
                    f"prepare_state failed before Attempt {context.attempt_id!r}: {setup_error}", unrestored=False
                )
        if setup_error is None:
            setup_error = _invoke_control(LifecyclePoint.APPLY_CONDITION, controls, context, self.steps)[1]
        if setup_error is not None:
            self.attempt = Attempt(
                attempt_id=context.attempt_id,
                task_id=task.task_id,
                execution_status=ExecutionStatus.SETUP_FAILED,
                execution_error=f"{self.steps[-1].point.value} failed: {setup_error}",
                retry_of=retry_of,
                # The agent was not invoked, so there is nothing to ask it about.
                confidence=ConfidenceUnavailable(reason=ATTEMPT_NOT_COMPLETED) if ask_confidence else None,
                operational_constraints=task.operational_constraints,
            )
            return
        attempt, confidence_follow_up = make_attempt(task, context.attempt_id, retry_of)
        self.observed, verify_error = _verify_condition(controls, context, self.steps)
        if attempt.execution_status is ExecutionStatus.COMPLETED and (verify_error or not self.observed.met):
            attempt = replace(
                attempt,
                execution_status=ExecutionStatus.CONDITION_NOT_MET,
                execution_error=(
                    f"the intended condition was not verified: {verify_error}"
                    if verify_error
                    else "the verify control observed that the intended condition did not occur"
                ),
            )
        # After the condition is verified, so that the agent is not asked about an Attempt whose
        # condition was not met. Before clean-up, so that the agent is asked in the state the Attempt left.
        self.attempt = confidence_follow_up(attempt)

    def clean_up(self) -> None:
        cleanup_error = _invoke_control(
            LifecyclePoint.CLEAN_UP, self.experiment.controls, self.context, self.steps
        )[1]
        if cleanup_error is not None:
            self.stop = _Stop(
                f"clean_up failed after Attempt {self.context.attempt_id!r}: {cleanup_error}", unrestored=True
            )
            if self.attempt.execution_status is ExecutionStatus.COMPLETED:
                self.attempt = replace(
                    self.attempt,
                    execution_status=ExecutionStatus.CLEANUP_FAILED,
                    execution_error=f"clean_up failed: {cleanup_error}",
                )

    def recorded(self, *, retried: bool) -> Attempt:
        """The Attempt as it is kept in the evidence. `retried` says that it is made again."""
        return replace(
            self.attempt,
            # The Attempt stays in the evidence with why it did not complete. The retry is another Attempt.
            execution_status=ExecutionStatus.RETRIED if retried else self.attempt.execution_status,
            experiment_id=self.experiment.experiment_id,
            starting_state=self.experiment.starting_state,
            intended_condition=self.context.condition,
            condition_role=self.context.condition_role,
            prompt_variant=self.context.prompt_variant,
            observed_condition=self.observed,
            lifecycle=tuple(self.steps),
        )


def _verify_condition(
    controls: CustomerControls, context: ControlContext, steps: list[LifecycleStep]
) -> tuple[ObservedCondition | None, str | None]:
    """The condition the verify control observed, or why it observed none."""
    observed, error = _invoke_control(LifecyclePoint.VERIFY_CONDITION, controls, context, steps)
    if error is None and not isinstance(observed, ObservedCondition):
        error = f"the verify control returned {observed!r}, not an ObservedCondition with its source"
        steps[-1] = LifecycleStep(LifecyclePoint.VERIFY_CONDITION, error)
    return (None, error) if error is not None else (observed, None)


def _invoke_control(
    point: LifecyclePoint, controls: CustomerControls, context: ControlContext, steps: list[LifecycleStep]
) -> tuple[Any, str | None]:
    """Invoke a customer control, record it in `steps`, and return its result and why it failed."""
    try:
        result = getattr(controls, point.value)(context)
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"
        steps.append(LifecycleStep(point, error))
        return None, error
    steps.append(LifecycleStep(point))
    return result, None

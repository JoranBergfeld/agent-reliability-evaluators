"""The lifecycle of an experiment: the customer controls invoked around each of its Attempts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from ..evidence import (
    Attempt,
    ConfidenceUnavailable,
    ExecutionStatus,
    ExperimentStatus,
    LifecyclePoint,
    LifecycleStep,
    ObservedCondition,
    StartingStatePolicy,
)
from .confidence import ATTEMPT_NOT_COMPLETED
from .declaration import ControlContext, CustomerControls, Experiment, Task

# Completes an Attempt with its confidence evidence. It asks the agent only when a selected
# metric uses confidence and the Attempt it is given completed.
ConfidenceFollowUp = Callable[[Attempt], Attempt]

# Invokes the agent for one Attempt of a Task, numbered within its condition and given its
# identity, and decides its task outcome. Returns the Attempt and its confidence follow-up.
# The lifecycle knows nothing else about the agent.
MakeAttempt = Callable[[Task, int, str], tuple[Attempt, ConfidenceFollowUp]]


@dataclass(frozen=True)
class _Stop:
    """Why the remaining Attempts of an experiment are not made."""

    reason: str
    # Whether the experiment's test resources were left in a state that no control restored.
    unrestored: bool


def run_experiments(
    experiments: Sequence[Experiment], make_attempt: MakeAttempt, ask_confidence: bool
) -> tuple[list[Attempt], list[dict[str, Any]]]:
    """Run the experiments in the order declared. Returns their Attempts and their scorecard records.

    An experiment is not started when it may change a test resource that an earlier
    experiment failed to restore. Experiments that share no such resource are unrelated.

    `ask_confidence` says whether a selected metric uses confidence. An Attempt for which
    the agent was not invoked then records why it has no confidence rating.
    """
    attempts: list[Attempt] = []
    records = []
    unrestored: dict[str, str] = {}
    for experiment in experiments:
        planned = len(_planned_attempts(experiment))
        made: list[Attempt] = []
        stop_reason = next(
            (unrestored[resource] for resource in experiment.authorized_resources if resource in unrestored), None
        )
        if stop_reason is None:
            made, stop = _run_experiment(experiment, make_attempt, ask_confidence)
            if stop is not None:
                stop_reason = stop.reason
                if stop.unrestored:
                    for resource in experiment.authorized_resources:
                        unrestored.setdefault(
                            resource,
                            f"experiment {experiment.experiment_id!r} did not restore the test resource "
                            f"{resource!r}: {stop.reason}",
                        )
        attempts.extend(made)
        # An experiment whose every Attempt was made completed, even if its last clean-up failed.
        stopped = len(made) < planned
        records.append(
            {
                **experiment.to_dict(),
                "execution": {
                    "status": (ExperimentStatus.STOPPED if stopped else ExperimentStatus.COMPLETED).value,
                    "reason": stop_reason if stopped else None,
                    "attempts_planned": planned,
                    "attempts_recorded": len(made),
                },
            }
        )
    return attempts, records


def _run_experiment(
    experiment: Experiment, make_attempt: MakeAttempt, ask_confidence: bool
) -> tuple[list[Attempt], _Stop | None]:
    """Make the Attempts of an experiment until they are all made or one stops the experiment."""
    fresh = experiment.starting_state is StartingStatePolicy.FRESH
    attempts = []
    for context, number in _planned_attempts(experiment):
        attempt, stop = _run_controlled_attempt(
            experiment,
            context,
            number,
            make_attempt,
            ask_confidence,
            prepares=fresh or number == 1,
            cleans_up=fresh or number == experiment.task.attempts,
        )
        attempts.append(attempt)
        if stop is not None:
            return attempts, stop
    return attempts, None


def planned_attempt_ids(experiment: Experiment) -> list[str]:
    """The identities of the Attempts an experiment declares, in order."""
    return [context.attempt_id for context, _ in _planned_attempts(experiment)]


def _planned_attempts(experiment: Experiment) -> list[tuple[ControlContext, int]]:
    """The Attempts an experiment declares, in order, each with its number under its condition."""
    return [
        (
            ControlContext(
                experiment_id=experiment.experiment_id,
                task_id=experiment.task.task_id,
                attempt_id=f"{experiment.experiment_id}/{condition.condition_id}/attempt-{number}",
                condition=condition,
                condition_role=role,
                starting_state=experiment.starting_state,
                authorized_resources=experiment.authorized_resources,
            ),
            number,
        )
        for role, condition in experiment.conditions()
        for number in range(1, experiment.task.attempts + 1)
    ]


def _run_controlled_attempt(
    experiment: Experiment,
    context: ControlContext,
    number: int,
    make_attempt: MakeAttempt,
    ask_confidence: bool,
    *,
    prepares: bool,
    cleans_up: bool,
) -> tuple[Attempt, _Stop | None]:
    """Make one Attempt of an experiment, invoking the customer controls around it.

    Returns the Attempt and, when the later Attempts of the experiment are not to be made, why.
    """
    task, controls = experiment.task, experiment.controls
    steps: list[LifecycleStep] = []
    observed = None
    setup_error = None
    stop = None
    if prepares:
        setup_error = _invoke_control(LifecyclePoint.PREPARE_STATE, controls, context, steps)[1]
        if setup_error is not None and experiment.starting_state is StartingStatePolicy.CONTINUING:
            # The later Attempts would continue from the state that was not prepared.
            stop = _Stop(
                f"prepare_state failed before Attempt {context.attempt_id!r}: {setup_error}", unrestored=False
            )
    if setup_error is None:
        setup_error = _invoke_control(LifecyclePoint.APPLY_CONDITION, controls, context, steps)[1]
    if setup_error is not None:
        attempt = Attempt(
            attempt_id=context.attempt_id,
            task_id=task.task_id,
            execution_status=ExecutionStatus.SETUP_FAILED,
            execution_error=f"{steps[-1].point.value} failed: {setup_error}",
            # The agent was not invoked, so there is nothing to ask it about.
            confidence=ConfidenceUnavailable(reason=ATTEMPT_NOT_COMPLETED) if ask_confidence else None,
            operational_constraints=task.operational_constraints,
        )
    else:
        attempt, confidence_follow_up = make_attempt(task, number, context.attempt_id)
        observed, verify_error = _verify_condition(controls, context, steps)
        if attempt.execution_status is ExecutionStatus.COMPLETED and (verify_error or not observed.met):
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
        attempt = confidence_follow_up(attempt)
    if cleans_up or stop is not None:
        cleanup_error = _invoke_control(LifecyclePoint.CLEAN_UP, controls, context, steps)[1]
        if cleanup_error is not None:
            stop = _Stop(f"clean_up failed after Attempt {context.attempt_id!r}: {cleanup_error}", unrestored=True)
            if attempt.execution_status is ExecutionStatus.COMPLETED:
                attempt = replace(
                    attempt,
                    execution_status=ExecutionStatus.CLEANUP_FAILED,
                    execution_error=f"clean_up failed: {cleanup_error}",
                )
    attempt = replace(
        attempt,
        experiment_id=experiment.experiment_id,
        starting_state=experiment.starting_state,
        intended_condition=context.condition,
        condition_role=context.condition_role,
        observed_condition=observed,
        lifecycle=tuple(steps),
    )
    return attempt, stop


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

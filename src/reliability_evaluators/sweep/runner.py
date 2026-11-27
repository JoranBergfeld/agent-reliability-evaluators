"""The Evaluator sweep's public entry point."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from time import perf_counter
from typing import Any, Callable, Sequence

from ..agent_target import AgentResponse, AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, ScoringPath
from ..evidence import (
    ActionEvidence,
    Attempt,
    ConditionRole,
    ConfidenceUnavailable,
    EvidenceError,
    ExecutionStatus,
    ResourceMeasurement,
    ResourceType,
    TaskEvidence,
    TaskOutcome,
    ViolationAssessment,
)
from ..scorecard import AcceptanceCriterion, ArtifactLocation, ScoredMetric, build_scorecard, criteria_by_metric
from .confidence import ATTEMPT_NOT_COMPLETED, SWEEP_CANCELLED, elicit_confidence
from .declaration import ActionEvidenceDeclaration, Experiment, Task
from .execution import RETRYABLE, Budget, Cancellation, planned_attempts
from .lifecycle import ConfidenceFollowUp, planned_attempt_ids, run_experiments

ELAPSED_TIME_UNIT = "seconds"
ELAPSED_TIME_BOUNDARY = (
    "wall clock from just before the agent target is invoked until it returns, measured by the Evaluator sweep"
)


def run_sweep(
    *,
    tasks: Sequence[Task] = (),
    experiments: Sequence[Experiment] = (),
    agent_target: AgentTarget,
    metrics: Sequence[str],
    action_evidence: ActionEvidenceDeclaration | None = None,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
    max_concurrent_attempts: int = 1,
    cancellation: Callable[[], bool] | None = None,
    clock: Callable[[], float] = perf_counter,
    artifact_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` names the reliability metrics to score, by metric identity. Scoring happens
    in this process. The result is the machine-readable reliability scorecard, as
    JSON-compatible data.

    `experiments` are controlled experiments: the sweep invokes their customer controls
    around every Attempt and follows their starting-state policy. `tasks` are Tasks given
    without an experiment: nothing prepares or restores their state, and their Attempts
    record no starting-state policy and no condition. The sweep never assumes that the
    agent target resets conversation or external state.

    Attempts run one after another, Task by Task and then experiment by experiment. With
    `max_concurrent_attempts` above 1, up to that many Attempts of the Tasks given without
    an experiment are in flight at once, and the agent target and everything the customer
    supplies for those Tasks are called from several threads, `cancellation` included.
    The Attempts of an experiment always run one after another, after those Tasks, because
    each depends on the state the customer controls prepare. The scorecard records what
    was declared. Shared state and timing are the caller's to control.

    Every Task has its own execution limits, in an experiment or without one. A planned
    Attempt whose invocation failed or timed out is made again while it has retries left.
    `cancellation` is asked before every Attempt and retry; once it returns True no further
    one is started and no further request is sent to the agent. Attempts that are in flight
    are not interrupted. What was collected until then is scored, and the scorecard is
    partial and names what is missing.

    `action_evidence` declares where the actions of every Attempt are recorded from and
    what action type each tool name is. The trajectory metrics need it, and every metric
    that needs it is scored from the same Attempts.

    When a selected metric uses confidence, each completed Attempt is followed by one
    separate confidence-only request through the agent target, before the next Attempt.
    In an experiment the request is sent after the condition is verified and before the
    clean-up control, and only when the condition was met.

    `acceptance_criteria` are the customer's own, at most one per metric. Each is reported
    with its metric as met, not met, or not assessed with a reason. Without one, a metric
    has no acceptance verdict.

    The sweep times every invocation that returns, on `clock`, which gives seconds and
    never runs backwards. An agent target that reports its own elapsed time is recorded
    instead. Every other resource measurement comes from the agent target. Time limits are
    measured on the same clock.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that does not return a sourced, binary TaskOutcome stops the sweep. So does a
    violation assessor that does not return sourced violation assessments of the operational
    constraints its Task declares, and an action recording that is not a sequence of ActionEvent.

    With `artifact_dir`, the attempt-level evidence, the scorecard and the human-readable
    report rendered from it are also written to that directory. The caller chooses it in the
    run or artifact context of the platform it runs on. The sweep then either delivers all
    three or raises ReportingFailure: whatever stops it, a rejected declaration included, is
    raised as that, and the evidence collected until then is kept at the location.
    """
    location = None if artifact_dir is None else ArtifactLocation(artifact_dir)
    attempts: list[Attempt] = []
    try:
        scorecard = _sweep(
            attempts,
            tasks=tasks,
            experiments=experiments,
            agent_target=agent_target,
            metrics=metrics,
            action_evidence=action_evidence,
            acceptance_criteria=acceptance_criteria,
            max_concurrent_attempts=max_concurrent_attempts,
            cancellation=cancellation,
            clock=clock,
        )
    except Exception as error:
        if location is None:
            raise
        raise location.failed(
            "the Evaluator sweep stopped before it produced a scorecard",
            error,
            evidence=[attempt.to_dict() for attempt in attempts],
        ) from error
    if location is not None:
        location.deliver(scorecard)
    return scorecard


def _sweep(
    attempts: list[Attempt],
    *,
    tasks: Sequence[Task],
    experiments: Sequence[Experiment],
    agent_target: AgentTarget,
    metrics: Sequence[str],
    action_evidence: ActionEvidenceDeclaration | None,
    acceptance_criteria: Sequence[AcceptanceCriterion],
    max_concurrent_attempts: int,
    cancellation: Callable[[], bool] | None,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """Make the Attempts and score them. Each Attempt is added to `attempts` as it is made,
    so that `run_sweep` still has the evidence if this stops.
    """
    evaluators = [_evaluator(metric_id) for metric_id in metrics]
    declared = {"action_types": None if action_evidence is None else action_evidence.mapping}
    for evaluator in evaluators:
        _require_declarations(evaluator, declared)
    _require_distinct_tasks([*tasks, *(experiment.task for experiment in experiments)])
    _require_distinct_identities(tasks, experiments)
    # Called for its check: a second criterion for one metric is rejected here, not after the Attempts.
    criteria_by_metric(acceptance_criteria)
    if type(max_concurrent_attempts) is not int or max_concurrent_attempts < 1:
        raise ValueError(f"max_concurrent_attempts is a whole number, 1 or more, got {max_concurrent_attempts!r}")
    ask_confidence = any(evaluator.needs_confidence for evaluator in evaluators)
    cancelled = Cancellation(cancellation)
    make_attempt = partial(
        _invoke_agent,
        agent_target=agent_target,
        action_evidence=action_evidence,
        ask_confidence=ask_confidence,
        cancellation=cancelled,
        clock=clock,
    )
    budgets = [Budget(task, cancelled, clock) for task in tasks]
    made = _run_planned_attempts(
        [
            partial(_run_planned_attempt, task, number, make_attempt, budget, attempts)
            for task, budget in zip(tasks, budgets)
            for number in range(1, task.attempts + 1)
        ],
        max_concurrent_attempts,
    )
    # In the order planned, whatever the order they finished in.
    attempts[:] = [attempt for executions in made for attempt in executions]
    planned = []
    for task, budget in zip(tasks, budgets):
        of_task, made = made[: task.attempts], made[task.attempts :]
        planned.append(planned_attempts(task, [executions for executions in of_task if executions], budget))
    planned += run_experiments(
        experiments, make_attempt, ask_confidence, lambda task: Budget(task, cancelled, clock), attempts
    )
    # The Evaluators that exist score Attempts under the baseline. Attempts under a variation
    # are kept in the scorecard and are not mixed in.
    scored_attempts = [attempt for attempt in attempts if attempt.condition_role is not ConditionRole.VARIATION]
    # A Task whose Attempts were not made is still a Task in the evidence.
    evidence = [
        TaskEvidence(task.task_id, tuple(attempt for attempt in scored_attempts if attempt.task_id == task.task_id))
        for task in (*tasks, *(experiment.task for experiment in experiments))
    ]
    scored = [
        ScoredMetric(
            result=evaluator.evaluate(evidence, *(declared[name] for name in evaluator.declarations)),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
        )
        for evaluator in evaluators
    ]
    return build_scorecard(
        metrics=scored,
        attempts=attempts,
        planned=planned,
        max_concurrent_attempts=max_concurrent_attempts,
        acceptance_criteria=acceptance_criteria,
    )


def _run_planned_attempts(
    planned: Sequence[Callable[[], list[Attempt]]], max_concurrent_attempts: int
) -> list[list[Attempt]]:
    """Run every planned Attempt and give their executions in the order they were planned."""
    if max_concurrent_attempts == 1:
        return [run() for run in planned]
    with ThreadPoolExecutor(max_workers=max_concurrent_attempts) as pool:
        futures = [pool.submit(run) for run in planned]
        try:
            return [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise


def _evaluator(metric_id: str) -> Evaluator:
    if metric_id in EVALUATORS:
        return EVALUATORS[metric_id]
    if any(metric.metric_id == metric_id for metric in RELIABILITY_METRICS):
        raise ValueError(f"{metric_id!r} has no Evaluator in this version")
    raise ValueError(f"{metric_id!r} is not a reliability metric")


def _require_declarations(evaluator: Evaluator, declared: dict[str, Any]) -> None:
    undeclared = {
        "action_types": "action evidence to be declared: the evidence source and the action types have no default",
    }
    for name in evaluator.declarations:
        if declared[name] is None:
            raise ValueError(f"{evaluator.definition.metric.metric_id!r} needs {undeclared[name]}")


def _require_distinct_tasks(tasks: Sequence[Task]) -> None:
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(f"more than one Task is declared as {task.task_id!r}")
        seen.add(task.task_id)


def _require_distinct_identities(tasks: Sequence[Task], experiments: Sequence[Experiment]) -> None:
    seen: set[str] = set()
    for experiment in experiments:
        if experiment.experiment_id in seen:
            raise ValueError(f"more than one experiment is declared as {experiment.experiment_id!r}")
        seen.add(experiment.experiment_id)
    planned: set[str] = set()
    for attempt_id in (
        *(_attempt_id(task, number) for task in tasks for number in range(1, task.attempts + 1)),
        *(attempt_id for experiment in experiments for attempt_id in planned_attempt_ids(experiment)),
    ):
        if attempt_id in planned:
            raise ValueError(f"more than one Attempt would have the identity {attempt_id!r}")
        planned.add(attempt_id)


def _attempt_id(task: Task, number: int) -> str:
    return f"{task.task_id}/attempt-{number}"


def _run_planned_attempt(
    task: Task,
    number: int,
    make_attempt: Callable[..., tuple[Attempt, ConfidenceFollowUp]],
    budget: Budget,
    attempts: list[Attempt],
) -> list[Attempt]:
    """Make one planned Attempt of a Task given without an experiment, and its retries.

    Every execution is returned, in order, and added to `attempts` as it is made. Nothing
    is returned for a planned Attempt that may no longer start.
    """
    first = _attempt_id(task, number)
    executions: list[Attempt] = []
    retries = budget.may_start()
    while retries:
        retry = len(executions)
        attempt, follow_up = make_attempt(
            task, first if retry == 0 else f"{first}/retry-{retry}", executions[-1].attempt_id if executions else None
        )
        attempt = follow_up(attempt)
        retries = (
            attempt.execution_status in RETRYABLE and retry < task.limits.max_retries and budget.may_start()
        )
        if retries:
            # The Attempt stays in the evidence with why it did not complete. The retry is another Attempt.
            attempt = replace(attempt, execution_status=ExecutionStatus.RETRIED)
        executions.append(attempt)
        attempts.append(attempt)
    return executions


def _invoke_agent(
    task: Task,
    attempt_id: str,
    retry_of: str | None,
    *,
    agent_target: AgentTarget,
    action_evidence: ActionEvidenceDeclaration | None,
    ask_confidence: bool,
    cancellation: Cancellation,
    clock: Callable[[], float],
) -> tuple[Attempt, ConfidenceFollowUp]:
    """Invoke the agent for one Attempt and collect the evidence of it, short of its confidence evidence.

    Returns the Attempt and the follow-up that completes it with its confidence evidence.
    The two are separate so that an experiment can verify its condition in between.
    """
    limit = task.limits.attempt_timeout_seconds
    request = AttemptRequest(attempt_id=attempt_id, task_id=task.task_id, prompt=task.prompt, timeout_seconds=limit)

    def not_completed(status: ExecutionStatus, why: str) -> tuple[Attempt, ConfidenceFollowUp]:
        attempt = Attempt(
            attempt_id=attempt_id,
            task_id=task.task_id,
            execution_status=status,
            execution_error=why,
            retry_of=retry_of,
            operational_constraints=task.operational_constraints,
        )
        # The agent is not asked about an Attempt that did not complete.
        return attempt, partial(_with_confidence, ask=None, ask_confidence=ask_confidence, cancellation=cancellation)

    started = clock()
    try:
        response = agent_target.invoke(request)
    except Exception as error:
        why = f"{type(error).__name__}: {error}"
        if isinstance(error, TimeoutError):
            return not_completed(ExecutionStatus.TIMED_OUT, why)
        # The sweep cannot interrupt an invocation. It measures, so that the limit holds
        # whether or not the agent target stopped at it.
        if limit is not None and clock() - started > limit:
            return not_completed(
                ExecutionStatus.TIMED_OUT, f"{why}, after the Attempt's time limit of {limit:g} seconds"
            )
        if cancellation.requested():
            return not_completed(ExecutionStatus.CANCELLED, f"{why}, after the sweep was cancelled")
        return not_completed(ExecutionStatus.FAILED, why)
    elapsed = clock() - started
    if limit is not None and elapsed > limit:
        # The limit applies to every Attempt alike, so a late answer is not scored.
        return not_completed(
            ExecutionStatus.TIMED_OUT, f"the agent target answered after the Attempt's time limit of {limit:g} seconds"
        )
    outcome = task.success_rule(request, response)
    if not isinstance(outcome, TaskOutcome):
        raise EvidenceError(
            f"the task-success rule of Task {task.task_id!r} must return a TaskOutcome with its source, "
            f"got {outcome!r}"
        )
    # The task outcome is decided before the agent is asked for its confidence rating, so the
    # rating cannot enter the outcome, and the follow-up cannot disturb what the outcome is decided from.
    # The same holds for the violation assessments and the recorded actions.
    violation_assessments = _assess_violations(task, request, response)
    actions = None if action_evidence is None else _recorded_actions(action_evidence, request, response)
    attempt = Attempt(
        attempt_id=attempt_id,
        task_id=task.task_id,
        execution_status=ExecutionStatus.COMPLETED,
        task_outcome=outcome,
        retry_of=retry_of,
        resource_measurements=_resource_measurements(elapsed, response.resource_measurements),
        operational_constraints=task.operational_constraints,
        violation_assessments=violation_assessments,
        action_evidence=actions,
    )
    ask = partial(elicit_confidence, task, request, response, agent_target)
    return attempt, partial(_with_confidence, ask=ask, ask_confidence=ask_confidence, cancellation=cancellation)


def _with_confidence(
    attempt: Attempt, *, ask: Callable[[], Any] | None, ask_confidence: bool, cancellation: Cancellation
) -> Attempt:
    """The Attempt with its confidence evidence.

    The agent is asked only when the Attempt completed and the sweep was not cancelled since.
    """
    if not ask_confidence:
        return attempt
    if ask is None or attempt.execution_status is not ExecutionStatus.COMPLETED:
        return replace(attempt, confidence=ConfidenceUnavailable(reason=ATTEMPT_NOT_COMPLETED))
    if cancellation.requested():
        return replace(attempt, confidence=ConfidenceUnavailable(reason=SWEEP_CANCELLED))
    return replace(attempt, confidence=ask())


def _resource_measurements(
    elapsed: float, reported: Sequence[ResourceMeasurement]
) -> tuple[ResourceMeasurement, ...]:
    """What the agent target reported, with the sweep's own elapsed time unless the target gave one."""
    if any(getattr(measurement, "resource_type", None) is ResourceType.ELAPSED_TIME for measurement in reported):
        return tuple(reported)
    timed = ResourceMeasurement(
        resource_type=ResourceType.ELAPSED_TIME,
        value=elapsed,
        unit=ELAPSED_TIME_UNIT,
        boundary=ELAPSED_TIME_BOUNDARY,
    )
    return (timed, *reported)


def _assess_violations(
    task: Task, request: AttemptRequest, response: AgentResponse
) -> Sequence[ViolationAssessment]:
    if task.violation_assessor is None:
        return ()
    assessments = task.violation_assessor(request, response)
    if (
        not isinstance(assessments, Sequence)
        or isinstance(assessments, str)
        or not all(isinstance(assessment, ViolationAssessment) for assessment in assessments)
    ):
        raise EvidenceError(
            f"the violation assessor of Task {task.task_id!r} must return a sequence of ViolationAssessment, "
            f"each with its evidence and source, got {assessments!r}"
        )
    return assessments


def _recorded_actions(
    declaration: ActionEvidenceDeclaration, request: AttemptRequest, response: AgentResponse
) -> ActionEvidence | None:
    events = declaration.record(request, response)
    if events is None:
        return None
    return ActionEvidence(source=declaration.evidence_source, events=events)

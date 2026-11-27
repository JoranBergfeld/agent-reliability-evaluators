"""The Evaluator sweep's public entry point."""

from __future__ import annotations

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
    VariationKind,
    ViolationAssessment,
    group_by_task,
)
from ..scorecard import AcceptanceCriterion, ScoredMetric, build_scorecard, criteria_by_metric
from .confidence import ATTEMPT_NOT_COMPLETED, elicit_confidence
from .declaration import ActionEvidenceDeclaration, Experiment, Task
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
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` names the reliability metrics to score, by metric identity. Attempts run one
    after another, Task by Task and then experiment by experiment, and scoring happens in
    this process. The result is the machine-readable reliability scorecard, as
    JSON-compatible data.

    `experiments` are controlled experiments: the sweep invokes their customer controls
    around every Attempt and follows their starting-state policy. `tasks` are Tasks given
    without an experiment: nothing prepares or restores their state, and their Attempts
    record no starting-state policy and no condition. The sweep never assumes that the
    agent target resets conversation or external state.

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
    instead. Every other resource measurement comes from the agent target.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that does not return a sourced, binary TaskOutcome stops the sweep. So does a
    violation assessor that does not return sourced violation assessments of the operational
    constraints its Task declares, and an action recording that is not a sequence of ActionEvent.
    """
    evaluators = [_evaluator(metric_id) for metric_id in metrics]
    declared = {"action_types": None if action_evidence is None else action_evidence.mapping}
    for evaluator in evaluators:
        _require_declarations(evaluator, declared)
        _require_compared_variation(evaluator, experiments)
    _require_distinct_tasks([*tasks, *(experiment.task for experiment in experiments)])
    _require_distinct_identities(tasks, experiments)
    # Called for its check: a second criterion for one metric is rejected here, not after the Attempts.
    criteria_by_metric(acceptance_criteria)
    ask_confidence = any(evaluator.needs_confidence for evaluator in evaluators)
    attempts = [
        _run_attempt(task, number, agent_target, action_evidence, ask_confidence, clock)
        for task in tasks
        for number in range(1, task.attempts + 1)
    ]
    experiment_attempts, experiment_records = run_experiments(
        experiments,
        lambda task, number, attempt_id: _invoke_agent(
            task, number, agent_target, action_evidence, ask_confidence, clock, attempt_id=attempt_id
        ),
        ask_confidence,
    )
    attempts.extend(experiment_attempts)
    scored = [
        ScoredMetric(
            result=evaluator.evaluate(
                _evidence_for(evaluator, attempts, experiments),
                *(declared[name] for name in evaluator.declarations),
            ),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
        )
        for evaluator in evaluators
    ]
    return build_scorecard(
        metrics=scored, attempts=attempts, experiments=experiment_records, acceptance_criteria=acceptance_criteria
    )


def _evidence_for(
    evaluator: Evaluator, attempts: Sequence[Attempt], experiments: Sequence[Experiment]
) -> tuple[TaskEvidence, ...]:
    """The Attempts an Evaluator is called with, grouped by Task. Conditions are never mixed silently."""
    kind = evaluator.compares_variation
    if kind is None:
        # Scored from Attempts under the baseline. Attempts under a variation are kept in the
        # scorecard and are not mixed in.
        return group_by_task(
            attempt for attempt in attempts if attempt.condition_role is not ConditionRole.VARIATION
        )
    # The baseline and the variation Attempts of the experiments that declare this kind of variation.
    # A Task is in one experiment only, and it is a Task of the comparison even when no Attempt was made.
    return tuple(
        TaskEvidence(
            experiment.task.task_id,
            tuple(attempt for attempt in attempts if attempt.experiment_id == experiment.experiment_id),
        )
        for experiment in experiments
        if _declares_variation(experiment, kind)
    )


def _declares_variation(experiment: Experiment, kind: VariationKind) -> bool:
    return experiment.variation is not None and experiment.variation.kind is kind


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


def _require_compared_variation(evaluator: Evaluator, experiments: Sequence[Experiment]) -> None:
    kind = evaluator.compares_variation
    if kind is not None and not any(_declares_variation(experiment, kind) for experiment in experiments):
        raise ValueError(
            f"{evaluator.definition.metric.metric_id!r} needs an experiment whose variation declares "
            f"the kind {kind.value!r}: it compares Attempts under that variation with Attempts under the baseline"
        )


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


def _run_attempt(
    task: Task,
    number: int,
    agent_target: AgentTarget,
    action_evidence: ActionEvidenceDeclaration | None,
    ask_confidence: bool,
    clock: Callable[[], float],
) -> Attempt:
    """Make one Attempt of a Task given without an experiment."""
    attempt, follow_up = _invoke_agent(task, number, agent_target, action_evidence, ask_confidence, clock)
    return follow_up(attempt)


def _invoke_agent(
    task: Task,
    number: int,
    agent_target: AgentTarget,
    action_evidence: ActionEvidenceDeclaration | None,
    ask_confidence: bool,
    clock: Callable[[], float],
    attempt_id: str | None = None,
) -> tuple[Attempt, ConfidenceFollowUp]:
    """Invoke the agent for one Attempt and collect the evidence of it, short of its confidence evidence.

    Returns the Attempt and the follow-up that completes it with its confidence evidence.
    The two are separate so that an experiment can verify its condition in between.
    """
    request = AttemptRequest(
        attempt_id=attempt_id or _attempt_id(task, number), task_id=task.task_id, prompt=task.prompt
    )
    started = clock()
    try:
        response = agent_target.invoke(request)
    except Exception as error:
        failed = Attempt(
            attempt_id=request.attempt_id,
            task_id=task.task_id,
            execution_status=ExecutionStatus.FAILED,
            execution_error=f"{type(error).__name__}: {error}",
            operational_constraints=task.operational_constraints,
        )
        return failed, partial(_with_confidence, ask=None, ask_confidence=ask_confidence)
    elapsed = clock() - started
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
        attempt_id=request.attempt_id,
        task_id=task.task_id,
        execution_status=ExecutionStatus.COMPLETED,
        task_outcome=outcome,
        resource_measurements=_resource_measurements(elapsed, response.resource_measurements),
        operational_constraints=task.operational_constraints,
        violation_assessments=violation_assessments,
        action_evidence=actions,
    )
    ask = partial(elicit_confidence, task, request, response, agent_target)
    return attempt, partial(_with_confidence, ask=ask, ask_confidence=ask_confidence)


def _with_confidence(attempt: Attempt, *, ask: Callable[[], Any] | None, ask_confidence: bool) -> Attempt:
    """The Attempt with its confidence evidence. The agent is asked only when the Attempt completed."""
    if not ask_confidence:
        return attempt
    if ask is None or attempt.execution_status is not ExecutionStatus.COMPLETED:
        return replace(attempt, confidence=ConfidenceUnavailable(reason=ATTEMPT_NOT_COMPLETED))
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

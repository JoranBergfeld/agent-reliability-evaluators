"""The Evaluator sweep's public entry point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..agent_target import AgentResponse, AgentTarget, AttemptRequest
from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, MetricResult, ScoringPath
from ..evidence import (
    ActionEvidence,
    Attempt,
    EvidenceError,
    ExecutionStatus,
    TaskEvidence,
    TaskOutcome,
    ViolationAssessment,
    group_by_task,
)
from ..scorecard import AcceptanceCriterion, ScoredMetric, build_scorecard, criteria_by_metric
from .declaration import ActionEvidenceDeclaration, ConfiguredEvaluator, Task
from .reuse import SharedAttempts, share_attempts


def run_sweep(
    *,
    tasks: Sequence[Task],
    agent_target: AgentTarget,
    metrics: Sequence[str | ConfiguredEvaluator],
    action_evidence: ActionEvidenceDeclaration | None = None,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` selects the reliability metrics to score, each by its metric identity or as
    a ConfiguredEvaluator with declarations of its own. Attempts run one after another,
    Task by Task, and scoring happens in this process. The result is the machine-readable
    reliability scorecard, as JSON-compatible data.

    Evaluators are scored from the same Attempts when what they need those Attempts to be
    made with is the same, so that no Attempt is made twice. Where it differs, the Tasks
    are attempted again for the Evaluator that needs something else, after the Attempts
    before it, and Attempts made for different needs are never scored together. The
    scorecard shows which Attempts each Evaluator used.

    `action_evidence` declares where the actions of every Attempt are recorded from and
    what action type each tool name is. The trajectory metrics need it, and it applies to
    every Evaluator that is not configured with its own.

    `acceptance_criteria` are the customer's own, at most one per metric. Each is reported
    with its metric as met, not met, or not assessed with a reason. Without one, a metric
    has no acceptance verdict.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that does not return a sourced, binary TaskOutcome stops the sweep. So does a
    violation assessor that does not return sourced violation assessments of the operational
    constraints its Task declares, and an action recording that is not a sequence of ActionEvent.
    """
    selected = [_select(metric, action_evidence) for metric in metrics]
    _require_one_evaluator_per_metric(selected)
    _require_distinct_tasks(tasks)
    # Called for its check: a second criterion for one metric is rejected here, not after the Attempts.
    criteria_by_metric(acceptance_criteria)
    # With no Evaluator selected the planned Attempts are still made, once.
    sharing = share_attempts((evaluator, evaluator.requirements()) for evaluator in selected) or [SharedAttempts()]
    planned = [
        [(task, _attempt_id(task, set_number, number)) for task in tasks for number in range(1, task.attempts + 1)]
        for set_number in range(1, len(sharing) + 1)
    ]
    _require_distinct_attempts(attempt_id for attempts in planned for _, attempt_id in attempts)
    made = [
        [
            _run_attempt(task, attempt_id, agent_target, _recorded_with(shared, action_evidence))
            for task, attempt_id in attempts
        ]
        for shared, attempts in zip(sharing, planned)
    ]
    scored = [
        ScoredMetric(
            result=evaluator.evaluate(group_by_task(attempts)),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
            attempts_used=tuple(attempt.attempt_id for attempt in attempts),
        )
        for shared, attempts in zip(sharing, made)
        for evaluator in shared.members
    ]
    return build_scorecard(
        metrics=scored,
        attempts=[attempt for attempts in made for attempt in attempts],
        acceptance_criteria=acceptance_criteria,
    )


# What an Evaluator is called with for a declaration it names, given what the customer declared for it.
_EVALUATED_WITH = {"action_types": lambda action_evidence: action_evidence.mapping}


@dataclass(frozen=True)
class _SelectedEvaluator:
    """An Evaluator selected for the sweep, with what the customer declared for it."""

    evaluator: Evaluator
    # What the customer declared for each declaration the Evaluator names.
    declared: dict[str, Any]

    def requirements(self) -> dict[str, Any]:
        """What the Attempts this Evaluator is scored from have to be made with.

        A requirement is named after the evidence it is about, and holds only what decides
        the evidence an Attempt gets. Evaluators share Attempts when their requirements match.
        """
        requirements: dict[str, Any] = {}
        if "action_types" in self.declared:
            # The action types are applied when scoring. They do not change what is recorded.
            action_evidence = self.declared["action_types"]
            requirements["action_evidence"] = (action_evidence.evidence_source, action_evidence.record)
        return requirements

    def evaluate(self, evidence: Sequence[TaskEvidence]) -> MetricResult:
        declared = (_EVALUATED_WITH[name](self.declared[name]) for name in self.evaluator.declarations)
        return self.evaluator.evaluate(evidence, *declared)


def _select(
    metric: str | ConfiguredEvaluator, action_evidence: ActionEvidenceDeclaration | None
) -> _SelectedEvaluator:
    configured = metric if isinstance(metric, ConfiguredEvaluator) else ConfiguredEvaluator(metric_id=metric)
    evaluator = _evaluator(configured.metric_id)
    if configured.action_evidence is not None and "action_types" not in evaluator.declarations:
        raise ValueError(f"{configured.metric_id!r} does not use action evidence, and it is configured with one")
    declared = {"action_types": configured.action_evidence or action_evidence}
    _require_declarations(evaluator, declared)
    return _SelectedEvaluator(evaluator, {name: declared[name] for name in evaluator.declarations})


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


def _require_one_evaluator_per_metric(selected: Sequence[_SelectedEvaluator]) -> None:
    seen: set[str] = set()
    for evaluator in selected:
        metric_id = evaluator.evaluator.definition.metric.metric_id
        if metric_id in seen:
            raise ValueError(f"{metric_id!r} is selected more than once, and a scorecard reports each metric once")
        seen.add(metric_id)


def _require_distinct_attempts(planned: Iterable[str]) -> None:
    seen: set[str] = set()
    for attempt_id in planned:
        if attempt_id in seen:
            raise ValueError(f"more than one Attempt would have the identity {attempt_id!r}")
        seen.add(attempt_id)


def _recorded_with(
    shared: SharedAttempts, action_evidence: ActionEvidenceDeclaration | None
) -> ActionEvidenceDeclaration | None:
    """What the actions of a set of Attempts are recorded with.

    The Evaluators sharing the set that need action evidence all need the same recording,
    so any of their declarations records it. A set that none of them needs action evidence
    from is recorded with what the sweep declares.
    """
    return next(
        (evaluator.declared["action_types"] for evaluator in shared.members if "action_types" in evaluator.declared),
        action_evidence,
    )

def _attempt_id(task: Task, set_number: int, number: int) -> str:
    """The identity of a planned Attempt. The first set of Attempts is identified as it is in a sweep with one set."""
    if set_number == 1:
        return f"{task.task_id}/attempt-{number}"
    return f"{task.task_id}/set-{set_number}/attempt-{number}"

def _require_distinct_tasks(tasks: Sequence[Task]) -> None:
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(f"more than one Task is declared as {task.task_id!r}")
        seen.add(task.task_id)


def _run_attempt(
    task: Task, attempt_id: str, agent_target: AgentTarget, action_evidence: ActionEvidenceDeclaration | None
) -> Attempt:
    request = AttemptRequest(attempt_id=attempt_id, task_id=task.task_id, prompt=task.prompt)
    try:
        response = agent_target.invoke(request)
    except Exception as error:
        return Attempt(
            attempt_id=request.attempt_id,
            task_id=task.task_id,
            execution_status=ExecutionStatus.FAILED,
            execution_error=f"{type(error).__name__}: {error}",
            operational_constraints=task.operational_constraints,
        )
    outcome = task.success_rule(request, response)
    if not isinstance(outcome, TaskOutcome):
        raise EvidenceError(
            f"the task-success rule of Task {task.task_id!r} must return a TaskOutcome with its source, "
            f"got {outcome!r}"
        )
    return Attempt(
        attempt_id=request.attempt_id,
        task_id=task.task_id,
        execution_status=ExecutionStatus.COMPLETED,
        task_outcome=outcome,
        operational_constraints=task.operational_constraints,
        violation_assessments=_assess_violations(task, request, response),
        action_evidence=None if action_evidence is None else _recorded_actions(action_evidence, request, response),
    )


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

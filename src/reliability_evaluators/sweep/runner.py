"""The Evaluator sweep's public entry point."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from ..agent_target import AgentResponse, AgentTarget, AttemptRequest, InvocationFailed
from ..evaluators import EVALUATORS, Evaluator, Fidelity, MetricResult, ScoringPath
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
)
from ..managed_scoring import FoundryManagedScoring, ManagedFailure, has_managed_evaluator
from ..scorecard import (
    AcceptanceCriterion,
    ArtifactLocation,
    CutShort,
    MissingResult,
    PreparationUsage,
    ScoredMetric,
    build_scorecard,
    criteria_by_metric,
)
from .confidence import ATTEMPT_NOT_COMPLETED, SWEEP_CANCELLED, elicit_confidence
from .declaration import ActionEvidenceDeclaration, ConfiguredEvaluator, Experiment, NoTaskOutcomeYet, Task
from .execution import RETRYABLE, Budget, Cancellation, Execution, execution_of
from .lifecycle import ConfidenceFollowUp, planned_attempt_ids, run_experiments
from .reuse import SharedAttempts, share_attempts

ELAPSED_TIME_UNIT = "seconds"
ELAPSED_TIME_BOUNDARY = (
    "wall clock from just before the agent target is invoked until it returns, measured by the Evaluator sweep"
)


def run_sweep(
    *,
    tasks: Sequence[Task] = (),
    experiments: Sequence[Experiment] = (),
    agent_target: AgentTarget,
    metrics: Sequence[str | ConfiguredEvaluator],
    action_evidence: ActionEvidenceDeclaration | None = None,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
    preparation_usage: Sequence[PreparationUsage] = (),
    max_concurrent_attempts: int = 1,
    cancellation: Callable[[], bool] | None = None,
    clock: Callable[[], float] = perf_counter,
    artifact_dir: str | os.PathLike[str] | None = None,
    managed_scoring: FoundryManagedScoring | None = None,
) -> dict[str, Any]:
    """Make the declared Attempts, score the selected metrics, and return the scorecard.

    `metrics` selects the reliability metrics to score, each by its metric identity or as
    a ConfiguredEvaluator with declarations of its own. Scoring happens in this process
    unless an Evaluator is configured for Foundry-managed scoring.
    The result is the machine-readable reliability scorecard, as JSON-compatible data.

    `managed_scoring` is the customer's Foundry project, for the Evaluators configured with
    the scoring path "foundry_managed". Their derived evaluators are registered in that
    project before any Attempt is made, and after the Attempts the project calculates their
    values from the grouped evidence, each Evaluator in a managed run of its own. A value it
    returns is reported as Foundry returned it, attributed to that scoring path, as an
    unverified result. A project that cannot be used stops the sweep with
    ManagedScoringFailure before any Attempt. After the Attempts, an Evaluator whose managed
    run gives no result has none: the scorecard is partial, names the missing result with its
    execution error, and keeps the evidence and the result of every other metric. The sweep
    never scores such an Evaluator in this process instead. `rescore_in_customer_process`
    does, when the customer asks for it, and reports that result beside the managed one.

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

    Evaluators are scored from the same Attempts when what they need those Attempts to be
    made with is the same, so that no Attempt is made twice. Where it differs, every Task
    and experiment is attempted again for the Evaluator that needs something else, after
    the Attempts before it, and Attempts made for different needs are never scored
    together. An Evaluator is given only the Attempts made under the experimental
    conditions it is calculated from. The scorecard shows which Attempts each Evaluator used.

    A metric that compares one kind of variation with the baseline, such as fault
    robustness, is calculated from the Attempts of the experiments whose variation declares
    that kind, under both conditions. At least one experiment has to declare it. Every
    other metric is calculated from Attempts under the baseline only.

    The sweep generates no variation. The customer's apply control puts a fault or another
    presentation of the tools and data in place. Under a prompt variation the agent target
    is given the exact text of each prompt variant the experiment declares, as the prompt
    of the Attempt, `task.attempts` times per variant. Each such Attempt records the variant
    it ran under and is judged by the task-success rule of its Task.

    `action_evidence` declares where the actions of every Attempt are recorded from and
    what action type each tool name is. The trajectory metrics need it, and it applies to
    every Evaluator that is not configured with its own.

    When a metric uses confidence, each completed Attempt made for it is followed by one
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

    A task-success rule that cannot decide during the sweep returns NO_TASK_OUTCOME_YET. The
    Attempt is then recorded without a task outcome, which is not a task failure, and the
    outcome can be supplied later with `recalculate`.

    After the Attempts and the scoring, the scorecard reports under `token_usage` what was
    measured of their token usage: per Attempt, per Evaluator and for the sweep. An Attempt's
    usage is what the agent target recorded as the `token_usage` of its invocation and of the
    confidence request that followed it. Usage that was not reported is missing with a
    reason and is never zero, and Attempts that failed, were cancelled or were retried are
    included. It is metadata: no metric and no acceptance assessment reads it, there is no
    estimate beforehand, and nothing is stopped because of it. `preparation_usage` is what
    the customer reports for a variation they generated with a model, such as the prompt
    variants of an experiment. The sweep generates none, so it reports that usage only when
    it is declared here.

    A declaration that cannot be run is rejected before any Attempt is made. A task-success
    rule that returns anything else than a sourced, binary TaskOutcome stops the sweep. So does a
    violation assessor that does not return sourced violation assessments of the operational
    constraints its Task declares, and an action recording that is not a sequence of ActionEvent.

    With `artifact_dir`, the attempt-level evidence, the scorecard, and the CI job summary
    and the human-readable report rendered from it are also written to that directory. The
    caller chooses it in the run or artifact context of the platform it runs on, and its
    pipeline publishes the summary. The sweep then either delivers all four or raises
    ReportingFailure: whatever stops it, a rejected declaration included, is raised as that,
    and the evidence collected until then is kept at the location.
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
            preparation_usage=preparation_usage,
            max_concurrent_attempts=max_concurrent_attempts,
            cancellation=cancellation,
            clock=clock,
            managed_scoring=managed_scoring,
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
    metrics: Sequence[str | ConfiguredEvaluator],
    action_evidence: ActionEvidenceDeclaration | None,
    acceptance_criteria: Sequence[AcceptanceCriterion],
    preparation_usage: Sequence[PreparationUsage],
    max_concurrent_attempts: int,
    cancellation: Callable[[], bool] | None,
    clock: Callable[[], float],
    managed_scoring: FoundryManagedScoring | None,
) -> dict[str, Any]:
    """Make the Attempts and score them. Each Attempt is added to `attempts` as it is made,
    so that `run_sweep` still has the evidence if this stops.
    """
    selected = [_select(metric, action_evidence) for metric in metrics]
    _require_one_evaluator_per_metric(selected)
    for evaluator in selected:
        _require_compared_variation(evaluator.evaluator, experiments)
    _require_distinct_tasks([*tasks, *(experiment.task for experiment in experiments)])
    # Called for its check: a second criterion for one metric is rejected here, not after the Attempts.
    criteria_by_metric(acceptance_criteria)
    _require_prepared_variations(preparation_usage, experiments)
    if type(max_concurrent_attempts) is not int or max_concurrent_attempts < 1:
        raise ValueError(f"max_concurrent_attempts is a whole number, 1 or more, got {max_concurrent_attempts!r}")
    cancelled = Cancellation(cancellation)
    managed = [evaluator.metric_id for evaluator in selected if evaluator.scoring_path is ScoringPath.FOUNDRY_MANAGED]
    _require_managed_scoring(managed, managed_scoring)
    if managed:
        # Before any Attempt, so that a project that cannot be used costs no Attempts.
        managed_scoring.register(managed)
    # With no Evaluator selected the planned Attempts are still made, once.
    sharing = share_attempts((evaluator, evaluator.requirements()) for evaluator in selected) or [SharedAttempts()]
    planned = [_plan(tasks, experiments, set_number) for set_number in range(1, len(sharing) + 1)]
    _require_distinct_identities(planned)
    made: list[tuple[list[Attempt], list[Execution]]] = []
    # The test resources an experiment did not restore. No later set runs an experiment on them either.
    unrestored: dict[str, str] = {}
    for set_number, (shared, plan) in enumerate(zip(sharing, planned), start=1):
        first = len(attempts)
        ask_confidence = "confidence" in shared.requirements
        make_attempt = partial(
            _invoke_agent,
            agent_target=agent_target,
            action_evidence=_recorded_with(shared, action_evidence),
            ask_confidence=ask_confidence,
            cancellation=cancelled,
            clock=clock,
        )
        # Each set makes the planned Attempts of a Task anew, under the Task's limits anew.
        budgets = {task.task_id: Budget(task, cancelled, clock) for task in tasks}
        at_tasks = _run_planned_attempts(
            [
                partial(_run_planned_attempt, task, attempt_id, make_attempt, budgets[task.task_id], attempts)
                for task, attempt_id in plan.attempts_at_tasks
            ],
            max_concurrent_attempts,
        )
        # In the order planned, whatever the order they finished in.
        attempts[first:] = [attempt for executions in at_tasks for attempt in executions]
        executions = []
        for task in tasks:
            of_task, at_tasks = at_tasks[: task.attempts], at_tasks[task.attempts :]
            executions.append(
                execution_of(
                    task, [made_for for made_for in of_task if made_for], budgets[task.task_id], attempt_set=set_number
                )
            )
        executions += run_experiments(
            plan.experiments,
            make_attempt,
            ask_confidence,
            lambda task: Budget(task, cancelled, clock),
            attempts,
            unrestored,
            set_number,
        )
        made.append((attempts[first:], executions))
    # Each Evaluator with the evidence it is calculated from and where its Attempts were cut short.
    to_score = [
        (
            evaluator,
            evaluator.evidence(attempts_of_set, executions),
            CutShort.of(
                execution.record
                for execution in executions
                if execution.cut_short_for(evaluator.evaluator.compares_variation)
            ),
        )
        for shared, (attempts_of_set, executions) in zip(sharing, made)
        for evaluator in shared.members
    ]
    returned = _score_managed(
        managed_scoring,
        {evaluator.metric_id: evidence for evaluator, evidence, _ in to_score if evaluator.metric_id in managed},
    )
    scored: list[ScoredMetric | MissingResult] = []
    for evaluator, evidence, cut_short in to_score:
        attributed = {
            "attempts_used": tuple(attempt.attempt_id for task in evidence for attempt in task.attempts),
            "cut_short": cut_short,
        }
        # By the path that was declared, never by what came back: nothing falls back to this process.
        if evaluator.scoring_path is ScoringPath.FOUNDRY_MANAGED:
            managed_result = returned[evaluator.metric_id]
            if isinstance(managed_result, ManagedFailure):
                scored.append(
                    MissingResult(
                        definition=evaluator.evaluator.definition,
                        execution_error=managed_result.error,
                        managed_scoring={**managed_result.provenance, "error": managed_result.error},
                        reported_usage=managed_result.reported_usage,
                        **attributed,
                    )
                )
                continue
            scored.append(
                ScoredMetric(
                    result=managed_result.result,
                    scoring_path=ScoringPath.FOUNDRY_MANAGED,
                    # A fidelity rule decides whether a value Foundry returned may be authoritative. There is none yet.
                    fidelity=Fidelity.UNVERIFIED,
                    limitation=managed_result.limitation,
                    managed_scoring=managed_result.provenance,
                    reported_usage=managed_result.reported_usage,
                    **attributed,
                )
            )
        else:
            scored.append(
                ScoredMetric(
                    result=evaluator.evaluate(evidence),
                    scoring_path=ScoringPath.CUSTOMER_PROCESS,
                    fidelity=Fidelity.AUTHORITATIVE,
                    **attributed,
                )
            )
    return build_scorecard(
        metrics=scored,
        attempts=attempts,
        executions=[execution.record for _, executions in made for execution in executions],
        max_concurrent_attempts=max_concurrent_attempts,
        acceptance_criteria=acceptance_criteria,
        preparation_usage=[_in_every_set(preparation, len(planned)) for preparation in preparation_usage],
    )


def _require_prepared_variations(preparation_usage: Sequence[PreparationUsage], experiments: Sequence[Experiment]) -> None:
    """Reject preparation usage that cannot be attributed: it names experiments that declare a variation."""
    declared = {experiment.experiment_id: experiment for experiment in experiments}
    seen: set[str] = set()
    for preparation in preparation_usage:
        if not isinstance(preparation, PreparationUsage):
            raise ValueError(f"preparation usage is declared as PreparationUsage, got {preparation!r}")
        if preparation.preparation_id in seen:
            raise ValueError(f"more than one preparation is declared as {preparation.preparation_id!r}")
        seen.add(preparation.preparation_id)
        for experiment_id in preparation.experiment_ids:
            if experiment_id not in declared:
                raise ValueError(
                    f"the preparation {preparation.preparation_id!r} names the experiment {experiment_id!r}, "
                    "which is not declared"
                )
            if declared[experiment_id].variation is None:
                raise ValueError(
                    f"the preparation {preparation.preparation_id!r} is of the variation of an experiment, and "
                    f"experiment {experiment_id!r} declares no variation"
                )


def _again(set_number: int) -> str:
    """What the identities of a later set of Attempts carry. The first set is identified as in a sweep with one set."""
    return "" if set_number == 1 else f"/set-{set_number}"


def _in_every_set(preparation: PreparationUsage, sets: int) -> PreparationUsage:
    """The preparation, naming its experiments as each set of Attempts identifies them: one preparation serves all."""
    return replace(
        preparation,
        experiment_ids=[
            f"{experiment_id}{_again(set_number)}"
            for set_number in range(1, sets + 1)
            for experiment_id in preparation.experiment_ids
        ],
    )


def _score_managed(
    managed_scoring: FoundryManagedScoring | None, evidence: dict[str, Sequence[TaskEvidence]]
) -> dict[str, Any]:
    """What Foundry-managed scoring gives for each metric on that path: its result, or its failure.

    The Attempts are made by now. Whatever goes wrong here leaves those metrics without a
    result, and never without the evidence or the results of the other metrics.
    """
    if not evidence:
        return {}
    try:
        returned = dict(managed_scoring.score(evidence))
    except Exception as error:
        failure = ManagedFailure(error=f"{type(error).__name__}: {error}")
        return dict.fromkeys(evidence, failure)
    not_returned = ManagedFailure(error="Foundry-managed scoring returned nothing for this metric")
    return {metric_id: returned.get(metric_id, not_returned) for metric_id in evidence}


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


# What an Evaluator is called with for a declaration it names, given what the customer declared for it.
_EVALUATED_WITH = {"action_types": lambda action_evidence: action_evidence.mapping}


@dataclass(frozen=True)
class _SelectedEvaluator:
    """An Evaluator selected for the sweep, with what the customer declared for it."""

    evaluator: Evaluator
    # What the customer declared for each declaration the Evaluator names.
    declared: dict[str, Any]
    # Where the customer declared the calculation to run.
    scoring_path: ScoringPath = ScoringPath.CUSTOMER_PROCESS

    @property
    def metric_id(self) -> str:
        return self.evaluator.definition.metric.metric_id

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
        if self.evaluator.needs_confidence:
            requirements["confidence"] = True
        return requirements

    def evidence(self, attempts: Sequence[Attempt], executions: Sequence[Execution]) -> tuple[TaskEvidence, ...]:
        """The Attempts of one set that this Evaluator is calculated from, grouped by Task.

        `executions` are the Tasks and experiments of that set. Only Attempts made under an
        experimental condition the Evaluator is calculated from are given to it. A Task that
        Attempts were planned for under such a condition is a Task of its evidence even when
        none of them was made.
        """
        kind = self.evaluator.compares_variation
        evidence = []
        for execution in executions:
            roles = execution.conditions_for(kind)
            if roles:
                # An Attempt made outside an experiment had no variation applied to it, so it is
                # used as a baseline Attempt.
                evidence.append(
                    TaskEvidence(
                        execution.task.task_id,
                        tuple(
                            attempt
                            for attempt in attempts
                            if attempt.task_id == execution.task.task_id
                            and (attempt.condition_role or ConditionRole.BASELINE) in roles
                        ),
                    )
                )
        return tuple(evidence)

    def evaluate(self, evidence: Sequence[TaskEvidence]) -> MetricResult:
        declared = (_EVALUATED_WITH[name](self.declared[name]) for name in self.evaluator.declarations)
        return self.evaluator.evaluate(evidence, *declared)


@dataclass(frozen=True)
class _PlannedAttempts:
    """One set of the Attempts the sweep declares: every Task and every experiment, as often as declared."""

    # Each Attempt at a Task given without an experiment, with its identity.
    attempts_at_tasks: tuple[tuple[Task, str], ...]
    experiments: tuple[Experiment, ...]


def _plan(tasks: Sequence[Task], experiments: Sequence[Experiment], set_number: int) -> _PlannedAttempts:
    """Plan one set of Attempts.

    The first set is identified as it is in a sweep with one set. A later set carries its
    number in the identity of its Attempts and of its experiments, which are run again.
    """
    again = _again(set_number)
    return _PlannedAttempts(
        attempts_at_tasks=tuple(
            (task, f"{task.task_id}{again}/attempt-{number}")
            for task in tasks
            for number in range(1, task.attempts + 1)
        ),
        experiments=tuple(
            replace(experiment, experiment_id=f"{experiment.experiment_id}{again}") if again else experiment
            for experiment in experiments
        ),
    )


def _select(
    metric: str | ConfiguredEvaluator, action_evidence: ActionEvidenceDeclaration | None
) -> _SelectedEvaluator:
    configured = metric if isinstance(metric, ConfiguredEvaluator) else ConfiguredEvaluator(metric_id=metric)
    evaluator = _evaluator(configured.metric_id)
    if configured.action_evidence is not None and "action_types" not in evaluator.declarations:
        raise ValueError(f"{configured.metric_id!r} does not use action evidence, and it is configured with one")
    declared = {"action_types": configured.action_evidence or action_evidence}
    _require_declarations(evaluator, declared)
    return _SelectedEvaluator(
        evaluator, {name: declared[name] for name in evaluator.declarations}, configured.scoring_path
    )


def _evaluator(metric_id: str) -> Evaluator:
    # Every one of the twelve reliability metrics has an Evaluator.
    if metric_id in EVALUATORS:
        return EVALUATORS[metric_id]
    raise ValueError(f"{metric_id!r} is not a reliability metric")


def _require_declarations(evaluator: Evaluator, declared: dict[str, Any]) -> None:
    undeclared = {
        "action_types": "action evidence to be declared: the evidence source and the action types have no default",
    }
    for name in evaluator.declarations:
        if declared[name] is None:
            raise ValueError(f"{evaluator.definition.metric.metric_id!r} needs {undeclared[name]}")


def _require_managed_scoring(managed: Sequence[str], managed_scoring: FoundryManagedScoring | None) -> None:
    """Reject a choice of Foundry-managed scoring that cannot be honoured. Nothing falls back to this process."""
    for metric_id in managed:
        if managed_scoring is None:
            raise ValueError(
                f"{metric_id!r} is configured for Foundry-managed scoring, which needs `managed_scoring`: "
                "the Foundry project to score in"
            )
        if not has_managed_evaluator(metric_id):
            raise ValueError(f"{metric_id!r} has no Foundry-managed evaluator in this version")


def _declares_variation(experiment: Experiment, kind: VariationKind) -> bool:
    return experiment.variation is not None and experiment.variation.kind is kind


def _require_compared_variation(evaluator: Evaluator, experiments: Sequence[Experiment]) -> None:
    kind = evaluator.compares_variation
    if kind is not None and not any(_declares_variation(experiment, kind) for experiment in experiments):
        raise ValueError(
            f"{evaluator.definition.metric.metric_id!r} needs an experiment whose variation declares "
            f"the kind {kind.value!r}: it compares Attempts under that variation with Attempts under the baseline"
        )


def _require_one_evaluator_per_metric(selected: Sequence[_SelectedEvaluator]) -> None:
    seen: set[str] = set()
    for evaluator in selected:
        metric_id = evaluator.evaluator.definition.metric.metric_id
        if metric_id in seen:
            raise ValueError(f"{metric_id!r} is selected more than once, and a scorecard reports each metric once")
        seen.add(metric_id)


def _require_distinct_tasks(tasks: Sequence[Task]) -> None:
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(f"more than one Task is declared as {task.task_id!r}")
        seen.add(task.task_id)


def _require_distinct_identities(planned: Sequence[_PlannedAttempts]) -> None:
    """Reject a sweep in which two experiments or two Attempts would be identified alike."""
    experiments: set[str] = set()
    attempts: set[str] = set()
    for set_number, plan in enumerate(planned, start=1):
        for experiment in plan.experiments:
            if experiment.experiment_id in experiments:
                raise ValueError(
                    f"more than one experiment is declared as {experiment.experiment_id!r}"
                    if set_number == 1
                    else f"more than one experiment would have the identity {experiment.experiment_id!r}"
                )
            experiments.add(experiment.experiment_id)
        for attempt_id in (
            *(attempt_id for _, attempt_id in plan.attempts_at_tasks),
            *(attempt_id for experiment in plan.experiments for attempt_id in planned_attempt_ids(experiment)),
        ):
            if attempt_id in attempts:
                raise ValueError(f"more than one Attempt would have the identity {attempt_id!r}")
            attempts.add(attempt_id)


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


def _run_planned_attempt(
    task: Task,
    first: str,
    make_attempt: Callable[..., tuple[Attempt, ConfidenceFollowUp]],
    budget: Budget,
    attempts: list[Attempt],
) -> list[Attempt]:
    """Make one planned Attempt of a Task given without an experiment, and its retries.

    `first` is the identity of the planned Attempt. Every execution is returned, in order,
    and added to `attempts` as it is made. Nothing is returned for a planned Attempt that
    may no longer start.
    """
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

    def not_completed(
        status: ExecutionStatus, why: str, invocation: Mapping[str, Any] | None = None
    ) -> tuple[Attempt, ConfidenceFollowUp]:
        attempt = Attempt(
            attempt_id=attempt_id,
            task_id=task.task_id,
            execution_status=status,
            execution_error=why,
            retry_of=retry_of,
            invocation=invocation,
            operational_constraints=task.operational_constraints,
        )
        # The agent is not asked about an Attempt that did not complete.
        return attempt, partial(_with_confidence, ask=None, ask_confidence=ask_confidence, cancellation=cancellation)

    started = clock()
    try:
        response = agent_target.invoke(request)
    except Exception as error:
        why = f"{type(error).__name__}: {error}"
        # What the agent target recorded of the invocation stays on the Attempt, however it ended.
        recorded = error.invocation if isinstance(error, InvocationFailed) else None
        if isinstance(error, TimeoutError):
            return not_completed(ExecutionStatus.TIMED_OUT, why)
        # The sweep cannot interrupt an invocation. It measures, so that the limit holds
        # whether or not the agent target stopped at it.
        if limit is not None and clock() - started > limit:
            return not_completed(
                ExecutionStatus.TIMED_OUT, f"{why}, after the Attempt's time limit of {limit:g} seconds", recorded
            )
        if cancellation.requested():
            return not_completed(ExecutionStatus.CANCELLED, f"{why}, after the sweep was cancelled", recorded)
        return not_completed(ExecutionStatus.FAILED, why, recorded)
    elapsed = clock() - started
    if limit is not None and elapsed > limit:
        # The limit applies to every Attempt alike, so a late answer is not scored.
        return not_completed(
            ExecutionStatus.TIMED_OUT,
            f"the agent target answered after the Attempt's time limit of {limit:g} seconds",
            response.invocation,
        )
    outcome = task.success_rule(request, response)
    if isinstance(outcome, NoTaskOutcomeYet):
        outcome = None
    elif not isinstance(outcome, TaskOutcome):
        raise EvidenceError(
            f"the task-success rule of Task {task.task_id!r} must return a TaskOutcome with its source, "
            f"or NO_TASK_OUTCOME_YET, got {outcome!r}"
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
        invocation=response.invocation,
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
    confidence, recorded = ask()
    return replace(attempt, confidence=confidence, confidence_invocation=recorded)


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

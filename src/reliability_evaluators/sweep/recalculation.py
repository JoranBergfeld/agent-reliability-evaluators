"""Recalculation of an earlier scorecard with task outcomes supplied or corrected after the run.

The offering keeps no evidence store. The customer supplies the earlier scorecard, which
holds the attempt-level evidence, from the platform that kept it. Nothing here makes an
Attempt, invokes an agent or reads a file, and an earlier run is not modified. A platform
is called for one thing only: a metric that Foundry-managed scoring scored is scored again
in the customer's Foundry project, when the customer supplies that project.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from math import isclose
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..evaluators import RELIABILITY_METRICS, MetricState, ScoringPath
from ..evidence import (
    Attempt,
    EvidenceError,
    OutcomeCorrection,
    TaskEvidence,
    apply_outcome_corrections,
    read_attempts,
)
from ..managed_scoring import (
    DerivationError,
    FoundryManagedScoring,
    ManagedFailure,
    has_managed_evaluator,
    submitted_evidence_sha256,
)
from ..scorecard import (
    NOT_RECALCULATED,
    SCHEMA_VERSION,
    TOKEN_USAGE,
    AcceptanceCriterion,
    CarriedMetric,
    CutShort,
    ExecutionRecord,
    MissingResult,
    PreparationUsage,
    ScorecardStatus,
    ScoredMetric,
    build_scorecard,
    criteria_by_metric,
    read_execution_record,
    read_preparation_usage,
)
from .declaration import ActionEvidenceDeclaration, ConfiguredEvaluator, ExecutionLimits
from .runner import as_foundry_returned, score_in_foundry, scored_in_this_process, select_evaluator, SelectedEvaluator

SUPERSEDED = "superseded"

# What a scorecard holds besides its schema version. A recalculation reads each of these, and
# refuses a scorecard that holds anything else: a part the scorecard gains is added here
# together with what a recalculated scorecard does with it, so that it is never left out silently.
_METRICS, _EXPERIMENTS, _ATTEMPTS, _SUPERSEDED_RESULTS = "metrics", "experiments", "attempts", "superseded_results"
_STATUS, _MISSING, _CONCURRENCY, _TASKS = "status", "missing", "concurrency", "tasks_without_experiment"
# How the sweep was executed, and what that left missing. A correction concerns none of it:
# these parts are read, have to be what the supplied evidence gives, and are carried over.
# So is what the sweep used: a recalculation makes no Attempt and scores by code, so it uses no tokens.
_EXECUTION_RECORD = (_STATUS, _MISSING, _CONCURRENCY, _EXPERIMENTS, _TASKS, TOKEN_USAGE)
_READ = {"schema_version", _METRICS, _ATTEMPTS, _SUPERSEDED_RESULTS, *_EXECUTION_RECORD}


class RecalculationFailed(Exception):
    """The recalculation did not produce a scorecard, so nothing was superseded.

    The earlier scorecard stands as it was. The message says what stopped the recalculation.
    """


def recalculate(
    *,
    earlier_scorecard: Mapping[str, Any],
    outcome_corrections: Sequence[OutcomeCorrection],
    metrics: Sequence[str | ConfiguredEvaluator] = (),
    action_evidence: ActionEvidenceDeclaration | None = None,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
    managed_scoring: FoundryManagedScoring | None = None,
) -> dict[str, Any]:
    """Score the evidence of an earlier scorecard again, with task outcomes supplied or corrected since.

    `earlier_scorecard` is the scorecard of the earlier sweep or recalculation, as the data
    of its JSON. It holds the attempt-level evidence, so nothing else is needed: the
    offering keeps no evidence store, and the customer supplies it from their platform.
    It is read and not changed.

    `outcome_corrections` gives, by Attempt identity, the task outcome of each Attempt that
    is supplied late or corrected, with its source and its provenance.

    The result is a new scorecard. Every metric the earlier scorecard scored in the
    customer's own process is scored again by the same Evaluator, in this process, from the
    Attempts its Evaluator was given before. A correction changes the task outcome of the Attempt it names and nothing else:
    the confidence rating, the action evidence, the resource measurements, the violation
    assessments and what the Attempt records about its experiment stay as they were. So a
    metric that is not calculated from task outcomes keeps its result. Each corrected
    Attempt keeps the record that was replaced, in `superseded_task_outcomes`, with the
    provenance of the correction. The earlier results are kept in `superseded_results`,
    marked as superseded, with the corrections that replaced them.

    A recalculation makes no Attempt, so how the sweep was executed stays as it was: the
    status of the scorecard, what it is missing, the execution limits and the record of
    what was planned and made are carried over, and a partial scorecard stays partial. So
    is the token usage: it is what the sweep used, the recalculation scores by code and
    uses none, and what the customer declared for preparation is kept as it was reported. A
    retried Attempt and its retry stay two Attempts. A metric that had no result because
    its Attempts were cut short still has none: its entry is carried over as it was and is
    not scored from the corrected outcomes. Running the sweep again is what gives it one.

    A metric that Foundry-managed scoring scored is never scored in this process instead.
    Each metric stays on the scoring path the earlier scorecard attributes it to:

    - With `managed_scoring`, the customer's Foundry project, Foundry scores it again from
      the corrected evidence, in managed runs of its own as in a sweep, and the new result is
      reported as Foundry returned it. When Foundry gives no result for it the recalculation
      fails, and nothing is superseded.
    - Without `managed_scoring`, its entry is carried into the new scorecard as it was, and
      marked under `not_recalculated` with the Attempts whose corrected task outcomes it does
      not rest on. It is then not the result for the corrected outcomes, and an acceptance
      criterion is not assessed on it. It stays marked in later recalculations until Foundry
      scores it again.
    - Either way, an entry that the corrections do not touch is carried as it is, and is
      current: the evidence Foundry was sent for it is the same with and without them. That
      is so for a metric that is not calculated from task outcomes, and for one that was not
      given a corrected Attempt.

    `metrics`, `action_evidence` and `acceptance_criteria` are declared again as for the
    sweep, because the earlier scorecard cannot hold the customer's code. `action_evidence`
    is needed when a trajectory metric was scored, and `metrics` only for an Evaluator
    that was configured with declarations of its own. A metric that the earlier scorecard
    did not score cannot be added. Acceptance criteria are assessed on the new results.

    Raises RecalculationFailed, and returns no scorecard, when the recalculation cannot be
    done as asked. That includes: evidence that does not validate, or that does not give
    the earlier results when it is scored as it is; a correction for an Attempt that is not
    in the evidence, that matches more than one Attempt, or whose execution did not
    complete, which includes one that timed out, answered after its time limit, was
    cancelled or was retried; two corrections for one Attempt; no correction at all; a
    missing declaration; a metric that is declared for another scoring path than the one
    the earlier scorecard attributes it to; evidence that is not the evidence Foundry was
    sent for a Foundry-managed metric; a Foundry project that gives no result;
    and an acceptance criterion of the earlier scorecard that is not declared again.
    """
    try:
        return _recalculate(
            earlier_scorecard, outcome_corrections, metrics, action_evidence, acceptance_criteria, managed_scoring
        )
    except Exception as error:
        # A ValueError is one of this package's own refusals and reads as a sentence.
        reason = str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: {error}"
        raise RecalculationFailed(f"recalculation failed: {reason}") from error


def _recalculate(
    earlier_scorecard: Mapping[str, Any],
    outcome_corrections: Sequence[OutcomeCorrection],
    metrics: Sequence[str | ConfiguredEvaluator],
    action_evidence: ActionEvidenceDeclaration | None,
    acceptance_criteria: Sequence[AcceptanceCriterion],
    managed_scoring: FoundryManagedScoring | None,
) -> dict[str, Any]:
    corrections = _given(outcome_corrections)
    with validating_the_supplied_evidence():
        earlier = read_earlier_scorecard(earlier_scorecard)
        scored_earlier = _scored_earlier(earlier)
    evaluators = _selected(scored_earlier, metrics, action_evidence)
    with validating_the_supplied_evidence():
        _require_reproduced(earlier, _scorecard(earlier, _score(evaluators, earlier, earlier.attempts)))
    _require_criteria_declared_again(earlier.metrics, acceptance_criteria)
    attempts = apply_outcome_corrections(earlier.attempts, corrections)
    superseded = {
        "status": SUPERSEDED,
        "outcome_corrections": _correction_records(corrections, earlier.attempts),
        "metrics": deepcopy(earlier.metrics),
    }
    corrected = tuple(correction.attempt_id for correction in corrections)
    scored = _score(evaluators, earlier, attempts, corrected, managed_scoring)
    scorecard = _scorecard(earlier, scored, attempts, acceptance_criteria)
    return {**scorecard, _SUPERSEDED_RESULTS: [*deepcopy(earlier.superseded_results), superseded]}


def _scorecard(
    earlier: EarlierScorecard,
    scored: Sequence[ScoredMetric | MissingResult | CarriedMetric],
    attempts: Sequence[Attempt] | None = None,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
) -> dict[str, Any]:
    """The scorecard of these results, with the execution of the earlier sweep as the earlier scorecard records it."""
    return build_scorecard(
        metrics=scored,
        attempts=earlier.attempts if attempts is None else attempts,
        executions=earlier.executions,
        max_concurrent_attempts=earlier.max_concurrent_attempts,
        acceptance_criteria=acceptance_criteria,
        preparation_usage=earlier.preparation_usage,
    )


def _given(outcome_corrections: Any) -> tuple[Any, ...]:
    """The corrections a recalculation is given, of which there is at least one."""
    if isinstance(outcome_corrections, (str, Mapping)) or not isinstance(outcome_corrections, Iterable):
        raise ValueError(f"task outcomes are supplied as a sequence of OutcomeCorrection. Got {outcome_corrections!r}")
    corrections = tuple(outcome_corrections)
    if not corrections:
        raise ValueError(
            "no task outcome was supplied or corrected, so there is nothing to recalculate: "
            "a recalculation needs at least one OutcomeCorrection"
        )
    return corrections


@contextmanager
def validating_the_supplied_evidence() -> Iterator[None]:
    try:
        yield
    except EvidenceError as error:
        raise EvidenceError(f"the supplied evidence does not validate: {error}") from error


@dataclass(frozen=True)
class EarlierScorecard:
    """What the scorecard the customer supplied holds."""

    # The scorecard as it was supplied, for what is compared with it part by part.
    supplied: Mapping[str, Any]
    metrics: list[Any]
    experiments: list[Any]
    attempts: tuple[Attempt, ...]
    # What was planned and made for each experiment and for each Task given without one.
    executions: tuple[ExecutionRecord, ...]
    max_concurrent_attempts: int
    # The results it had itself superseded, oldest first.
    superseded_results: list[Any]
    # What the customer reported for preparing the variation of experiments, which no evidence gives again.
    preparation_usage: tuple[PreparationUsage, ...]


def read_earlier_scorecard(earlier_scorecard: Any) -> EarlierScorecard:
    if not isinstance(earlier_scorecard, Mapping):
        raise EvidenceError(
            "the earlier scorecard is supplied as the data of its JSON, with the attempt-level evidence in it. "
            f"Got {earlier_scorecard!r}"
        )
    version = earlier_scorecard.get("schema_version")
    if version != SCHEMA_VERSION:
        raise EvidenceError(
            f"it has schema version {version!r}, and this version reads schema version {SCHEMA_VERSION}"
        )
    # A part this version does not know cannot be carried into the new scorecard, and is not left out silently.
    unread = sorted(set(earlier_scorecard) - _READ)
    if unread:
        raise EvidenceError(f"it holds what this version does not recalculate with: {', '.join(unread)}")
    if not isinstance(earlier_scorecard.get(_ATTEMPTS), list):
        raise EvidenceError("it has no attempt-level evidence: 'attempts' is not a list of Attempts")
    if not isinstance(earlier_scorecard.get(_METRICS), list):
        raise EvidenceError(
            "it has no metric results to supersede. Supply the earlier scorecard, which holds the "
            "attempt-level evidence as well, and not the evidence alone"
        )
    experiments = earlier_scorecard.get(_EXPERIMENTS)
    if not isinstance(experiments, list):
        raise EvidenceError(f"its experiments are not a list. Got {experiments!r}")
    superseded_results = earlier_scorecard.get(_SUPERSEDED_RESULTS, [])
    if not isinstance(superseded_results, list):
        raise EvidenceError(f"its superseded results are not a list. Got {superseded_results!r}")
    status = earlier_scorecard.get(_STATUS)
    if status not in (ScorecardStatus.COMPLETE.value, ScorecardStatus.PARTIAL.value):
        raise EvidenceError(f"it records no status of a scorecard, complete or partial. Got {status!r}")
    if not isinstance(earlier_scorecard.get(_MISSING), list):
        raise EvidenceError(f"what it is missing is not a list. Got {earlier_scorecard.get(_MISSING)!r}")
    tasks = earlier_scorecard.get(_TASKS)
    if not isinstance(tasks, list):
        raise EvidenceError(f"its {_TASKS} are not a list. Got {tasks!r}")
    concurrency = earlier_scorecard.get(_CONCURRENCY)
    allowed = concurrency.get("max_concurrent_attempts") if isinstance(concurrency, Mapping) else None
    if type(allowed) is not int or allowed < 1:
        raise EvidenceError(
            f"its concurrency does not record how many Attempts were allowed in flight at once. Got {concurrency!r}"
        )
    for experiment in experiments:
        _recorded_experiment(experiment)
    token_usage = earlier_scorecard.get(TOKEN_USAGE)
    if not isinstance(token_usage, Mapping):
        raise EvidenceError(f"it does not report its token usage. Got {token_usage!r}")
    try:
        preparation_usage = read_preparation_usage(token_usage.get("shared_preparation"))
    except ValueError as error:
        raise EvidenceError(f"its token usage is not what this version reports: {error}") from error
    return EarlierScorecard(
        supplied=earlier_scorecard,
        metrics=earlier_scorecard[_METRICS],
        experiments=experiments,
        attempts=read_attempts(earlier_scorecard[_ATTEMPTS]),
        # In the order the scorecard is assembled in: every set's Tasks and experiments.
        executions=tuple(
            sorted(
                (*map(_execution_of_a_task, tasks), *map(_execution_of_an_experiment, experiments)),
                key=lambda record: record.attempt_set,
            )
        ),
        max_concurrent_attempts=allowed,
        superseded_results=superseded_results,
        preparation_usage=preparation_usage,
    )


def _execution_of_a_task(recorded: Any) -> ExecutionRecord:
    return _with_readable_limits(read_execution_record(recorded, of_experiment=False), "Task")


def _execution_of_an_experiment(recorded: Any) -> ExecutionRecord:
    return _with_readable_limits(read_execution_record(recorded, of_experiment=True), "experiment")


def _with_readable_limits(record: ExecutionRecord, of: str) -> ExecutionRecord:
    """The record, whose limits have to be execution limits as a Task declares them."""
    named = record.task_id if record.experiment_id is None else record.experiment_id
    try:
        declared = ExecutionLimits(**record.limits).to_dict()
    except (TypeError, ValueError) as error:
        raise EvidenceError(f"the execution record of {of} {named!r} has limits that are not execution limits: {error}")
    if declared != record.limits:
        raise EvidenceError(f"the execution record of {of} {named!r} is not what this version records for it")
    return record


@dataclass(frozen=True)
class _ScoredEarlier:
    """A metric the earlier scorecard scored, and the Attempts its Evaluator was given."""

    metric_id: str
    attempts_used: tuple[Attempt, ...]
    # Where the sweep cut the Attempts for this metric short, which no recalculation changes.
    cut_short: CutShort
    # Whether that left the metric without a result.
    had_execution_failure: bool
    # The scoring path the earlier scorecard attributes the metric to, and its entry as recorded.
    scoring_path: ScoringPath
    entry: Mapping[str, Any]


def _scored_earlier(earlier: EarlierScorecard) -> list[_ScoredEarlier]:
    """The metrics the earlier scorecard scored, which are the ones scored again.

    Each is scored again from the Attempts its Evaluator was given by the sweep, which the
    earlier scorecard names. Those are matched by Attempt identity, never by position.
    """
    known = {metric.metric_id for metric in RELIABILITY_METRICS}
    by_identity = {attempt.attempt_id: attempt for attempt in earlier.attempts}
    seen: set[str] = set()
    scored = []
    for entry in earlier.metrics:
        if not isinstance(entry, Mapping):
            raise EvidenceError(f"a metric result is recorded as an object. Got {entry!r}")
        metric_id = entry.get("metric_id")
        if metric_id not in known:
            raise EvidenceError(f"{metric_id!r} is not a reliability metric")
        if metric_id in seen:
            raise EvidenceError(f"it has more than one result for {metric_id!r}")
        seen.add(metric_id)
        if "state" not in entry:
            raise EvidenceError(f"the result of {metric_id!r} is recorded without its state")
        if entry["state"] == MetricState.NOT_CONFIGURED.value:
            continue
        try:
            scoring_path = ScoringPath(entry.get("scoring_path") or ScoringPath.CUSTOMER_PROCESS.value)
        except ValueError:
            raise EvidenceError(
                f"the result of {metric_id!r} is attributed to {entry.get('scoring_path')!r}, which is no scoring path"
            ) from None
        used = entry.get("attempts_used")
        attempt_ids = used.get("attempt_ids") if isinstance(used, Mapping) else None
        if not isinstance(attempt_ids, list):
            raise EvidenceError(f"the result of {metric_id!r} is recorded without the Attempts its Evaluator used")
        for attempt_id in attempt_ids:
            # An identity that is not text cannot be looked up, and is not in the evidence either.
            if not isinstance(attempt_id, str) or attempt_id not in by_identity:
                raise EvidenceError(
                    f"the Evaluator of {metric_id!r} used Attempt {attempt_id!r}, "
                    "which is not in the attempt-level evidence"
                )
        scored.append(
            _ScoredEarlier(
                metric_id,
                tuple(by_identity[attempt_id] for attempt_id in attempt_ids),
                cut_short_of(entry, earlier.executions),
                entry.get("execution_failure") is not None,
                scoring_path,
                entry,
            )
        )
    return scored


def cut_short_of(entry: Mapping[str, Any], executions: Sequence[ExecutionRecord]) -> CutShort:
    """Where the earlier scorecard says the Attempts for a metric were cut short."""
    metric_id = entry["metric_id"]
    recorded = entry.get("cut_short")
    named = [recorded.get(key) for key in ("experiment_ids", "task_ids")] if isinstance(recorded, Mapping) else []
    if not named or not all(isinstance(ids, list) and all(isinstance(one, str) for one in ids) for ids in named):
        raise EvidenceError(f"the result of {metric_id!r} is recorded without where its Attempts were cut short")
    experiment_ids, task_ids = named
    unknown = [
        *(one for one in experiment_ids if one not in {record.experiment_id for record in executions}),
        *(
            one
            for one in task_ids
            if one not in {record.task_id for record in executions if record.experiment_id is None}
        ),
    ]
    if unknown:
        raise EvidenceError(
            f"the result of {metric_id!r} says its Attempts were cut short in "
            f"{', '.join(map(repr, unknown))}, of which the scorecard has no execution record"
        )
    return CutShort(tuple(experiment_ids), tuple(task_ids))


@dataclass(frozen=True)
class EarlierEvaluation:
    """An Evaluator with what the customer declared for it, and the identities of the Attempts it is given."""

    evaluator: SelectedEvaluator
    attempt_ids: tuple[str, ...]
    cut_short: CutShort
    # The metric had no result because its Attempts were cut short. A recalculation gives it none.
    had_execution_failure: bool
    # The entry of a metric that Foundry-managed scoring scored, as the earlier scorecard records it.
    # None: the metric was scored in the customer's own process.
    managed_entry: Mapping[str, Any] | None = None


def _selected(
    scored_earlier: Sequence[_ScoredEarlier],
    metrics: Sequence[str | ConfiguredEvaluator],
    action_evidence: ActionEvidenceDeclaration | None,
) -> list[EarlierEvaluation]:
    """The Evaluators of the metrics the earlier scorecard scored, selected as the sweep selects them."""
    configured: dict[str, str | ConfiguredEvaluator] = {}
    for metric in metrics:
        metric_id = metric.metric_id if isinstance(metric, ConfiguredEvaluator) else metric
        if metric_id in configured:
            raise ValueError(f"{metric_id!r} is selected more than once, and a scorecard reports each metric once")
        configured[metric_id] = metric
    scored_ids = [scored.metric_id for scored in scored_earlier]
    for metric_id in configured:
        if metric_id not in scored_ids:
            raise ValueError(
                f"{metric_id!r} was not scored in the earlier scorecard. A recalculation scores the metrics "
                "the earlier scorecard scored, and no other"
            )
    for scored in scored_earlier:
        declared = configured.get(scored.metric_id)
        # Declared by its identity alone, a metric stays on the scoring path the earlier scorecard records.
        if isinstance(declared, ConfiguredEvaluator) and declared.scoring_path is not scored.scoring_path:
            raise ValueError(
                f"{scored.metric_id!r} is configured for the scoring path {declared.scoring_path.value!r}, and "
                f"the earlier scorecard attributes it to {scored.scoring_path.value!r}. A result is not scored "
                "again on another scoring path than the one that was chosen for it"
            )
    return [
        EarlierEvaluation(
            select_evaluator(configured.get(scored.metric_id, scored.metric_id), action_evidence),
            tuple(attempt.attempt_id for attempt in scored.attempts_used),
            scored.cut_short,
            scored.had_execution_failure,
            scored.entry if scored.scoring_path is ScoringPath.FOUNDRY_MANAGED else None,
        )
        for scored in scored_earlier
    ]


def _score(
    evaluators: Sequence[EarlierEvaluation],
    earlier: EarlierScorecard,
    attempts: Sequence[Attempt],
    corrected: Sequence[str] = (),
    managed_scoring: FoundryManagedScoring | None = None,
) -> list[ScoredMetric | MissingResult | CarriedMetric]:
    """Score each Evaluator from the Attempts it is given, taken from `attempts` by identity.

    A metric that the earlier sweep left without a result, because its Attempts were cut
    short, is scored from the earlier evidence as it was: an execution failure is not made
    into a result by a task outcome that is supplied or corrected afterwards.

    A metric that Foundry-managed scoring scored is never scored here. Its entry is carried,
    or Foundry scores it again: see `_carried`. `corrected` names the Attempts whose task
    outcomes `attempts` holds corrected.
    """
    by_identity = {attempt.attempt_id: attempt for attempt in attempts}
    as_it_was = {attempt.attempt_id: attempt for attempt in earlier.attempts}
    scored: list[ScoredMetric | MissingResult | CarriedMetric] = []
    for_foundry: dict[str, tuple[EarlierEvaluation, tuple[TaskEvidence, ...]]] = {}
    for evaluation in evaluators:
        if evaluation.managed_entry is not None:
            evidence = evidence_given(evaluation, [by_identity[one] for one in evaluation.attempt_ids], earlier)
            carried = _carried(evaluation, earlier, evidence, corrected, asked_again=managed_scoring is not None)
            if carried is None:
                for_foundry[evaluation.evaluator.metric_id] = (evaluation, evidence)
            else:
                scored.append(carried)
            continue
        given = as_it_was if evaluation.had_execution_failure else by_identity
        evidence = evidence_given(evaluation, [given[attempt_id] for attempt_id in evaluation.attempt_ids], earlier)
        scored.append(
            scored_in_this_process(
                evaluation.evaluator, evidence, attempts_used=evaluation.attempt_ids, cut_short=evaluation.cut_short
            )
        )
    return [*scored, *_scored_again_in_foundry(managed_scoring, for_foundry)]


def _carried(
    evaluation: EarlierEvaluation,
    earlier: EarlierScorecard,
    evidence: Sequence[TaskEvidence],
    corrected: Sequence[str],
    *,
    asked_again: bool,
) -> CarriedMetric | None:
    """The entry of a Foundry-managed metric as the new scorecard carries it, or None when Foundry scores it again.

    `evidence` is what its Evaluator would be given now. The entry is out of date when that
    is not what Foundry was sent for it, or when an earlier recalculation already marked it
    so. Foundry is asked again, when the customer supplied their project, for an entry that
    is out of date and for one that Foundry gave no result for. A metric whose Attempts were
    cut short is never scored again, in Foundry either.
    """
    entry, metric_id = evaluation.managed_entry, evaluation.evaluator.metric_id
    usage = usage_of_the_evaluator(earlier.supplied, metric_id)
    marked = entry.get(NOT_RECALCULATED)
    already = tuple(marked.get("corrected_attempt_ids") or ()) if isinstance(marked, Mapping) else ()
    declared = evaluation.evaluator.evaluated_with()
    as_it_was = {attempt.attempt_id: attempt for attempt in earlier.attempts}
    before = evidence_given(evaluation, [as_it_was[one] for one in evaluation.attempt_ids], earlier)
    # An entry that is already out of date records the digest of evidence that was corrected since.
    submitted = submitted_evidence(dict(entry, managed_scoring=None) if already else entry, before, declared)
    touched = submitted_evidence({"metric_id": metric_id}, evidence, declared) != submitted
    out_of_date = (
        *already,
        *(one for one in evaluation.attempt_ids if touched and one in corrected and one not in already),
    )
    failure = entry.get("execution_failure")
    cut_short = isinstance(failure, Mapping) and bool(failure.get("experiment_ids") or failure.get("task_ids"))
    if asked_again and not cut_short and (out_of_date or failure is not None):
        return None
    return CarriedMetric(entry=entry, usage=usage, corrected_attempt_ids=out_of_date)


def _scored_again_in_foundry(
    managed_scoring: FoundryManagedScoring | None,
    for_foundry: Mapping[str, tuple[EarlierEvaluation, tuple[TaskEvidence, ...]]],
) -> list[ScoredMetric | MissingResult]:
    """What Foundry returns for each metric it is asked to score again, attributed to Foundry-managed scoring.

    A metric that Foundry gives no result for fails the recalculation: nothing is scored in
    this process in its place, and the earlier scorecard stands.
    """
    if not for_foundry:
        return []
    for metric_id in for_foundry:
        if not has_managed_evaluator(metric_id):
            raise ValueError(f"{metric_id!r} has no Foundry-managed evaluator in this version")
    managed_scoring.register(list(for_foundry))
    returned = score_in_foundry(
        managed_scoring,
        {metric_id: evidence for metric_id, (_, evidence) in for_foundry.items()},
        {metric_id: evaluation.evaluator.evaluated_with() for metric_id, (evaluation, _) in for_foundry.items()},
    )
    scored = []
    for metric_id, (evaluation, _) in for_foundry.items():
        if isinstance(returned[metric_id], ManagedFailure):
            raise ValueError(
                f"Foundry-managed scoring gave no result for {metric_id!r}, and a recalculation does not score "
                f"it in the customer's own process instead: {returned[metric_id].error}"
            )
        scored.append(
            as_foundry_returned(
                evaluation.evaluator,
                returned[metric_id],
                attempts_used=evaluation.attempt_ids,
                cut_short=evaluation.cut_short,
            )
        )
    return scored


def usage_of_the_evaluator(scorecard: Mapping[str, Any], metric_id: str) -> dict[str, Any]:
    """Where a scorecard reports the token usage of the Evaluator of one metric."""
    usage = scorecard.get(TOKEN_USAGE)
    evaluators = usage.get("evaluators") if isinstance(usage, Mapping) else None
    reported = [
        entry
        for entry in (evaluators if isinstance(evaluators, list) else [])
        if isinstance(entry, dict) and entry.get("metric_id") == metric_id
    ]
    if len(reported) != 1 or not isinstance(reported[0].get("scoring"), list):
        raise EvidenceError(f"it does not report the token usage of the Evaluator of {metric_id!r} once")
    return reported[0]


def submitted_evidence(entry: Mapping[str, Any], evidence: Sequence[Any], declared: Sequence[Any]) -> str | None:
    """The digest of the evidence a metric is scored from, which has to be the digest of what was submitted.

    The digest is the one the evidence travels to Foundry with, so the two are the same
    exactly when the evidence is what the managed evaluator was sent. `declared` is what the
    customer declares for the Evaluator, such as the action types. It is submitted with the
    evidence, so evidence under another declaration is refused too. An entry that records
    no digest has nothing to be compared with. None when this version derives no Foundry
    evaluator for the metric and nothing was recorded as submitted.
    """
    metric_id = entry["metric_id"]
    managed = entry.get("managed_scoring")
    submitted = managed.get("evidence_sha256") if isinstance(managed, Mapping) else None
    try:
        digest = submitted_evidence_sha256(metric_id, evidence, *declared)
    except DerivationError as error:
        if submitted is None:
            return None
        raise ValueError(
            f"the supplied evidence cannot be compared with the evidence that was submitted to Foundry "
            f"for {metric_id!r}: {error}"
        ) from error
    if submitted is not None and submitted != digest:
        raise EvidenceError(
            f"the supplied evidence is not the evidence that was submitted to Foundry for {metric_id!r}: it has "
            f"the digest {digest}, and {submitted} was submitted. Foundry-managed scoring is given the same "
            "evidence, with what the customer declared for the Evaluator as it was declared for the sweep. "
            "Use the scorecard as the sweep wrote it, and the commit or tag that made it"
        )
    return digest


def evidence_given(
    evaluation: EarlierEvaluation, attempts: Sequence[Attempt], earlier: EarlierScorecard
) -> tuple[TaskEvidence, ...]:
    """The Attempts an Evaluator is given, grouped by Task as the sweep grouped them for it.

    A Task that Attempts were planned for is a Task of the evidence also when none of them
    was made. An Evaluator that is calculated from the baseline was given every Task of the
    sweep. One that compares a kind of variation with the baseline was given the Task of
    every experiment whose variation is of that kind.
    """
    kind = evaluation.evaluator.evaluator.compares_variation
    if kind is None:
        # Every set of Attempts is made with the same Tasks and experiments, so each Task is named once.
        by_task: dict[str, list[Attempt]] = {record.task_id: [] for record in earlier.executions}
        for attempt in attempts:
            if attempt.task_id not in by_task:
                raise EvidenceError(
                    f"Attempt {attempt.attempt_id!r} was made at Task {attempt.task_id!r}, "
                    "of which the scorecard has no execution record"
                )
            by_task[attempt.task_id].append(attempt)
        return tuple(TaskEvidence(task_id, tuple(members)) for task_id, members in by_task.items())
    # Every set of Attempts is made with the same experiments, so the Tasks compared are the same
    # whichever set the Evaluator was given, and each is named once.
    task_of = {
        experiment_id: task_id
        for experiment_id, task_id, variation_kind in map(_recorded_experiment, earlier.experiments)
        if variation_kind == kind.value
    }
    by_task = {task_id: [] for task_id in task_of.values()}
    for attempt in attempts:
        if attempt.experiment_id not in task_of:
            raise EvidenceError(
                f"the Evaluator of {evaluation.evaluator.evaluator.definition.metric.metric_id!r} used Attempt "
                f"{attempt.attempt_id!r}, which was not made in an experiment whose variation is of the kind "
                f"{kind.value!r}"
            )
        by_task[task_of[attempt.experiment_id]].append(attempt)
    return tuple(TaskEvidence(task_id, tuple(members)) for task_id, members in by_task.items())


def _recorded_experiment(recorded: Any) -> tuple[str, str, Any]:
    """The identity of a recorded experiment, its Task, and the kind of its variation if it declares one."""
    variation = recorded.get("variation") if isinstance(recorded, Mapping) else None
    if (
        not isinstance(recorded, Mapping)
        or not isinstance(recorded.get("experiment_id"), str)
        or not isinstance(recorded.get("task_id"), str)
        or not isinstance(variation, (Mapping, type(None)))
    ):
        raise EvidenceError(
            "an experiment is recorded as an object with its experiment_id, its task_id and its variation. "
            f"Got {recorded!r}"
        )
    return recorded["experiment_id"], recorded["task_id"], None if variation is None else variation.get("kind")


def _require_reproduced(earlier: EarlierScorecard, reproduced: Mapping[str, Any]) -> None:
    """Require that the supplied evidence gives the earlier scorecard when it is scored as it is.

    `reproduced` is the scorecard of the supplied evidence, scored without any correction.
    Otherwise the new results would differ from the superseded ones for more than the
    corrected task outcomes: the evidence is not the evidence behind the earlier scorecard,
    or the results were calculated with other declarations or another metric definition.
    The same holds for how the sweep was executed and what that left missing.
    """
    recorded = {entry["metric_id"]: entry for entry in earlier.metrics}
    for calculated in reproduced[_METRICS]:
        entry = recorded[calculated["metric_id"]]
        # What a recalculation is given again, and what follows from it, is not compared.
        parts = [part for part in calculated if part not in ("acceptance", "attempts_used", "scoring_path", "fidelity")]
        differing = [part for part in parts if part not in entry or not _same(calculated[part], entry[part])]
        if differing:
            raise EvidenceError(
                f"it does not reproduce the earlier result of {calculated['metric_id']!r}, "
                f"which differs in: {', '.join(differing)}"
            )
    for part in _EXECUTION_RECORD:
        if part not in earlier.supplied or not _same(reproduced[part], earlier.supplied[part]):
            raise EvidenceError(
                f"it does not reproduce the earlier {part!r}: the status of a scorecard, what it is missing, "
                "how its sweep was executed and what that used follow from its attempt-level evidence and its "
                "execution records"
            )


def _same(calculated: Any, earlier: Any) -> bool:
    """Whether a part of a result calculated now is the part the earlier scorecard has.

    Numbers are the same when they agree to nine significant digits: the earlier result may
    have been calculated on a platform whose mathematical library rounds the last digit of
    an exponential or a logarithm the other way.
    """
    if isinstance(calculated, Mapping) and isinstance(earlier, Mapping):
        return calculated.keys() == earlier.keys() and all(_same(calculated[key], earlier[key]) for key in calculated)
    if isinstance(calculated, (list, tuple)) and isinstance(earlier, (list, tuple)):
        return len(calculated) == len(earlier) and all(map(_same, calculated, earlier))
    if _is_number(calculated) and _is_number(earlier):
        return isclose(calculated, earlier, rel_tol=1e-9, abs_tol=1e-12)
    return type(calculated) is type(earlier) and calculated == earlier


def _is_number(value: Any) -> bool:
    return type(value) in (int, float)


def _require_criteria_declared_again(
    earlier_metrics: Sequence[Mapping[str, Any]], acceptance_criteria: Sequence[AcceptanceCriterion]
) -> None:
    """Require a criterion for every metric the earlier scorecard assessed one for.

    A criterion cannot be taken from the earlier scorecard, because a customer-supplied
    assessment is the customer's code. Without this, the new scorecard would silently
    have no acceptance assessment where the earlier one had one.
    """
    declared = criteria_by_metric(acceptance_criteria)
    for entry in earlier_metrics:
        if entry.get("acceptance") is not None and entry["metric_id"] not in declared:
            raise ValueError(
                f"the earlier scorecard assessed an acceptance criterion for {entry['metric_id']!r}, and none is "
                "declared for the recalculation. Declare the acceptance criteria again, as for the sweep"
            )


def _correction_records(
    corrections: Sequence[OutcomeCorrection], earlier_attempts: Sequence[Attempt]
) -> list[dict[str, Any]]:
    earlier = {attempt.attempt_id: attempt for attempt in earlier_attempts}
    records = []
    for correction in corrections:
        attempt = earlier[correction.attempt_id]
        replaced = attempt.task_outcome
        records.append(
            {
                "attempt_id": attempt.attempt_id,
                "task_id": attempt.task_id,
                "task_outcome": correction.task_outcome.to_dict(),
                "provenance": correction.provenance,
                "superseded_task_outcome": None if replaced is None else replaced.to_dict(),
            }
        )
    return records

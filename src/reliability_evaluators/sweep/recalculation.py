"""Recalculation of an earlier scorecard with task outcomes supplied or corrected after the run.

The offering keeps no evidence store. The customer supplies the earlier scorecard, which
holds the attempt-level evidence, from the platform that kept it. Nothing here makes an
Attempt, invokes an agent, reads a file or calls a platform: an earlier run is not modified.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from math import isclose
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..evaluators import RELIABILITY_METRICS, Fidelity, MetricState, ScoringPath
from ..evidence import (
    Attempt,
    EvidenceError,
    OutcomeCorrection,
    apply_outcome_corrections,
    group_by_task,
    read_attempts,
)
from ..scorecard import SCHEMA_VERSION, AcceptanceCriterion, ScoredMetric, build_scorecard, criteria_by_metric
from .declaration import ActionEvidenceDeclaration, ConfiguredEvaluator
from .runner import _select, _SelectedEvaluator

SUPERSEDED = "superseded"

# What a scorecard holds besides its schema version. A recalculation reads each of these, and
# refuses a scorecard that holds anything else: a part the scorecard gains is added here
# together with what a recalculated scorecard does with it, so that it is never left out silently.
_METRICS, _EXPERIMENTS, _ATTEMPTS, _SUPERSEDED_RESULTS = "metrics", "experiments", "attempts", "superseded_results"
_READ = {"schema_version", _METRICS, _EXPERIMENTS, _ATTEMPTS, _SUPERSEDED_RESULTS}


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
) -> dict[str, Any]:
    """Score the evidence of an earlier scorecard again, with task outcomes supplied or corrected since.

    `earlier_scorecard` is the scorecard of the earlier sweep or recalculation, as the data
    of its JSON. It holds the attempt-level evidence, so nothing else is needed: the
    offering keeps no evidence store, and the customer supplies it from their platform.
    It is read and not changed.

    `outcome_corrections` gives, by Attempt identity, the task outcome of each Attempt that
    is supplied late or corrected, with its source and its provenance.

    The result is a new scorecard. Every metric the earlier scorecard scored is scored
    again by the same Evaluator, in this process, from the Attempts its Evaluator was given
    before. A correction changes the task outcome of the Attempt it names and nothing else:
    the confidence rating, the action evidence, the resource measurements, the violation
    assessments and what the Attempt records about its experiment stay as they were. So a
    metric that is not calculated from task outcomes keeps its result. Each corrected
    Attempt keeps the record that was replaced, in `superseded_task_outcomes`, with the
    provenance of the correction. The earlier results are kept in `superseded_results`,
    marked as superseded, with the corrections that replaced them.

    `metrics`, `action_evidence` and `acceptance_criteria` are declared again as for the
    sweep, because the earlier scorecard cannot hold the customer's code. `action_evidence`
    is needed when a trajectory metric was scored, and `metrics` only for an Evaluator
    that was configured with declarations of its own. A metric that the earlier scorecard
    did not score cannot be added. Acceptance criteria are assessed on the new results.

    Raises RecalculationFailed, and returns no scorecard, when the recalculation cannot be
    done as asked. That includes: evidence that does not validate, or that does not give
    the earlier results when it is scored as it is; a correction for an Attempt that is not
    in the evidence, that matches more than one Attempt, or whose execution did not
    complete; two corrections for one Attempt; no correction at all; a missing declaration;
    and an acceptance criterion of the earlier scorecard that is not declared again.
    """
    try:
        return _recalculate(earlier_scorecard, outcome_corrections, metrics, action_evidence, acceptance_criteria)
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
) -> dict[str, Any]:
    corrections = _given(outcome_corrections)
    with _validating_the_supplied_evidence():
        earlier = _read(earlier_scorecard)
        scored_earlier = _scored_earlier(earlier)
    evaluators = _selected(scored_earlier, metrics, action_evidence)
    with _validating_the_supplied_evidence():
        _require_reproduced(earlier.metrics, _score(evaluators, earlier.attempts))
    _require_criteria_declared_again(earlier.metrics, acceptance_criteria)
    attempts = apply_outcome_corrections(earlier.attempts, corrections)
    superseded = {
        "status": SUPERSEDED,
        "outcome_corrections": _correction_records(corrections, earlier.attempts),
        "metrics": deepcopy(earlier.metrics),
    }
    scorecard = build_scorecard(
        metrics=_score(evaluators, attempts),
        attempts=attempts,
        experiments=deepcopy(earlier.experiments),
        acceptance_criteria=acceptance_criteria,
    )
    return {**scorecard, _SUPERSEDED_RESULTS: [*deepcopy(earlier.superseded_results), superseded]}


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
def _validating_the_supplied_evidence() -> Iterator[None]:
    try:
        yield
    except EvidenceError as error:
        raise EvidenceError(f"the supplied evidence does not validate: {error}") from error


@dataclass(frozen=True)
class _EarlierScorecard:
    """What the scorecard the customer supplied holds."""

    metrics: list[Any]
    experiments: list[Any]
    attempts: tuple[Attempt, ...]
    # The results it had itself superseded, oldest first.
    superseded_results: list[Any]


def _read(earlier_scorecard: Any) -> _EarlierScorecard:
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
    return _EarlierScorecard(
        metrics=earlier_scorecard[_METRICS],
        experiments=experiments,
        attempts=read_attempts(earlier_scorecard[_ATTEMPTS]),
        superseded_results=superseded_results,
    )


@dataclass(frozen=True)
class _ScoredEarlier:
    """A metric the earlier scorecard scored, and the Attempts its Evaluator was given."""

    metric_id: str
    attempts_used: tuple[Attempt, ...]


def _scored_earlier(earlier: _EarlierScorecard) -> list[_ScoredEarlier]:
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
        scored.append(_ScoredEarlier(metric_id, tuple(by_identity[attempt_id] for attempt_id in attempt_ids)))
    return scored


@dataclass(frozen=True)
class _Rescoring:
    """An Evaluator with what the customer declared for it, and the identities of the Attempts it is given."""

    evaluator: _SelectedEvaluator
    attempt_ids: tuple[str, ...]


def _selected(
    scored_earlier: Sequence[_ScoredEarlier],
    metrics: Sequence[str | ConfiguredEvaluator],
    action_evidence: ActionEvidenceDeclaration | None,
) -> list[_Rescoring]:
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
    return [
        _Rescoring(
            _select(configured.get(scored.metric_id, scored.metric_id), action_evidence),
            tuple(attempt.attempt_id for attempt in scored.attempts_used),
        )
        for scored in scored_earlier
    ]


def _score(evaluators: Sequence[_Rescoring], attempts: Sequence[Attempt]) -> list[ScoredMetric]:
    """Score each Evaluator from the Attempts it is given, taken from `attempts` by identity."""
    by_identity = {attempt.attempt_id: attempt for attempt in attempts}
    return [
        ScoredMetric(
            result=rescoring.evaluator.evaluate(
                group_by_task(by_identity[attempt_id] for attempt_id in rescoring.attempt_ids)
            ),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
            attempts_used=rescoring.attempt_ids,
        )
        for rescoring in evaluators
    ]


def _require_reproduced(earlier_metrics: Sequence[Mapping[str, Any]], rescored: Sequence[ScoredMetric]) -> None:
    """Require that the supplied evidence gives the earlier results when it is scored as it is.

    Otherwise the new results would differ from the superseded ones for more than the
    corrected task outcomes: the evidence is not the evidence behind the earlier scorecard,
    or the results were calculated with other declarations or another metric definition.
    """
    earlier = {entry["metric_id"]: entry for entry in earlier_metrics}
    for scored in rescored:
        calculated = scored.result.to_dict()
        entry = earlier[calculated["metric_id"]]
        differing = [part for part in calculated if part not in entry or not _same(calculated[part], entry[part])]
        if differing:
            raise EvidenceError(
                f"it does not reproduce the earlier result of {calculated['metric_id']!r}, "
                f"which differs in: {', '.join(differing)}"
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

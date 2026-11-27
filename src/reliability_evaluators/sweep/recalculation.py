"""Recalculation of an earlier scorecard with task outcomes supplied or corrected after the run."""

from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Iterator, Mapping, Sequence

from ..evaluators import EVALUATORS, RELIABILITY_METRICS, Evaluator, Fidelity, MetricState, ScoringPath
from ..evidence import (
    Attempt,
    EvidenceError,
    OutcomeCorrection,
    apply_outcome_corrections,
    group_by_task,
    read_attempts,
)
from ..scorecard import SCHEMA_VERSION, ScoredMetric, build_scorecard
from .declaration import ActionEvidenceDeclaration

SUPERSEDED = "superseded"


class RecalculationFailed(Exception):
    """The recalculation did not produce a scorecard. The earlier result stands as it was."""


def recalculate(
    *,
    earlier_scorecard: Mapping[str, Any],
    outcome_corrections: Sequence[OutcomeCorrection],
    action_evidence: ActionEvidenceDeclaration | None = None,
) -> dict[str, Any]:
    """Score the evidence of an earlier scorecard again, with the corrected task outcomes."""
    try:
        return _recalculate(earlier_scorecard, outcome_corrections, action_evidence)
    except Exception as error:
        raise RecalculationFailed(f"recalculation failed: {error}") from error


def _recalculate(
    earlier_scorecard: Mapping[str, Any],
    outcome_corrections: Sequence[OutcomeCorrection],
    action_evidence: ActionEvidenceDeclaration | None,
) -> dict[str, Any]:
    with _validating_the_supplied_evidence():
        earlier_metrics, earlier_attempts = _read(earlier_scorecard)
        evaluators = _evaluators_of(earlier_metrics)
    declared = {"action_types": None if action_evidence is None else action_evidence.mapping}
    with _validating_the_supplied_evidence():
        _require_reproduced(earlier_metrics, _score(evaluators, earlier_attempts, declared))
    attempts = apply_outcome_corrections(earlier_attempts, outcome_corrections)
    if not outcome_corrections:
        raise ValueError(
            "no task outcome was supplied or corrected, so there is nothing to recalculate: "
            "a recalculation needs at least one OutcomeCorrection"
        )
    superseded = {
        "status": SUPERSEDED,
        "outcome_corrections": _correction_records(outcome_corrections, earlier_attempts),
        "metrics": deepcopy(earlier_metrics),
    }
    superseded_results = [*deepcopy(earlier_scorecard.get("superseded_results", [])), superseded]
    scorecard = build_scorecard(metrics=_score(evaluators, attempts, declared), attempts=attempts)
    return {**scorecard, "superseded_results": superseded_results}


@contextmanager
def _validating_the_supplied_evidence() -> Iterator[None]:
    try:
        yield
    except EvidenceError as error:
        raise EvidenceError(f"the supplied evidence does not validate: {error}") from error


def _read(earlier_scorecard: Any) -> tuple[list[Any], tuple[Attempt, ...]]:
    """The metric results and the Attempts of the scorecard the customer supplied."""
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
    if not isinstance(earlier_scorecard.get("attempts"), list):
        raise EvidenceError("it has no attempt-level evidence: 'attempts' is not a list of Attempts")
    if not isinstance(earlier_scorecard.get("metrics"), list):
        raise EvidenceError(
            "it has no metric results to supersede. Supply the earlier scorecard, which holds the "
            "attempt-level evidence as well, and not the evidence alone"
        )
    return earlier_scorecard["metrics"], read_attempts(earlier_scorecard["attempts"])


def _evaluators_of(earlier_metrics: Sequence[Any]) -> list[Evaluator]:
    """The Evaluators of the metrics the earlier scorecard scored, which are scored again."""
    known = {metric.metric_id for metric in RELIABILITY_METRICS}
    seen: set[str] = set()
    evaluators = []
    for entry in earlier_metrics:
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
        if metric_id not in EVALUATORS:
            raise EvidenceError(
                f"it has a result for {metric_id!r}, and {metric_id!r} has no Evaluator in this version"
            )
        evaluators.append(EVALUATORS[metric_id])
    return evaluators


def _score(
    evaluators: Sequence[Evaluator], attempts: Sequence[Attempt], declared: Mapping[str, Any]
) -> list[ScoredMetric]:
    evidence = group_by_task(attempts)
    return [
        ScoredMetric(
            result=evaluator.evaluate(evidence, *(declared[name] for name in evaluator.declarations)),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
        )
        for evaluator in evaluators
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
        differing = [
            part for part in calculated if part not in entry or _as_json(calculated[part]) != _as_json(entry[part])
        ]
        if differing:
            raise EvidenceError(
                f"it does not reproduce the earlier result of {calculated['metric_id']!r}, "
                f"which differs in: {', '.join(differing)}"
            )


def _as_json(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


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

"""Recalculation of an earlier scorecard with task outcomes supplied or corrected after the run."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from ..evaluators import EVALUATORS, Fidelity, MetricState, ScoringPath
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
    try:
        earlier_metrics, earlier_attempts = _read(earlier_scorecard)
    except EvidenceError as error:
        raise EvidenceError(f"the supplied evidence does not validate: {error}") from error
    attempts = apply_outcome_corrections(earlier_attempts, outcome_corrections)
    if not outcome_corrections:
        raise ValueError(
            "no task outcome was supplied or corrected, so there is nothing to recalculate: "
            "a recalculation needs at least one OutcomeCorrection"
        )
    evidence = group_by_task(attempts)
    declared = {"action_types": None if action_evidence is None else action_evidence.mapping}
    scored = []
    for entry in earlier_metrics:
        if entry["state"] == MetricState.NOT_CONFIGURED.value:
            continue
        evaluator = EVALUATORS[entry["metric_id"]]
        scored.append(
            ScoredMetric(
                result=evaluator.evaluate(evidence, *(declared[name] for name in evaluator.declarations)),
                scoring_path=ScoringPath.CUSTOMER_PROCESS,
                fidelity=Fidelity.AUTHORITATIVE,
            )
        )
    superseded = {
        "status": SUPERSEDED,
        "outcome_corrections": _correction_records(outcome_corrections, earlier_attempts),
        "metrics": deepcopy(earlier_metrics),
    }
    superseded_results = [*deepcopy(earlier_scorecard.get("superseded_results", [])), superseded]
    return {**build_scorecard(metrics=scored, attempts=attempts), "superseded_results": superseded_results}


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

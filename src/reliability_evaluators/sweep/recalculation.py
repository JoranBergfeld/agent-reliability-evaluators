"""Recalculation of an earlier scorecard with task outcomes supplied or corrected after the run."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from ..evaluators import EVALUATORS, Fidelity, MetricState, ScoringPath
from ..evidence import Attempt, OutcomeCorrection, apply_outcome_corrections, group_by_task, read_attempt
from ..scorecard import ScoredMetric, build_scorecard
from .declaration import ActionEvidenceDeclaration

SUPERSEDED = "superseded"


def recalculate(
    *,
    earlier_scorecard: Mapping[str, Any],
    outcome_corrections: Sequence[OutcomeCorrection],
    action_evidence: ActionEvidenceDeclaration | None = None,
) -> dict[str, Any]:
    """Score the evidence of an earlier scorecard again, with the corrected task outcomes."""
    earlier_attempts = [read_attempt(recorded) for recorded in earlier_scorecard["attempts"]]
    attempts = apply_outcome_corrections(earlier_attempts, outcome_corrections)
    evidence = group_by_task(attempts)
    declared = {"action_types": None if action_evidence is None else action_evidence.mapping}
    scored = []
    for entry in earlier_scorecard["metrics"]:
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
        "metrics": deepcopy(earlier_scorecard["metrics"]),
    }
    superseded_results = [*deepcopy(earlier_scorecard.get("superseded_results", [])), superseded]
    return {**build_scorecard(metrics=scored, attempts=attempts), "superseded_results": superseded_results}


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

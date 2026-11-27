"""Recalculation of an earlier scorecard with task outcomes supplied or corrected after the run."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..evaluators import EVALUATORS, Fidelity, MetricState, ScoringPath
from ..evidence import OutcomeCorrection, apply_outcome_corrections, group_by_task, read_attempt
from ..scorecard import ScoredMetric, build_scorecard


def recalculate(
    *,
    earlier_scorecard: Mapping[str, Any],
    outcome_corrections: Sequence[OutcomeCorrection],
) -> dict[str, Any]:
    """Score the evidence of an earlier scorecard again, with the corrected task outcomes."""
    attempts = apply_outcome_corrections(
        [read_attempt(recorded) for recorded in earlier_scorecard["attempts"]], outcome_corrections
    )
    evidence = group_by_task(attempts)
    scored = [
        ScoredMetric(
            result=EVALUATORS[entry["metric_id"]].evaluate(evidence),
            scoring_path=ScoringPath.CUSTOMER_PROCESS,
            fidelity=Fidelity.AUTHORITATIVE,
        )
        for entry in earlier_scorecard["metrics"]
        if entry["state"] != MetricState.NOT_CONFIGURED.value
    ]
    return build_scorecard(metrics=scored, attempts=attempts)

"""Assembly of the machine-readable reliability scorecard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from ..evaluators import RELIABILITY_METRICS, Fidelity, MetricResult, MetricState, ReliabilityMetric, ScoringPath
from ..evidence import Attempt
from .acceptance import AcceptanceCriterion, assess, criteria_by_metric

SCHEMA_VERSION = 1


@dataclass(frozen=True, kw_only=True)
class ScoredMetric:
    """A metric result, attributed to the scoring path that produced it."""

    result: MetricResult
    scoring_path: ScoringPath
    fidelity: Fidelity


def build_scorecard(
    *,
    metrics: Sequence[ScoredMetric],
    attempts: Sequence[Attempt],
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
) -> dict[str, Any]:
    """Assemble the scorecard as JSON-compatible data.

    All twelve metrics are listed individually, in catalogue order. A metric that was not
    scored is not configured. Metric entries refer to `attempts` by Attempt identity.
    A metric's `acceptance` is the assessment of the customer's criterion for it, or None
    when the customer declared none.
    """
    scored_by_id = {scored.result.definition.metric.metric_id: scored for scored in metrics}
    criteria = criteria_by_metric(acceptance_criteria)
    return {
        "schema_version": SCHEMA_VERSION,
        "metrics": [
            _with_acceptance(_metric_entry(metric, scored_by_id.get(metric.metric_id)), criteria.get(metric.metric_id))
            for metric in RELIABILITY_METRICS
        ],
        "attempts": [attempt.to_dict() for attempt in attempts],
    }


def _metric_entry(metric: ReliabilityMetric, scored: ScoredMetric | None) -> dict[str, Any]:
    if scored is None:
        return {
            "metric_id": metric.metric_id,
            "name": metric.name,
            "dimension": metric.dimension,
            "state": MetricState.NOT_CONFIGURED.value,
        }
    return {
        **scored.result.to_dict(),
        "scoring_path": scored.scoring_path.value,
        "fidelity": scored.fidelity.value,
    }


def _with_acceptance(entry: dict[str, Any], criterion: AcceptanceCriterion | None) -> dict[str, Any]:
    return {**entry, "acceptance": None if criterion is None else assess(criterion, entry)}


def to_json(scorecard: dict[str, Any]) -> str:
    """Serialise a scorecard as strict JSON. Numbers keep the precision they were computed at."""
    return json.dumps(scorecard, indent=2, allow_nan=False)

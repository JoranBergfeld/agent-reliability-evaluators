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
    # Identities of the Attempts the Evaluator was given, whatever became of each. The value
    # may rest on fewer: `result.attempt_ids` are the ones it rests on.
    attempts_used: tuple[str, ...] = ()


def build_scorecard(
    *,
    metrics: Sequence[ScoredMetric],
    attempts: Sequence[Attempt],
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
) -> dict[str, Any]:
    """Assemble the scorecard as JSON-compatible data.

    All twelve metrics are listed individually, in catalogue order. A metric that was not
    scored is not configured. Metric entries refer to `attempts` by Attempt identity.
    A metric's `attempts_used` names every Attempt its Evaluator was given, and the other
    metrics whose Evaluators were given any of the same Attempts.
    A metric's `acceptance` is the assessment of the customer's criterion for it, or None
    when the customer declared none.
    """
    scored_by_id = {scored.result.definition.metric.metric_id: scored for scored in metrics}
    criteria = criteria_by_metric(acceptance_criteria)
    return {
        "schema_version": SCHEMA_VERSION,
        "metrics": [
            _with_acceptance(
                _with_attempts_used(_metric_entry(metric, scored_by_id.get(metric.metric_id)), scored_by_id),
                criteria.get(metric.metric_id),
            )
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


def _with_attempts_used(entry: dict[str, Any], scored_by_id: dict[str, ScoredMetric]) -> dict[str, Any]:
    """Add which Attempts the metric's Evaluator used, and which other metrics used any of them."""
    scored = scored_by_id.get(entry["metric_id"])
    if scored is None:
        return entry
    used = set(scored.attempts_used)
    return {
        **entry,
        "attempts_used": {
            "attempt_ids": list(scored.attempts_used),
            "shared_with": [
                metric.metric_id
                for metric in RELIABILITY_METRICS
                if metric.metric_id != entry["metric_id"]
                and metric.metric_id in scored_by_id
                and not used.isdisjoint(scored_by_id[metric.metric_id].attempts_used)
            ],
        },
    }


def _with_acceptance(entry: dict[str, Any], criterion: AcceptanceCriterion | None) -> dict[str, Any]:
    return {**entry, "acceptance": None if criterion is None else assess(criterion, entry)}


def to_json(scorecard: dict[str, Any]) -> str:
    """Serialise a scorecard as strict JSON. Numbers keep the precision they were computed at."""
    return json.dumps(scorecard, indent=2, allow_nan=False)

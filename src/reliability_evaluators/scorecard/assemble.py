"""Assembly of the machine-readable reliability scorecard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from ..evaluators import RELIABILITY_METRICS, Fidelity, MetricResult, MetricState, ReliabilityMetric, ScoringPath
from ..evidence import Attempt

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
    experiments: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Assemble the scorecard as JSON-compatible data.

    All twelve metrics are listed individually, in catalogue order. A metric that was not
    scored is not configured. Metric entries refer to `attempts` by Attempt identity.
    `experiments` is what each experiment declared and how its execution ended; Attempts
    refer to it by experiment identity.
    """
    scored_by_id = {scored.result.definition.metric.metric_id: scored for scored in metrics}
    return {
        "schema_version": SCHEMA_VERSION,
        "metrics": [_metric_entry(metric, scored_by_id.get(metric.metric_id)) for metric in RELIABILITY_METRICS],
        "experiments": list(experiments),
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


def to_json(scorecard: dict[str, Any]) -> str:
    """Serialise a scorecard as strict JSON. Numbers keep the precision they were computed at."""
    return json.dumps(scorecard, indent=2, allow_nan=False)

"""Assembly of the machine-readable reliability scorecard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from ..evaluators import RELIABILITY_METRICS, Fidelity, MetricResult, MetricState, ReliabilityMetric, ScoringPath
from ..evidence import Attempt, ExecutionStatus
from .acceptance import AcceptanceCriterion, assess, criteria_by_metric

SCHEMA_VERSION = 1


class ScorecardStatus(str, Enum):
    """Whether a scorecard holds everything the sweep planned to collect and score."""

    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True, kw_only=True)
class ScoredMetric:
    """A metric result, attributed to the scoring path that produced it."""

    result: MetricResult
    scoring_path: ScoringPath
    fidelity: Fidelity
    # The experiments that supplied the evidence the result was calculated from.
    experiment_ids: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ExperimentRun:
    """The running of one experiment: what was planned for it, what was made, and how it ended.

    The execution status is completed only when every planned Attempt completed. Otherwise
    it says why evidence is missing, with a reason. It is not a metric state.
    """

    experiment_id: str
    task_id: str
    planned_attempts: int
    # The execution limits the customer declared, as JSON-compatible data.
    limits: dict[str, Any]
    execution_status: ExecutionStatus
    reason: str | None = None
    # Every Attempt made, in the order planned.
    attempt_ids: tuple[str, ...]
    # Planned Attempts that were made and did not complete, by the identity of their last execution.
    not_completed: tuple[str, ...] = ()
    # Planned Attempts that were never started.
    not_made: int = 0
    # Retries made. Each is an Attempt of its own in `attempt_ids`.
    retries: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "execution_status": self.execution_status.value,
            "reason": self.reason,
            "limits": self.limits,
            "attempts": {
                "planned": self.planned_attempts,
                "completed": self.planned_attempts - len(self.not_completed) - self.not_made,
                "not_completed": list(self.not_completed),
                "not_made": self.not_made,
                "retries": self.retries,
            },
            "attempt_ids": list(self.attempt_ids),
        }


def build_scorecard(
    *,
    metrics: Sequence[ScoredMetric],
    attempts: Sequence[Attempt],
    experiments: Sequence[ExperimentRun],
    max_concurrent_attempts: int,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
) -> dict[str, Any]:
    """Assemble the scorecard as JSON-compatible data.

    All twelve metrics are listed individually, in catalogue order. A metric that was not
    scored is not configured. Metric entries and `experiments` refer to `attempts` by
    Attempt identity. `max_concurrent_attempts` is how many Attempts the sweep allowed in
    flight at once; 1 means they ran one after another.

    The scorecard is complete when nothing that was planned is missing. Otherwise it is
    partial, and `missing` names what is missing and why. A metric keeps its result when
    its Evaluator still calculates a value from the evidence that was collected.

    A metric's `acceptance` is the assessment of the customer's criterion for it, or None
    when the customer declared none.
    """
    scored_by_id = {scored.result.definition.metric.metric_id: scored for scored in metrics}
    criteria = criteria_by_metric(acceptance_criteria)
    attempts_by_id = {attempt.attempt_id: attempt for attempt in attempts}
    not_completed = [
        experiment for experiment in experiments if experiment.execution_status is not ExecutionStatus.COMPLETED
    ]
    failures = {
        metric_id: _experiment_failure(scored, not_completed) for metric_id, scored in scored_by_id.items()
    }
    missing = [_missing_attempt_evidence(experiment, attempts_by_id) for experiment in not_completed] + [
        {"kind": "metric_result", "metric_id": metric_id, **failure}
        for metric_id, failure in failures.items()
        if failure is not None
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": (ScorecardStatus.PARTIAL if missing else ScorecardStatus.COMPLETE).value,
        "missing": missing,
        "execution": {
            "concurrency": {
                "mode": "sequential" if max_concurrent_attempts == 1 else "concurrent",
                "max_concurrent_attempts": max_concurrent_attempts,
            }
        },
        "metrics": [
            _with_acceptance(
                _metric_entry(metric, scored_by_id.get(metric.metric_id), failures.get(metric.metric_id)),
                criteria.get(metric.metric_id),
            )
            for metric in RELIABILITY_METRICS
        ],
        "experiments": [experiment.to_dict() for experiment in experiments],
        "attempts": [attempt.to_dict() for attempt in attempts],
    }


def _experiment_failure(scored: ScoredMetric, not_completed: Sequence[ExperimentRun]) -> dict[str, Any] | None:
    """Why a metric has no result, when that is because its experiments did not complete.

    Evidence that is short because an experiment failed is not an unavailable measurement:
    what the complete evidence would have shown is not known.
    """
    failed = [
        experiment.experiment_id for experiment in not_completed if experiment.experiment_id in scored.experiment_ids
    ]
    if scored.result.state is not MetricState.UNAVAILABLE or not failed:
        return None
    return {
        "reason": (
            "experiments that supply this metric's evidence did not complete, and what they collected "
            f"does not permit a calculation: {scored.result.reason}"
        ),
        "experiment_ids": failed,
    }


def _missing_attempt_evidence(experiment: ExperimentRun, attempts_by_id: dict[str, Attempt]) -> dict[str, Any]:
    return {
        "kind": "attempt_evidence",
        "experiment_id": experiment.experiment_id,
        "execution_status": experiment.execution_status.value,
        "reason": experiment.reason,
        "attempts_not_completed": [
            {
                "attempt_id": attempt_id,
                "execution_status": attempts_by_id[attempt_id].execution_status.value,
                "execution_error": attempts_by_id[attempt_id].execution_error,
            }
            for attempt_id in experiment.not_completed
        ],
        "attempts_not_made": experiment.not_made,
    }


def _metric_entry(
    metric: ReliabilityMetric, scored: ScoredMetric | None, experiment_failure: dict[str, Any] | None
) -> dict[str, Any]:
    if scored is None:
        return {
            "metric_id": metric.metric_id,
            "name": metric.name,
            "dimension": metric.dimension,
            "state": MetricState.NOT_CONFIGURED.value,
        }
    entry = {
        **scored.result.to_dict(),
        "experiment_failure": experiment_failure,
        "scoring_path": scored.scoring_path.value,
        "fidelity": scored.fidelity.value,
    }
    if experiment_failure is not None:
        # There is no metric result, so the metric is in no metric state.
        entry.update(state=None, reason=None)
    return entry


def _with_acceptance(entry: dict[str, Any], criterion: AcceptanceCriterion | None) -> dict[str, Any]:
    return {**entry, "acceptance": None if criterion is None else assess(criterion, entry)}


def to_json(scorecard: dict[str, Any]) -> str:
    """Serialise a scorecard as strict JSON. Numbers keep the precision they were computed at."""
    return json.dumps(scorecard, indent=2, allow_nan=False)

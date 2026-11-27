"""Assembly of the machine-readable reliability scorecard."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from ..evaluators import RELIABILITY_METRICS, Fidelity, MetricResult, MetricState, ReliabilityMetric, ScoringPath
from ..evidence import Attempt, ExperimentStatus
from .acceptance import AcceptanceCriterion, assess, criteria_by_metric

SCHEMA_VERSION = 1


class ScorecardStatus(str, Enum):
    """Whether a scorecard holds everything the sweep planned to collect and score."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    # No scorecard was delivered. Only the record of a reporting failure has this status.
    REPORTING_FAILED = "reporting_failed"


@dataclass(frozen=True, kw_only=True)
class ScoredMetric:
    """A metric result, attributed to the scoring path that produced it."""

    result: MetricResult
    scoring_path: ScoringPath
    fidelity: Fidelity


@dataclass(frozen=True, kw_only=True)
class PlannedAttempts:
    """The Attempts planned for one experiment, or for one Task given without an experiment.

    It says what was planned, what was made, and how the making ended. `status` is not a
    metric state, and it is completed whenever every planned Attempt was made: whether
    each of those completed is counted separately.
    """

    task_id: str
    # None: the Task was given without an experiment.
    experiment_id: str | None = None
    # What the experiment declared, as the scorecard records it.
    declaration: dict[str, Any] = field(default_factory=dict)
    # The execution limits the customer declared, as JSON-compatible data.
    limits: dict[str, Any]
    status: ExperimentStatus
    # Why planned Attempts were not made, or why a retry was not.
    reason: str | None = None
    planned: int
    # Attempts in the scorecard, retries included.
    recorded: int
    # Planned Attempts that were made and did not complete, by the identity of their last execution.
    not_completed: tuple[str, ...] = ()
    # Planned Attempts that were never started.
    not_made: int = 0
    # Retries made. Each is an Attempt of its own.
    retries: int = 0
    # Whether the Attempts that the Evaluators score were cut short: not all of them were
    # made, or not one of them completed. Attempts under a variation are not scored yet.
    cut_short: bool = False

    def to_dict(self) -> dict[str, Any]:
        execution = {
            "status": self.status.value,
            "reason": self.reason,
            "attempts_planned": self.planned,
            "attempts_recorded": self.recorded,
            "attempts_completed": self.planned - len(self.not_completed) - self.not_made,
            "attempts_not_completed": list(self.not_completed),
            "attempts_not_made": self.not_made,
            "retries": self.retries,
        }
        declared = self.declaration if self.experiment_id is not None else {"task_id": self.task_id}
        return {**declared, "limits": self.limits, "execution": execution}


def build_scorecard(
    *,
    metrics: Sequence[ScoredMetric],
    attempts: Sequence[Attempt],
    planned: Sequence[PlannedAttempts],
    max_concurrent_attempts: int,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
) -> dict[str, Any]:
    """Assemble the scorecard as JSON-compatible data.

    All twelve metrics are listed individually, in catalogue order. A metric that was not
    scored is not configured. Metric entries refer to `attempts` by Attempt identity.
    A metric's `acceptance` is the assessment of the customer's criterion for it, or None
    when the customer declared none.

    `planned` is what was planned for each experiment and for each Task given without one,
    and how making it ended. The first are listed under `experiments` with what the
    experiment declared, the second under `tasks_without_experiment`. Attempts refer to
    their experiment by experiment identity.

    The scorecard is complete when every planned Attempt completed. Otherwise it is
    partial, and `missing` names what is missing and why. A metric keeps its result when
    its Evaluator still calculates a value from the evidence that was collected.

    `max_concurrent_attempts` is how many Attempts of Tasks given without an experiment
    the sweep allowed in flight at once; 1 means they ran one after another.
    """
    scored_by_id = {scored.result.definition.metric.metric_id: scored for scored in metrics}
    criteria = criteria_by_metric(acceptance_criteria)
    attempts_by_id = {attempt.attempt_id: attempt for attempt in attempts}
    failures = {metric_id: _execution_failure(scored, planned) for metric_id, scored in scored_by_id.items()}
    missing = [
        _missing_attempt_evidence(plan, attempts_by_id) for plan in planned if plan.not_made or plan.not_completed
    ] + [
        {"kind": "metric_result", "metric_id": metric_id, **failure}
        for metric_id, failure in failures.items()
        if failure is not None
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": (ScorecardStatus.PARTIAL if missing else ScorecardStatus.COMPLETE).value,
        "missing": missing,
        "concurrency": {
            "mode": "sequential" if max_concurrent_attempts == 1 else "concurrent",
            "max_concurrent_attempts": max_concurrent_attempts,
        },
        "metrics": [
            _with_acceptance(
                _metric_entry(metric, scored_by_id.get(metric.metric_id), failures.get(metric.metric_id)),
                criteria.get(metric.metric_id),
            )
            for metric in RELIABILITY_METRICS
        ],
        "experiments": [plan.to_dict() for plan in planned if plan.experiment_id is not None],
        "tasks_without_experiment": [plan.to_dict() for plan in planned if plan.experiment_id is None],
        "attempts": [attempt.to_dict() for attempt in attempts],
    }


def _execution_failure(scored: ScoredMetric, planned: Sequence[PlannedAttempts]) -> dict[str, Any] | None:
    """Why a metric has no result, when that is because the Attempts planned for it were cut short.

    An Evaluator that finds too little in evidence that was cut short has not measured
    anything: that is an execution failure and not an unavailable measurement. Where every
    planned Attempt was made and some of them completed, the Evaluator's own result stands.
    """
    short = [plan for plan in planned if plan.cut_short]
    if scored.result.state is not MetricState.UNAVAILABLE or not short:
        return None
    return {
        "reason": (
            "the Attempts that supply this metric's evidence were cut short, and what was collected "
            f"does not permit a calculation: {scored.result.reason}"
        ),
        "experiment_ids": [plan.experiment_id for plan in short if plan.experiment_id is not None],
        "task_ids": [plan.task_id for plan in short if plan.experiment_id is None],
    }


def _missing_attempt_evidence(plan: PlannedAttempts, attempts_by_id: dict[str, Attempt]) -> dict[str, Any]:
    return {
        "kind": "attempt_evidence",
        "experiment_id": plan.experiment_id,
        "task_id": plan.task_id,
        "status": plan.status.value,
        "reason": plan.reason,
        "attempts_not_made": plan.not_made,
        "attempts_not_completed": [
            {
                "attempt_id": attempt_id,
                "execution_status": attempts_by_id[attempt_id].execution_status.value,
                "execution_error": attempts_by_id[attempt_id].execution_error,
            }
            for attempt_id in plan.not_completed
        ],
    }


def _metric_entry(
    metric: ReliabilityMetric, scored: ScoredMetric | None, execution_failure: dict[str, Any] | None
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
        "execution_failure": execution_failure,
        "scoring_path": scored.scoring_path.value,
        "fidelity": scored.fidelity.value,
    }
    if execution_failure is not None:
        # There is no metric result, so the metric is in no metric state.
        entry.update(state=None, reason=None)
    return entry


def _with_acceptance(entry: dict[str, Any], criterion: AcceptanceCriterion | None) -> dict[str, Any]:
    return {**entry, "acceptance": None if criterion is None else assess(criterion, entry)}


def to_json(scorecard: dict[str, Any]) -> str:
    """Serialise a scorecard as strict JSON. Numbers keep the precision they were computed at."""
    return json.dumps(scorecard, indent=2, allow_nan=False)

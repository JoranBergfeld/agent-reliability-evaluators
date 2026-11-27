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
class ExecutionRecord:
    """What was planned for one experiment, or for one Task given without an experiment, and what was made.

    `status` says how the making of the Attempts ended. It is not a metric state, and it is
    completed whenever every planned Attempt was made: whether each of those completed is
    counted separately.
    """

    task_id: str
    # None: the Task was given without an experiment.
    experiment_id: str | None = None
    # What the experiment declared, as the scorecard records it.
    declaration: dict[str, Any] = field(default_factory=dict)
    # Which set of Attempts this is. Later sets are made for Evaluators that need separate Attempts.
    attempt_set: int = 1
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
        if self.experiment_id is not None:
            # A later set runs the experiment again under an identity of its own.
            return {**self.declaration, "limits": self.limits, "execution": execution}
        return {"task_id": self.task_id, "attempt_set": self.attempt_set, "limits": self.limits, "execution": execution}


@dataclass(frozen=True, kw_only=True)
class ScoredMetric:
    """A metric result, attributed to the scoring path that produced it."""

    result: MetricResult
    scoring_path: ScoringPath
    fidelity: Fidelity
    # Identities of the Attempts the Evaluator was given, whatever became of each. The value
    # may rest on fewer: `result.attempt_ids` are the ones it rests on.
    attempts_used: tuple[str, ...] = ()
    # The experiments and Tasks whose Attempts for this Evaluator were cut short: not all
    # that were planned under the conditions it is calculated from were made, or not one
    # of them completed.
    cut_short: tuple[ExecutionRecord, ...] = ()


def build_scorecard(
    *,
    metrics: Sequence[ScoredMetric],
    attempts: Sequence[Attempt],
    executions: Sequence[ExecutionRecord],
    max_concurrent_attempts: int,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
) -> dict[str, Any]:
    """Assemble the scorecard as JSON-compatible data.

    All twelve metrics are listed individually, in catalogue order. A metric that was not
    scored is not configured. Metric entries refer to `attempts` by Attempt identity.
    A metric's `attempts_used` names every Attempt its Evaluator was given, and the other
    metrics whose Evaluators were given any of the same Attempts.
    A metric's `acceptance` is the assessment of the customer's criterion for it, or None
    when the customer declared none.

    `executions` is what was planned for each experiment and for each Task given without
    one, and how making it ended. The first are listed under `experiments` with what the
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
    failures = {metric_id: _execution_failure(scored) for metric_id, scored in scored_by_id.items()}
    missing = [
        _missing_attempt_evidence(execution, attempts_by_id)
        for execution in executions
        if execution.not_made or execution.not_completed
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
                _with_attempts_used(
                    _metric_entry(metric, scored_by_id.get(metric.metric_id), failures.get(metric.metric_id)),
                    scored_by_id,
                ),
                criteria.get(metric.metric_id),
            )
            for metric in RELIABILITY_METRICS
        ],
        "experiments": [execution.to_dict() for execution in executions if execution.experiment_id is not None],
        "tasks_without_experiment": [
            execution.to_dict() for execution in executions if execution.experiment_id is None
        ],
        "attempts": [attempt.to_dict() for attempt in attempts],
    }


def _execution_failure(scored: ScoredMetric) -> dict[str, Any] | None:
    """Why a metric has no result, when that is because the Attempts planned for it were cut short.

    An Evaluator that finds too little in evidence that was cut short has not measured
    anything: that is an execution failure and not an unavailable measurement. Where every
    planned Attempt was made and some of them completed, the Evaluator's own result stands.
    """
    if scored.result.state is not MetricState.UNAVAILABLE or not scored.cut_short:
        return None
    return {
        "reason": (
            "the Attempts that supply this metric's evidence were cut short, and what was collected "
            f"does not permit a calculation: {scored.result.reason}"
        ),
        "experiment_ids": [
            execution.experiment_id for execution in scored.cut_short if execution.experiment_id is not None
        ],
        "task_ids": [execution.task_id for execution in scored.cut_short if execution.experiment_id is None],
    }


def _missing_attempt_evidence(execution: ExecutionRecord, attempts_by_id: dict[str, Attempt]) -> dict[str, Any]:
    return {
        "kind": "attempt_evidence",
        "experiment_id": execution.experiment_id,
        "task_id": execution.task_id,
        "attempt_set": execution.attempt_set,
        "status": execution.status.value,
        "reason": execution.reason,
        "attempts_not_made": execution.not_made,
        "attempts_not_completed": [
            {
                "attempt_id": attempt_id,
                "execution_status": attempts_by_id[attempt_id].execution_status.value,
                "execution_error": attempts_by_id[attempt_id].execution_error,
            }
            for attempt_id in execution.not_completed
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

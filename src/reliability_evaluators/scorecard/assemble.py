"""Assembly of the machine-readable reliability scorecard."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..evaluators import (
    RELIABILITY_METRICS,
    Fidelity,
    MetricDefinition,
    MetricResult,
    MetricState,
    ReliabilityMetric,
    ScoringPath,
)
from ..evidence import Attempt, EvidenceError, ExperimentStatus
from .acceptance import NOT_RECALCULATED, AcceptanceCriterion, assess, criteria_by_metric
from .usage import EvaluatorUse, PreparationUsage, code_scoring_use, managed_scoring_uses, sweep_token_usage

SCHEMA_VERSION = 1
# Where a scorecard reports token usage. It is metadata, apart from the metrics and their acceptance.
TOKEN_USAGE = "token_usage"


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
        declared = self.declaration if self.experiment_id is not None else {"task_id": self.task_id}
        return {**declared, "attempt_set": self.attempt_set, "limits": self.limits, "execution": execution}


def read_execution_record(recorded: Any, *, of_experiment: bool) -> ExecutionRecord:
    """The execution record that was written as `recorded`, for an experiment or for a Task given without one.

    It is read strictly, as attempt-level evidence is: a record that this version would not
    write back unchanged is refused. The limits are data here, and are not checked.
    """
    if not isinstance(recorded, Mapping):
        raise EvidenceError(f"an execution record is an object. Got {recorded!r}")
    named = recorded.get("experiment_id") if of_experiment else recorded.get("task_id")
    name = f"the execution record of {'experiment' if of_experiment else 'Task'} {named!r}"
    execution, limits = recorded.get("execution"), recorded.get("limits")
    if not isinstance(execution, Mapping) or not isinstance(limits, Mapping):
        raise EvidenceError(f"{name} is recorded without its limits or without its execution")
    try:
        status = ExperimentStatus(execution.get("status"))
    except ValueError:
        raise EvidenceError(f"{name} has the status {execution.get('status')!r}, which no execution ends with") from None
    not_completed = execution.get("attempts_not_completed")
    counts = [execution.get(key) for key in ("attempts_planned", "attempts_recorded", "attempts_not_made", "retries")]
    if (
        not isinstance(named, str)
        or not isinstance(recorded.get("task_id"), str)
        or type(recorded.get("attempt_set")) is not int
        or not isinstance(not_completed, list)
        or any(type(count) is not int for count in counts)
    ):
        raise EvidenceError(f"{name} is not what this version records for it")
    record = ExecutionRecord(
        task_id=recorded["task_id"],
        experiment_id=named if of_experiment else None,
        declaration=(
            {key: value for key, value in recorded.items() if key not in ("attempt_set", "limits", "execution")}
            if of_experiment
            else {}
        ),
        attempt_set=recorded["attempt_set"],
        limits=dict(limits),
        status=status,
        reason=execution.get("reason"),
        planned=counts[0],
        recorded=counts[1],
        not_completed=tuple(not_completed),
        not_made=counts[2],
        retries=counts[3],
    )
    if json.loads(to_json(record.to_dict())) != json.loads(to_json(dict(recorded))):
        raise EvidenceError(f"{name} is not what this version records for it")
    return record


@dataclass(frozen=True)
class CutShort:
    """The experiments, and the Tasks given without one, whose Attempts for a metric were cut short.

    They were cut short when not all the Attempts planned under the conditions the metric is
    calculated from were made, or not one of them completed.
    """

    experiment_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()

    @classmethod
    def of(cls, records: Iterable[ExecutionRecord]) -> CutShort:
        records = tuple(records)
        return cls(
            tuple(record.experiment_id for record in records if record.experiment_id is not None),
            tuple(record.task_id for record in records if record.experiment_id is None),
        )

    def __bool__(self) -> bool:
        return bool(self.experiment_ids or self.task_ids)

    def to_dict(self) -> dict[str, Any]:
        return {"experiment_ids": list(self.experiment_ids), "task_ids": list(self.task_ids)}


@dataclass(frozen=True, kw_only=True)
class ScoredMetric:
    """A metric result, attributed to the scoring path that produced it."""

    result: MetricResult
    scoring_path: ScoringPath
    fidelity: Fidelity
    # Identities of the Attempts the Evaluator was given, whatever became of each. The value
    # may rest on fewer: `result.attempt_ids` are the ones it rests on.
    attempts_used: tuple[str, ...] = ()
    # Where the Attempts for this Evaluator were cut short.
    cut_short: CutShort = CutShort()
    # Why the value is not known to equal what the Evaluator computed. An unverified result says.
    limitation: str | None = None
    # What Foundry-managed scoring recorded about the result: the evaluator and the run it
    # came from, and what the platform added to it. JSON-compatible data.
    managed_scoring: Mapping[str, Any] | None = None
    # What Foundry reported of the token usage of each managed run. It is reported as usage, not with the metric.
    reported_usage: Sequence[Mapping[str, Any]] = ()

    def __post_init__(self) -> None:
        if self.fidelity is Fidelity.UNVERIFIED and not (self.limitation or "").strip():
            raise ValueError("an unverified result is reported with its limitation")

    @property
    def definition(self) -> MetricDefinition:
        return self.result.definition


@dataclass(frozen=True, kw_only=True)
class MissingResult:
    """A selected metric that Foundry-managed scoring gave no result for.

    The metric is in no metric state. A scoring that failed is not an unavailable
    measurement, not a value of zero and not a task failure: nothing was measured. It is
    attributed to the scoring path that failed, and nothing was scored in its place.
    """

    definition: MetricDefinition
    # The execution error of the scoring, in words.
    execution_error: str
    # Identities of the Attempts the Evaluator was given.
    attempts_used: tuple[str, ...] = ()
    cut_short: CutShort = CutShort()
    # What Foundry-managed scoring recorded about the run that failed. JSON-compatible data.
    managed_scoring: Mapping[str, Any] | None = None
    # What Foundry reported of the token usage of each managed run that was made. Empty: no run is known.
    reported_usage: Sequence[Mapping[str, Any]] = ()
    scoring_path = ScoringPath.FOUNDRY_MANAGED

    def __post_init__(self) -> None:
        if not (self.execution_error or "").strip():
            raise ValueError("a missing result is reported with its execution error")


NOT_RECALCULATED_MEANING = (
    "This entry was not recalculated. The metric was scored by Foundry-managed scoring. A recalculation never "
    "scores such a metric in the customer's own process instead, and no Foundry project was supplied to score it "
    "again. So the entry holds what the earlier scorecard held, and that rests on task outcomes that were "
    "supplied or corrected since: those of the Attempts named here. It is not the result for the corrected "
    "outcomes, and an acceptance criterion is not assessed on it. Supply `managed_scoring` to the recalculation "
    "to have Foundry score the metric from the corrected outcomes."
)


@dataclass(frozen=True, kw_only=True)
class CarriedMetric:
    """The entry of a metric that a recalculation carries over from the earlier scorecard, as it was recorded.

    A recalculation does not repeat the scoring of a Foundry-managed metric in the customer's
    own process. Unless Foundry scores it again, its entry is carried: unchanged when the
    corrections do not touch the evidence it was scored from, and otherwise marked as not
    recalculated, with the Attempts whose task outcomes were corrected since.
    """

    # The entry as the earlier scorecard records it.
    entry: Mapping[str, Any]
    # What the earlier scorecard reports as the token usage of the Evaluator: its scoring, and the rest
    # of what it records beside what follows from the Attempts, such as a customer-process rescore.
    usage: Mapping[str, Any]
    # The Attempts the result rests on whose task outcomes were supplied or corrected since. Empty: none.
    corrected_attempt_ids: tuple[str, ...] = ()

    @property
    def metric_id(self) -> str:
        return self.entry["metric_id"]

    @property
    def attempts_used(self) -> tuple[str, ...]:
        return tuple(self.entry["attempts_used"]["attempt_ids"])


def _metric_id(scored: ScoredMetric | MissingResult | CarriedMetric) -> str:
    return scored.metric_id if isinstance(scored, CarriedMetric) else scored.definition.metric.metric_id


def build_scorecard(
    *,
    metrics: Sequence[ScoredMetric | MissingResult | CarriedMetric],
    attempts: Sequence[Attempt],
    executions: Sequence[ExecutionRecord],
    max_concurrent_attempts: int,
    acceptance_criteria: Sequence[AcceptanceCriterion] = (),
    preparation_usage: Sequence[PreparationUsage] = (),
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

    The scorecard is complete when every planned Attempt completed and every selected
    metric has a result. Otherwise it is partial, and `missing` names what is missing and
    why. A metric keeps its result when its Evaluator still calculates a value from the
    evidence that was collected. A metric whose scoring failed has none: it is listed in
    `missing` with its execution error, and every other metric keeps its own.

    `max_concurrent_attempts` is how many Attempts of Tasks given without an experiment
    the sweep allowed in flight at once; 1 means they ran one after another.

    `token_usage` reports what the Attempts, their confidence follow-ups, the scoring of
    each selected metric and the preparation in `preparation_usage` used, in tokens. It is
    metadata, apart from the metrics: no metric entry and no acceptance assessment holds or
    reads any of it.
    """
    scored_by_id = {_metric_id(scored): scored for scored in metrics}
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
        TOKEN_USAGE: _token_usage(attempts, scored_by_id, preparation_usage),
        "attempts": [attempt.to_dict() for attempt in attempts],
    }


# What the token usage of an Evaluator holds that follows from its Attempts and its scoring.
_CALCULATED_USAGE = (
    "metric_id", "total", "overlapping", "additive", "overlaps_with", "attempt_ids", "preparation_ids", "scoring"
)


def _token_usage(
    attempts: Sequence[Attempt],
    scored_by_id: Mapping[str, ScoredMetric | MissingResult | CarriedMetric],
    preparation_usage: Sequence[PreparationUsage],
) -> dict[str, Any]:
    usage = sweep_token_usage(
        attempts,
        [
            EvaluatorUse(
                metric_id=metric.metric_id,
                attempt_ids=tuple(scored_by_id[metric.metric_id].attempts_used),
                scoring=_scoring_uses(scored_by_id[metric.metric_id]),
            )
            for metric in RELIABILITY_METRICS
            if metric.metric_id in scored_by_id
        ],
        preparation_usage,
    )
    for of_evaluator in usage["evaluators"]:
        scored = scored_by_id[of_evaluator["metric_id"]]
        if isinstance(scored, CarriedMetric):
            # What the earlier scorecard records beside it, such as the use of a customer-process rescore.
            of_evaluator.update(
                {key: deepcopy(value) for key, value in scored.usage.items() if key not in _CALCULATED_USAGE}
            )
    return usage


def _scoring_uses(scored: ScoredMetric | MissingResult | CarriedMetric) -> list[dict[str, Any]]:
    """The uses that scoring a metric was, on the scoring path the metric is attributed to."""
    if isinstance(scored, CarriedMetric):
        # The scoring was not repeated, so what it used is what the earlier scorecard reports.
        return [dict(use) for use in scored.usage["scoring"]]
    if scored.scoring_path is ScoringPath.CUSTOMER_PROCESS:
        return [code_scoring_use()]
    reported = []
    for of_run in scored.reported_usage or ():
        try:
            reported.append(json.loads(json.dumps(of_run, allow_nan=False)))
        except (TypeError, ValueError):
            # What cannot be written to a scorecard is kept as text, and is then not read as a count.
            reported.append(repr(of_run))
    return managed_scoring_uses(reported, scored.managed_scoring)


MANAGED_SCORING_GAVE_NO_RESULT = (
    "Foundry-managed scoring gave no result for this metric, and it was not scored in the customer's own "
    "process instead"
)


def _execution_failure(scored: ScoredMetric | MissingResult | CarriedMetric) -> dict[str, Any] | None:
    """Why a metric has no result: the Attempts planned for it were cut short, or scoring it failed.

    An Evaluator that finds too little in evidence that was cut short has not measured
    anything: that is an execution failure and not an unavailable measurement. Where every
    planned Attempt was made and some of them completed, the Evaluator's own result stands.

    A scoring that failed names no experiment and no Task: it is not the Attempts that failed.
    """
    if isinstance(scored, CarriedMetric):
        return deepcopy(scored.entry.get("execution_failure"))
    if isinstance(scored, MissingResult):
        return {
            "reason": f"{MANAGED_SCORING_GAVE_NO_RESULT}: {scored.execution_error}",
            "experiment_ids": [],
            "task_ids": [],
        }
    if scored.result.state is not MetricState.UNAVAILABLE or not scored.cut_short:
        return None
    return {
        "reason": (
            "the Attempts that supply this metric's evidence were cut short, and what was collected "
            f"does not permit a calculation: {scored.result.reason}"
        ),
        **scored.cut_short.to_dict(),
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
    metric: ReliabilityMetric,
    scored: ScoredMetric | MissingResult | CarriedMetric | None,
    execution_failure: dict[str, Any] | None,
) -> dict[str, Any]:
    if scored is None:
        return {
            "metric_id": metric.metric_id,
            "name": metric.name,
            "dimension": metric.dimension,
            "state": MetricState.NOT_CONFIGURED.value,
        }
    if isinstance(scored, CarriedMetric):
        # As it was recorded. Which Attempts it used and its acceptance are added to every entry alike.
        entry = {
            key: deepcopy(value)
            for key, value in scored.entry.items()
            if key not in ("attempts_used", "acceptance", NOT_RECALCULATED)
        }
        if scored.corrected_attempt_ids:
            entry[NOT_RECALCULATED] = {
                "meaning": NOT_RECALCULATED_MEANING,
                "corrected_attempt_ids": list(scored.corrected_attempt_ids),
            }
        return entry
    if isinstance(scored, MissingResult):
        # The fields of a result, with nothing in them: no value, no state, no counts and no fidelity.
        result = {
            "metric_id": metric.metric_id,
            "name": metric.name,
            "dimension": metric.dimension,
            "state": None,
            "value": None,
            "reason": None,
            "definition": scored.definition.to_dict(),
            "counts": {},
            "details": {},
            "evidence": {"attempt_ids": []},
        }
        fidelity = None
    else:
        result, fidelity = scored.result.to_dict(), scored.fidelity.value
    entry = {
        **result,
        "execution_failure": execution_failure,
        "cut_short": scored.cut_short.to_dict(),
        "scoring_path": scored.scoring_path.value,
        "fidelity": fidelity,
    }
    if execution_failure is not None:
        # There is no metric result, so the metric is in no metric state.
        entry.update(state=None, reason=None)
    # Only a result that has them carries these, so that an entry never shows an empty limitation.
    if getattr(scored, "limitation", None) is not None:
        entry["limitation"] = scored.limitation
    if scored.managed_scoring is not None:
        entry["managed_scoring"] = json.loads(json.dumps(scored.managed_scoring, allow_nan=False))
    return entry


def _with_attempts_used(
    entry: dict[str, Any], scored_by_id: Mapping[str, ScoredMetric | MissingResult | CarriedMetric]
) -> dict[str, Any]:
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


# Where the entry of a metric records the customer-process rescore that the customer requested for it.
RESCORE = "customer_process_rescore"
RESCORE_MEANING = (
    "A customer-process rescore: the customer asked for the evidence that Foundry-managed scoring was given "
    "for this metric to be scored in their own process. It is a result of its own, attributed to the "
    "customer-process scoring path. It is not the Foundry-managed result of the metric and does not replace "
    "it: what Foundry-managed scoring gave stays in the entry of the metric as it was."
)


def rescore_entry(
    scored: ScoredMetric, evidence_sha256: str | None, criterion: AcceptanceCriterion | None
) -> dict[str, Any]:
    """A customer-process rescore as the entry of its metric records it, beside the Foundry-managed result.

    It holds what the entry of a metric scored in the customer's own process holds, and
    its own acceptance assessment. `evidence_sha256` is the digest of the evidence it was
    calculated from, as that evidence is submitted to Foundry.
    """
    metric = scored.definition.metric
    entry = {
        "meaning": RESCORE_MEANING,
        "evidence_sha256": evidence_sha256,
        **_metric_entry(metric, scored, _execution_failure(scored)),
    }
    return _with_acceptance(entry, criterion)


def to_json(scorecard: dict[str, Any]) -> str:
    """Serialise a scorecard as strict JSON. Numbers keep the precision they were computed at."""
    return json.dumps(scorecard, indent=2, allow_nan=False)

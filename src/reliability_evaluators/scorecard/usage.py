"""Token usage: what was measured, in tokens, of the model use behind a result.

Pure: no I/O. Usage is metadata. Nothing here reads a metric value or an acceptance
assessment, and nothing that scores or assesses reads what is reported here.

A use is one thing that could have used a model: the invocation of the agent for an Attempt,
the confidence request that followed it, preparation the customer did with a model, or the
scoring of an Evaluator. A total counts each use once. A use whose count was not reported
is missing, with a reason, and is never counted as zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..evaluators import ScoringPath
from ..evidence import Attempt, ConditionRole, ExecutionStatus, TaskEvidence

UNIT = "tokens"
MEANING = (
    "Measured model-token usage, reported after execution, in tokens. It is metadata: it is not a reliability "
    "metric, and it takes no part in any metric value or acceptance assessment. It is not an estimate, not an "
    "amount of money and not a limit. A count that was not reported is missing, with a reason, and is never "
    "counted as zero."
)
SCOPE = (
    "Covered: the invocation of the agent for each Attempt, the confidence request that followed it, the "
    "scoring of each Evaluator, and preparation that the customer declares usage for. The offering generates "
    "no task variations, so what generating them used is reported only where the customer declares it. A count "
    "is the total the platform reported for a request: usage of a model behind a tool is in it only where the "
    "platform includes it. This is not a report of all cost."
)
EVALUATOR_TOTALS = (
    "The total of an Evaluator covers every Attempt it was given, each with its confidence follow-up, the "
    "shared preparation of what those Attempts were made under, and its own scoring. A total labelled "
    "overlapping includes usage that the total of another Evaluator includes too. Such totals are not additive: "
    "adding them counts what they share more than once. The sweep total counts each use once."
)


class Basis(str, Enum):
    """What the count of one use rests on."""

    # A count was reported for the use.
    REPORTED = "reported"
    # Nothing used a model, so the count is known to be zero. It is not a reported count.
    NO_MODEL_USE = "no_model_use"
    # No count is known. The use records why.
    MISSING = "missing"


class Coverage(str, Enum):
    """How much of what a total covers was reported."""

    # No use is missing. The total is the sum of every reported count.
    COMPLETE = "complete"
    # Some uses are missing. The total is the sum of the reported counts only.
    PARTIAL = "partial"
    # Uses are missing and none was reported. There is no total, and it is not zero.
    UNAVAILABLE = "unavailable"


def evaluator_token_usage(tasks: Iterable[TaskEvidence]) -> dict[str, Any]:
    """The token usage a standalone Evaluator reports: of each Attempt it is given, and their total.

    `tasks` is the grouped Attempt evidence the Evaluator is called with. The result is
    JSON-compatible data. It needs no scorecard and no sweep.

    The usage of an Attempt is read from what its agent target recorded of the invocation,
    and of the confidence request that followed it: the `total_tokens` of the `token_usage`
    there. A resource measurement of token usage is evidence for resource consistency and
    is not read here.
    """
    attempts = [attempt_token_usage(attempt) for task in tasks for attempt in task.attempts]
    return {
        "meaning": MEANING,
        "unit": UNIT,
        "total": _total(_uses_of(attempts)),
        "scoring": code_scoring_use(),
        "attempts": attempts,
    }


@dataclass(frozen=True, kw_only=True)
class PreparationUsage:
    """What preparing the variation of experiments used, as the customer reports it.

    The offering generates no task variations. A customer who generated some with a model,
    such as the prompt variants of an experiment, declares here what that used, because
    nothing else can know it. `experiment_ids` names the experiments whose variation was
    prepared. `source` says where the count comes from.

    `total_tokens` is the count. A customer who knows that a model was used and not how
    much gives `missing_reason` instead: the usage is then reported as missing.
    """

    preparation_id: str
    description: str
    source: str
    experiment_ids: Sequence[str]
    total_tokens: int | None = None
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("preparation_id", "description", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"preparation usage is identified, described and records its source: {name} is {value!r}")
        this = f"the preparation {self.preparation_id!r}"
        experiment_ids = self.experiment_ids
        if (
            isinstance(experiment_ids, str)
            or not isinstance(experiment_ids, Sequence)
            or not experiment_ids
            or any(not isinstance(one, str) or not one.strip() for one in experiment_ids)
        ):
            raise ValueError(
                f"{this} names the experiments whose variation it prepared, by experiment identity. "
                f"Got {experiment_ids!r}"
            )
        object.__setattr__(self, "experiment_ids", tuple(experiment_ids))
        reason = self.missing_reason
        if (self.total_tokens is None) == (reason is None):
            raise ValueError(
                f"{this} reports either its total_tokens or its missing_reason: a count that is not known "
                "is missing with a reason, and is never zero"
            )
        if self.total_tokens is not None and not _is_count(self.total_tokens):
            raise ValueError(f"{this} reports a whole number of tokens, 0 or more. Got {self.total_tokens!r}")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError(f"{this} says why its usage is missing. Got {reason!r}")

    def to_dict(self) -> dict[str, Any]:
        use = _reported(self.total_tokens) if self.missing_reason is None else _missing(self.missing_reason)
        return {
            "preparation_id": self.preparation_id,
            "description": self.description,
            "source": self.source,
            "experiment_ids": list(self.experiment_ids),
            **use,
        }


def read_preparation_usage(recorded: Any) -> tuple[PreparationUsage, ...]:
    """The preparation usage that a scorecard reports as `recorded`, as it was declared.

    Raises ValueError for what is not a list of preparations as this version reports them.
    """
    if not isinstance(recorded, list) or not all(isinstance(entry, Mapping) for entry in recorded):
        raise ValueError(f"shared preparation is reported as a list of preparations. Got {recorded!r}")
    try:
        return tuple(
            PreparationUsage(
                preparation_id=entry["preparation_id"],
                description=entry["description"],
                source=entry["source"],
                experiment_ids=entry["experiment_ids"],
                total_tokens=entry["total_tokens"],
                missing_reason=entry["reason"],
            )
            for entry in recorded
        )
    except KeyError as missing:
        raise ValueError(f"a shared preparation is reported without its {missing.args[0]}") from None


@dataclass(frozen=True, kw_only=True)
class EvaluatorUse:
    """What one Evaluator of a sweep used: the Attempts it was given, and its own scoring."""

    metric_id: str
    # Identities of the Attempts the Evaluator was given, whatever became of each.
    attempt_ids: tuple[str, ...]
    # The use that scoring the Evaluator was, as `code_scoring_use` or `managed_scoring_use` gives it.
    scoring: Mapping[str, Any]


def sweep_token_usage(
    attempts: Sequence[Attempt],
    evaluators: Sequence[EvaluatorUse] = (),
    preparation_usage: Sequence[PreparationUsage] = (),
) -> dict[str, Any]:
    """The token usage of an Evaluator sweep: of each Attempt, of each Evaluator, and of the sweep as a whole.

    The sweep total counts each use once, whichever Evaluators used it. The total of an
    Evaluator is the usage of every Attempt it was given, of the shared preparation of what
    those Attempts were made under, and of its own scoring. Where another Evaluator was
    given any of the same Attempts or preparation, both totals include it: such a total is
    labelled overlapping and not additive.

    `preparation_usage` is what the customer reports for preparing the variation of
    experiments. It is listed apart from the Attempts. An Evaluator used a preparation when
    it was given an Attempt made under the variation of an experiment the preparation names.
    """
    by_identity = {attempt.attempt_id: attempt for attempt in attempts}
    used = {attempt_id: attempt_token_usage(attempt) for attempt_id, attempt in by_identity.items()}
    for attempt_id, entry in used.items():
        entry["used_by"] = [evaluator.metric_id for evaluator in evaluators if attempt_id in evaluator.attempt_ids]
    prepared_for = {
        evaluator.metric_id: [
            preparation.preparation_id
            for preparation in preparation_usage
            if any(_made_under_the_variation(by_identity[one], preparation) for one in evaluator.attempt_ids)
        ]
        for evaluator in evaluators
    }
    preparations = {
        preparation.preparation_id: {
            **preparation.to_dict(),
            "used_by": [
                evaluator.metric_id
                for evaluator in evaluators
                if preparation.preparation_id in prepared_for[evaluator.metric_id]
            ],
        }
        for preparation in preparation_usage
    }
    every_use = [
        *_uses_of(used.values()),
        *map(_preparation_use, preparations.values()),
        *map(_scoring_of, evaluators),
    ]
    return {
        "meaning": MEANING,
        "scope": SCOPE,
        "unit": UNIT,
        "sweep": _total(every_use),
        "evaluator_totals": EVALUATOR_TOTALS,
        "evaluators": [_evaluator_usage(evaluator, used, preparations, prepared_for) for evaluator in evaluators],
        "shared_preparation": list(preparations.values()),
        "attempts": list(used.values()),
    }


def _made_under_the_variation(attempt: Attempt, preparation: PreparationUsage) -> bool:
    return attempt.condition_role is ConditionRole.VARIATION and attempt.experiment_id in preparation.experiment_ids


def _evaluator_usage(
    evaluator: EvaluatorUse,
    used: Mapping[str, Mapping[str, Any]],
    preparations: Mapping[str, Mapping[str, Any]],
    prepared_for: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """What one Evaluator used, and whether its total includes what the total of another includes too."""
    preparation_ids = prepared_for[evaluator.metric_id]
    overlaps_with = sorted(
        {
            other
            for attempt_id in evaluator.attempt_ids
            for other in used[attempt_id]["used_by"]
            if other != evaluator.metric_id
        }
        | {
            other
            for preparation_id in preparation_ids
            for other in preparations[preparation_id]["used_by"]
            if other != evaluator.metric_id
        },
        # In the order the Evaluators are reported in.
        key=list(prepared_for).index,
    )
    uses = [
        *_uses_of(used[attempt_id] for attempt_id in evaluator.attempt_ids),
        *(_preparation_use(preparations[preparation_id]) for preparation_id in preparation_ids),
        _scoring_of(evaluator),
    ]
    return {
        "metric_id": evaluator.metric_id,
        "total": _total(uses),
        # A total that overlaps with another is not additive: adding them counts what they share twice.
        "overlapping": bool(overlaps_with),
        "additive": not overlaps_with,
        "overlaps_with": overlaps_with,
        "attempt_ids": list(evaluator.attempt_ids),
        "preparation_ids": list(preparation_ids),
        "scoring": dict(evaluator.scoring),
    }


def _preparation_use(preparation: Mapping[str, Any]) -> tuple[dict[str, Any], Mapping[str, Any]]:
    return {"use": "shared_preparation", "preparation_id": preparation["preparation_id"]}, preparation


def _scoring_of(evaluator: EvaluatorUse) -> tuple[dict[str, Any], Mapping[str, Any]]:
    return {"use": "scoring", "metric_id": evaluator.metric_id}, evaluator.scoring


def code_scoring_use() -> dict[str, Any]:
    """The use that scoring an Evaluator in the customer's process was: none of a model, so zero is known."""
    return dict(CODE_IN_THE_CUSTOMER_PROCESS)


ON_THE_RESULT = "the usage Foundry reported on the result of this Evaluator in its managed run"
PER_MODEL_USAGE = (
    "the per-model usage Foundry reported for the managed run of this Evaluator. Rows that repeat the same "
    "counts under another name are counted once"
)
NO_MANAGED_RUN = "no managed run is known for this Evaluator, so no usage was reported for its scoring"
NO_MANAGED_USAGE = (
    "Foundry reported no usage for the managed run of this Evaluator: none on its result and no per-model "
    "usage row. That is not a report of zero"
)
UNREADABLE_ROWS = (
    "the per-model usage Foundry reported for the managed run of this Evaluator is not a list of rows that "
    "each have a total_tokens that is a whole number"
)
ROWS_THAT_DIFFER = (
    "Foundry reported per-model usage rows with different counts for the managed run of this Evaluator. "
    "Whether such rows overlap is not documented, so they are listed as reported and are not summed"
)


def managed_scoring_use(reported: Any, provenance: Mapping[str, Any] | None) -> dict[str, Any]:
    """The use that scoring an Evaluator in its managed run was, from what Foundry reported of that run.

    `reported` is what managed scoring kept of the run's usage: the usage on the result of
    the Evaluator, and the per-model rows of the run. None: no run is known. `provenance`
    identifies the run. Each Evaluator has a managed run of its own, so what a run reports
    is the scoring of that Evaluator alone.

    The usage on the result is counted when it states a total, and the rows are then not
    added to it: they are another view of the same run. Without it, rows that repeat the
    same counts under another name are one use. Rows with different counts are not summed,
    because how they overlap is not documented: the count is then missing. A run that
    reports nothing is missing too, and is never zero. A zero is counted when it is reported.
    """
    provenance = provenance or {}
    identifiers = {
        "scoring_path": ScoringPath.FOUNDRY_MANAGED.value,
        "source": None,
        **{name: provenance.get(name) for name in ("eval_id", "run_id", "output_item_id")},
        "reported": reported,
    }
    if reported is None:
        return _missing(NO_MANAGED_RUN, **identifiers)
    on_result = reported.get("result_sample_usage") if isinstance(reported, Mapping) else None
    if isinstance(on_result, Mapping) and _is_count(on_result.get("total_tokens")):
        return _reported(on_result["total_tokens"], **{**identifiers, "source": ON_THE_RESULT})
    rows = reported.get("per_model_usage") if isinstance(reported, Mapping) else reported
    if rows is None or rows == []:
        return _missing(NO_MANAGED_USAGE, **identifiers)
    if not isinstance(rows, list) or not all(
        isinstance(row, Mapping) and _is_count(row.get("total_tokens")) for row in rows
    ):
        return _missing(UNREADABLE_ROWS, **identifiers)
    counts: list[dict[str, Any]] = []
    for row in rows:
        counted = {name: count for name, count in row.items() if name != "model_name"}
        if counted not in counts:
            counts.append(counted)
    if len(counts) > 1:
        return _missing(ROWS_THAT_DIFFER, **identifiers)
    return _reported(counts[0]["total_tokens"], **{**identifiers, "source": PER_MODEL_USAGE})


def attempt_token_usage(attempt: Attempt) -> dict[str, Any]:
    """What one Attempt used: the invocation of the agent for it, and the confidence request that followed it.

    The two are separate requests and stay two uses. `confidence_follow_up` is None when no
    confidence request was sent for the Attempt.
    """
    if attempt.execution_status is ExecutionStatus.SETUP_FAILED and attempt.invocation is None:
        invocation = _no_model_use(NOT_INVOKED, response_id=None, request_ids={})
    else:
        invocation = _recorded_use(attempt.invocation, "the invocation")
    follow_up = None
    if attempt.confidence_invocation is not None:
        follow_up = _recorded_use(attempt.confidence_invocation, "the confidence request")
    return {
        "attempt_id": attempt.attempt_id,
        "task_id": attempt.task_id,
        "execution_status": attempt.execution_status.value,
        "retry_of": attempt.retry_of,
        **_sum_of(use for use in (invocation, follow_up) if use is not None),
        _INVOCATION: invocation,
        _FOLLOW_UP: follow_up,
    }


# The uses of an Attempt, by the name each has in the usage of the Attempt.
_INVOCATION, _FOLLOW_UP = "invocation", "confidence_follow_up"
_USES_OF_AN_ATTEMPT = {_INVOCATION: "agent_invocation", _FOLLOW_UP: "confidence_follow_up"}


def _recorded_use(recorded: Mapping[str, Any] | None, of: str) -> dict[str, Any]:
    """The use an agent target recorded, with the identifiers the platform returned for it.

    The count is the `total_tokens` of the `token_usage` the target recorded, as the platform
    reported it. Without one the use is missing, for the reason the target recorded in
    `token_usage_missing` when it recorded one. `of` names the request.
    """
    recorded = recorded or {}
    identifiers = {"response_id": recorded.get("response_id"), "request_ids": recorded.get("request_ids") or {}}
    if not recorded:
        return _missing(f"the agent target recorded nothing of {of}", **identifiers)
    usage = recorded.get("token_usage")
    if usage is None:
        why = recorded.get("token_usage_missing")
        if not isinstance(why, str) or not why.strip():
            why = f"the agent target recorded no token usage for {of}"
        return _missing(why, **identifiers)
    total = usage.get("total_tokens") if isinstance(usage, Mapping) else None
    if not _is_count(total):
        # Nothing is derived from the parts of a usage that states no total.
        return _missing(
            f"the token usage the agent target recorded for {of} has no total_tokens that is a whole number",
            **identifiers,
        )
    return _reported(total, **identifiers)


def _reported(total_tokens: int, **identifiers: Any) -> dict[str, Any]:
    return {"basis": Basis.REPORTED.value, "total_tokens": total_tokens, "reason": None, **identifiers}


def _no_model_use(reason: str, **identifiers: Any) -> dict[str, Any]:
    return {"basis": Basis.NO_MODEL_USE.value, "total_tokens": 0, "reason": reason, **identifiers}


# A customer control failed before the agent target was called.
NOT_INVOKED = "the agent was not invoked for this Attempt"
# The canonical metric definitions are code. Scoring with one in this process is known to use no tokens.
CODE_IN_THE_CUSTOMER_PROCESS = _no_model_use(
    "the Evaluator is calculated by code in the customer's process, which uses no model",
    scoring_path=ScoringPath.CUSTOMER_PROCESS.value,
)


def _missing(reason: str, **identifiers: Any) -> dict[str, Any]:
    return {"basis": Basis.MISSING.value, "total_tokens": None, "reason": reason, **identifiers}


def _is_count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _uses_of(attempts: Iterable[Mapping[str, Any]]) -> list[tuple[dict[str, Any], Mapping[str, Any]]]:
    """The uses of Attempts, each with what names it where it is listed as missing."""
    return [
        ({"use": named, "attempt_id": attempt["attempt_id"]}, attempt[use])
        for attempt in attempts
        for use, named in _USES_OF_AN_ATTEMPT.items()
        if attempt[use] is not None
    ]


def _sum_of(uses: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The coverage and the sum of the counts that were reported for these uses."""
    uses = list(uses)
    reported = [use["total_tokens"] for use in uses if use["basis"] == Basis.REPORTED.value]
    missing = sum(use["basis"] == Basis.MISSING.value for use in uses)
    if not missing:
        coverage = Coverage.COMPLETE
    elif reported:
        coverage = Coverage.PARTIAL
    else:
        coverage = Coverage.UNAVAILABLE
    return {
        "coverage": coverage.value,
        "total_tokens": None if coverage is Coverage.UNAVAILABLE else sum(reported),
    }


def _total(uses: Iterable[tuple[dict[str, Any], Mapping[str, Any]]]) -> dict[str, Any]:
    """A total over uses, each counted once, with what it is missing and why."""
    uses = list(uses)
    missing = [{**named, "reason": use["reason"]} for named, use in uses if use["basis"] == Basis.MISSING.value]
    return {
        **_sum_of(use for _, use in uses),
        "uses_reported": sum(use["basis"] == Basis.REPORTED.value for _, use in uses),
        "uses_missing": len(missing),
        "missing": missing,
    }

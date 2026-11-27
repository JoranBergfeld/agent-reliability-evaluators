"""Token usage: what was measured, in tokens, of the model use behind a result.

Pure: no I/O. Usage is metadata. Nothing here reads a metric value or an acceptance
assessment, and nothing that scores or assesses reads what is reported here.

A use is one thing that could have used a model: the invocation of the agent for an Attempt,
the confidence request that followed it. A total counts each use once. A use whose count
was not reported is missing, with a reason, and is never counted as zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..evaluators import ScoringPath
from ..evidence import Attempt, ExecutionStatus, TaskEvidence

UNIT = "tokens"
MEANING = (
    "Measured model-token usage, reported after execution, in tokens. It is metadata: it is not a reliability "
    "metric, and it takes no part in any metric value or acceptance assessment. It is not an estimate, not an "
    "amount of money and not a limit. A count that was not reported is missing, with a reason, and is never "
    "counted as zero."
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
    """
    attempts = [attempt_token_usage(attempt) for task in tasks for attempt in task.attempts]
    return {
        "meaning": MEANING,
        "unit": UNIT,
        "total": _total(_uses_of(attempts)),
        "scoring": dict(CODE_IN_THE_CUSTOMER_PROCESS),
        "attempts": attempts,
    }


@dataclass(frozen=True, kw_only=True)
class EvaluatorUse:
    """What one Evaluator of a sweep used: the Attempts it was given, and its own scoring."""

    metric_id: str
    # Identities of the Attempts the Evaluator was given, whatever became of each.
    attempt_ids: tuple[str, ...]
    # The use that scoring the Evaluator was, as `scoring_use` gives it.
    scoring: Mapping[str, Any]


def sweep_token_usage(attempts: Sequence[Attempt], evaluators: Sequence[EvaluatorUse] = ()) -> dict[str, Any]:
    """The token usage of an Evaluator sweep: of each Attempt, of each Evaluator, and of the sweep as a whole.

    The sweep total counts each use once, whichever Evaluators used it. The total of an
    Evaluator is the usage of every Attempt it was given and of its own scoring. Where
    another Evaluator was given any of the same Attempts, both totals include them: such a
    total is labelled overlapping and not additive.
    """
    used = {attempt.attempt_id: attempt_token_usage(attempt) for attempt in attempts}
    for attempt_id, entry in used.items():
        entry["used_by"] = [evaluator.metric_id for evaluator in evaluators if attempt_id in evaluator.attempt_ids]
    scoring = [_scoring_of(evaluator) for evaluator in evaluators]
    return {
        "meaning": MEANING,
        "unit": UNIT,
        "sweep": _total([*_uses_of(used.values()), *scoring]),
        "evaluators": [_evaluator_usage(evaluator, used, evaluators) for evaluator in evaluators],
        "attempts": list(used.values()),
    }


def _evaluator_usage(
    evaluator: EvaluatorUse, used: Mapping[str, Mapping[str, Any]], evaluators: Sequence[EvaluatorUse]
) -> dict[str, Any]:
    """What one Evaluator used, and whether its total includes what the total of another includes too."""
    given = set(evaluator.attempt_ids)
    overlaps_with = [
        other.metric_id for other in evaluators if other is not evaluator and not given.isdisjoint(other.attempt_ids)
    ]
    uses = [*_uses_of(used[attempt_id] for attempt_id in evaluator.attempt_ids), _scoring_of(evaluator)]
    return {
        "metric_id": evaluator.metric_id,
        "total": _total(uses),
        # A total that overlaps with another is not additive: adding them counts what they share twice.
        "overlapping": bool(overlaps_with),
        "additive": not overlaps_with,
        "overlaps_with": overlaps_with,
        "attempt_ids": list(evaluator.attempt_ids),
        "scoring": dict(evaluator.scoring),
    }


def _scoring_of(evaluator: EvaluatorUse) -> tuple[dict[str, Any], Mapping[str, Any]]:
    return {"use": "scoring", "metric_id": evaluator.metric_id}, evaluator.scoring


def scoring_use(scoring_path: ScoringPath) -> dict[str, Any]:
    """The use that scoring an Evaluator on a scoring path was."""
    return dict(CODE_IN_THE_CUSTOMER_PROCESS)


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

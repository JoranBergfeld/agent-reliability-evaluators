"""Token usage: what was measured, in tokens, of the model use behind a result.

Pure: no I/O. Usage is metadata. Nothing here reads a metric value or an acceptance
assessment, and nothing that scores or assesses reads what is reported here.

A use is one thing that could have used a model: the invocation of the agent for an Attempt,
the confidence request that followed it. A total counts each use once. A use whose count
was not reported is missing, with a reason, and is never counted as zero.
"""

from __future__ import annotations

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
        "scoring": CODE_IN_THE_CUSTOMER_PROCESS,
        "attempts": attempts,
    }


def sweep_token_usage(attempts: Sequence[Attempt]) -> dict[str, Any]:
    """The token usage of an Evaluator sweep: of each Attempt, and of the sweep as a whole.

    The sweep total counts each use once, whichever Evaluators used it.
    """
    used = [attempt_token_usage(attempt) for attempt in attempts]
    return {
        "meaning": MEANING,
        "unit": UNIT,
        "sweep": _total(_uses_of(used)),
        "attempts": used,
    }


def attempt_token_usage(attempt: Attempt) -> dict[str, Any]:
    """What one Attempt used: the invocation of the agent for it."""
    if attempt.execution_status is ExecutionStatus.SETUP_FAILED and attempt.invocation is None:
        invocation = _no_model_use(NOT_INVOKED, response_id=None, request_ids={})
    else:
        invocation = _recorded_use(attempt.invocation)
    return {
        "attempt_id": attempt.attempt_id,
        "task_id": attempt.task_id,
        "execution_status": attempt.execution_status.value,
        "retry_of": attempt.retry_of,
        **_sum_of([invocation]),
        "invocation": invocation,
    }


NOTHING_RECORDED = "the agent target recorded nothing of the invocation"
NO_USAGE_REPORTED = "the agent target recorded no token usage for the invocation"
NO_TOTAL = "the token usage the agent target recorded has no total_tokens that is a whole number"


def _recorded_use(recorded: Mapping[str, Any] | None) -> dict[str, Any]:
    """The use an agent target recorded, with the identifiers the platform returned for it.

    The count is the `total_tokens` of the `token_usage` the target recorded, as the platform
    reported it. Without one the use is missing, for the reason the target recorded in
    `token_usage_missing` when it recorded one.
    """
    recorded = recorded or {}
    identifiers = {"response_id": recorded.get("response_id"), "request_ids": recorded.get("request_ids") or {}}
    if not recorded:
        return _missing(NOTHING_RECORDED, **identifiers)
    usage = recorded.get("token_usage")
    if usage is None:
        why = recorded.get("token_usage_missing")
        return _missing(why if isinstance(why, str) and why.strip() else NO_USAGE_REPORTED, **identifiers)
    total = usage.get("total_tokens") if isinstance(usage, Mapping) else None
    if not _is_count(total):
        return _missing(NO_TOTAL, **identifiers)
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
    return [({"use": "agent_invocation", "attempt_id": attempt["attempt_id"]}, attempt["invocation"]) for attempt in attempts]


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

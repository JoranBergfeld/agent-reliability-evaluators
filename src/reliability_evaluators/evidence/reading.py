"""Reading attempt-level evidence back from the JSON-compatible data it is written as."""

from __future__ import annotations

from typing import Any, Mapping

from .attempt import Attempt, ResourceMeasurement, TaskOutcome
from .confidence import ConfidenceRating, ConfidenceUnavailable


def read_attempt(recorded: Mapping[str, Any]) -> Attempt:
    """The Attempt that `Attempt.to_dict` wrote as `recorded`."""
    outcome = recorded["task_outcome"]
    return Attempt(
        attempt_id=recorded["attempt_id"],
        task_id=recorded["task_id"],
        execution_status=recorded["execution_status"],
        execution_error=recorded["execution_error"],
        task_outcome=None if outcome is None else TaskOutcome(**outcome),
        resource_measurements=[ResourceMeasurement(**measurement) for measurement in recorded["resource_measurements"]],
        confidence=_confidence(recorded["confidence"]),
    )


def _confidence(recorded: Mapping[str, Any] | None) -> ConfidenceRating | ConfidenceUnavailable | None:
    if recorded is None:
        return None
    if recorded["rating"] is None:
        return ConfidenceUnavailable(
            reason=recorded["unavailable_reason"], original_response=recorded["original_response"]
        )
    return ConfidenceRating(rating=recorded["rating"], original_response=recorded["original_response"])

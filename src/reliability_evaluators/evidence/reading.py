"""Reading attempt-level evidence back from the JSON-compatible data it is written as."""

from __future__ import annotations

from typing import Any, Mapping

from .actions import ActionEvent, ActionEvidence
from .attempt import (
    Attempt,
    OperationalConstraint,
    ResourceMeasurement,
    SupersededTaskOutcome,
    TaskOutcome,
    ViolationAssessment,
)
from .confidence import ConfidenceRating, ConfidenceUnavailable


def read_attempt(recorded: Mapping[str, Any]) -> Attempt:
    """The Attempt that `Attempt.to_dict` wrote as `recorded`."""
    return Attempt(
        attempt_id=recorded["attempt_id"],
        task_id=recorded["task_id"],
        execution_status=recorded["execution_status"],
        execution_error=recorded["execution_error"],
        task_outcome=_task_outcome(recorded["task_outcome"]),
        superseded_task_outcomes=[
            SupersededTaskOutcome(
                task_outcome=_task_outcome(superseded["task_outcome"]),
                correction_provenance=superseded["correction_provenance"],
            )
            for superseded in recorded["superseded_task_outcomes"]
        ],
        action_evidence=_action_evidence(recorded["action_evidence"]),
        resource_measurements=[ResourceMeasurement(**measurement) for measurement in recorded["resource_measurements"]],
        confidence=_confidence(recorded["confidence"]),
        operational_constraints=[OperationalConstraint(**constraint) for constraint in recorded["operational_constraints"]],
        violation_assessments=[ViolationAssessment(**assessment) for assessment in recorded["violation_assessments"]],
    )


def _task_outcome(recorded: Mapping[str, Any] | None) -> TaskOutcome | None:
    return None if recorded is None else TaskOutcome(**recorded)


def _action_evidence(recorded: Mapping[str, Any] | None) -> ActionEvidence | None:
    if recorded is None:
        return None
    return ActionEvidence(source=recorded["source"], events=[ActionEvent(**event) for event in recorded["events"]])


def _confidence(recorded: Mapping[str, Any] | None) -> ConfidenceRating | ConfidenceUnavailable | None:
    if recorded is None:
        return None
    if recorded["rating"] is None:
        return ConfidenceUnavailable(
            reason=recorded["unavailable_reason"], original_response=recorded["original_response"]
        )
    return ConfidenceRating(rating=recorded["rating"], original_response=recorded["original_response"])

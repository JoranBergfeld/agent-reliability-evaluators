"""Reading attempt-level evidence back from the JSON-compatible data it is written as.

The offering keeps no evidence store, so evidence that is needed again is supplied by the
customer from their platform. What is supplied is read strictly: an Attempt is accepted
only when it is exactly what `Attempt.to_dict` writes for the Attempt it is read as.
Nothing is repaired, defaulted or dropped.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .actions import ActionEvent, ActionEvidence
from .attempt import (
    Attempt,
    OperationalConstraint,
    ResourceMeasurement,
    SeverityRating,
    SupersededTaskOutcome,
    TaskOutcome,
    ViolationAssessment,
)
from .confidence import ConfidenceRating, ConfidenceUnavailable
from .errors import EvidenceError
from .experiment import Condition, LifecyclePoint, LifecycleStep, ObservedCondition


def read_attempts(recorded: Any) -> tuple[Attempt, ...]:
    """The Attempts that were written as the list `recorded`, in the same order.

    Raises EvidenceError, naming the Attempt, for a record that is not valid evidence or
    that would not be written back unchanged, such as one that carries evidence this
    version does not know.
    """
    if not isinstance(recorded, list):
        raise EvidenceError(f"attempt-level evidence is a list of Attempts. Got {recorded!r}")
    return tuple(_read_attempt(record, position) for position, record in enumerate(recorded, start=1))


def _read_attempt(recorded: Any, position: int) -> Attempt:
    attempt_id = recorded.get("attempt_id") if isinstance(recorded, Mapping) else None
    name = f"Attempt {attempt_id!r}" if isinstance(attempt_id, str) and attempt_id else f"the Attempt at position {position}"
    try:
        attempt = _attempt(recorded)
        differing = _differing_keys(attempt.to_dict(), recorded)
    except (ValueError, TypeError) as error:
        raise EvidenceError(f"{name} is not valid evidence: {error}") from error
    if differing:
        raise EvidenceError(
            f"{name} is not what this version records for it. It differs in: {', '.join(differing)}"
        )
    return attempt


def _differing_keys(written: Mapping[str, Any], recorded: Mapping[str, Any]) -> list[str]:
    """The keys under which the record supplied is not the record this version writes."""
    return sorted(
        key
        for key in {*written, *recorded}
        if key not in written or key not in recorded or _as_json(written[key]) != _as_json(recorded[key])
    )


def _as_json(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def _attempt(recorded: Any) -> Attempt:
    fields = _fields(
        "an Attempt",
        recorded,
        "attempt_id",
        "task_id",
        "execution_status",
        "execution_error",
        "task_outcome",
        "superseded_task_outcomes",
        "action_evidence",
        "resource_measurements",
        "confidence",
        "operational_constraints",
        "violation_assessments",
        "experiment_id",
        "starting_state",
        "intended_condition",
        "observed_condition",
        "lifecycle",
    )
    intended_condition, condition_role = _intended_condition(fields["intended_condition"])
    return Attempt(
        **{
            **fields,
            "task_outcome": _task_outcome(fields["task_outcome"]),
            "superseded_task_outcomes": [
                _superseded_task_outcome(record)
                for record in _each("superseded task outcomes", fields["superseded_task_outcomes"])
            ],
            "action_evidence": _action_evidence(fields["action_evidence"]),
            "resource_measurements": [
                ResourceMeasurement(
                    **_fields(
                        "a resource measurement", record, "resource_type", "value", "unit", "boundary", "reliably_sourced"
                    )
                )
                for record in _each("resource measurements", fields["resource_measurements"])
            ],
            "confidence": _confidence(fields["confidence"]),
            "operational_constraints": [
                OperationalConstraint(**_fields("an operational constraint", record, "constraint_id", "description"))
                for record in _each("operational constraints", fields["operational_constraints"])
            ],
            "violation_assessments": [
                _violation_assessment(record)
                for record in _each("violation assessments", fields["violation_assessments"])
            ],
            "intended_condition": intended_condition,
            "condition_role": condition_role,
            "observed_condition": _observed_condition(fields["observed_condition"]),
            "lifecycle": tuple(_lifecycle_step(record) for record in _each("lifecycle steps", fields["lifecycle"])),
        }
    )


def _task_outcome(recorded: Any) -> TaskOutcome | None:
    return None if recorded is None else TaskOutcome(**_fields("a task outcome", recorded, "success", "source"))


def _superseded_task_outcome(recorded: Any) -> SupersededTaskOutcome:
    fields = _fields("a superseded task outcome", recorded, "task_outcome", "correction_provenance")
    return SupersededTaskOutcome(**{**fields, "task_outcome": _task_outcome(fields["task_outcome"])})


def _action_evidence(recorded: Any) -> ActionEvidence | None:
    if recorded is None:
        return None
    fields = _fields("action evidence", recorded, "source", "events")
    events = [
        ActionEvent(**_fields("an action event", event, "tool_name", "arguments", "result", "error"))
        for event in _each("action events", fields["events"])
    ]
    return ActionEvidence(source=fields["source"], events=events)


def _confidence(recorded: Any) -> ConfidenceRating | ConfidenceUnavailable | None:
    if recorded is None:
        return None
    # The normalized rating is not read: it follows from the rating, and a record whose
    # normalized rating is another number is not written back unchanged.
    fields = _fields("confidence evidence", recorded, "rating", "original_response", "unavailable_reason")
    if fields["rating"] is None:
        return ConfidenceUnavailable(reason=fields["unavailable_reason"], original_response=fields["original_response"])
    return ConfidenceRating(rating=fields["rating"], original_response=fields["original_response"])


def _violation_assessment(recorded: Any) -> ViolationAssessment:
    fields = _fields("a violation assessment", recorded, "constraint_id", "violated", "evidence", "source", "severity")
    severity = fields["severity"]
    if severity is not None:
        severity = SeverityRating(**_fields("a severity rating", severity, "label", "level"))
    return ViolationAssessment(**{**fields, "severity": severity})


def _intended_condition(recorded: Any) -> tuple[Condition | None, Any]:
    """The intended condition and the part it plays in its experiment, which are recorded together."""
    if recorded is None:
        return None, None
    fields = _fields("an intended condition", recorded, "role", "condition_id", "description", "parameters")
    role = fields.pop("role")
    return Condition(**fields), role


def _observed_condition(recorded: Any) -> ObservedCondition | None:
    if recorded is None:
        return None
    return ObservedCondition(**_fields("an observed condition", recorded, "met", "source", "observations"))


def _lifecycle_step(recorded: Any) -> LifecycleStep:
    # Whether the step succeeded is not read: it follows from its error.
    fields = _fields("a lifecycle step", recorded, "point", "error")
    if fields["error"] is not None and not isinstance(fields["error"], str):
        raise EvidenceError(f"the error of a lifecycle step is text or None. Got {fields['error']!r}")
    try:
        point = LifecyclePoint(fields["point"])
    except ValueError:
        raise EvidenceError(f"{fields['point']!r} is not a point at which a customer control is invoked") from None
    return LifecycleStep(point=point, error=fields["error"])


def _fields(what: str, recorded: Any, *names: str) -> dict[str, Any]:
    """The named fields of one record. Fields it has besides these are found when it is written back."""
    if not isinstance(recorded, Mapping):
        raise EvidenceError(f"{what} is recorded as an object. Got {recorded!r}")
    missing = [name for name in names if name not in recorded]
    if missing:
        raise EvidenceError(f"{what} is recorded without its {', '.join(missing)}")
    return {name: recorded[name] for name in names}


def _each(what: str, recorded: Any) -> list[Any]:
    if not isinstance(recorded, list):
        raise EvidenceError(f"{what} are recorded as a list. Got {recorded!r}")
    return recorded

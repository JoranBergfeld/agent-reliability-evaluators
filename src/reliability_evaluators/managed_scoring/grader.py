"""What a derived Foundry evaluator runs around the canonical definition, and the encoding of the evidence it reads.

Pure: standard library only, no I/O. This module is copied into every derived artifact, so it
may import only what the Foundry code-evaluator sandbox provides.

Evidence travels as one string per Evaluator: `<sha256 of the JSON>:<JSON>`. A string reached
the sandbox unchanged when observed, where structured numbers did not. The digest makes any
other alteration a rejection inside the sandbox instead of a value calculated from other evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable, Sequence

from ..evaluators.result import MetricDefinition, MetricResult, MetricState
from ..evidence import Attempt, EvidenceError, TaskEvidence, TaskOutcome

ENCODING_VERSION = 1


class UnavailableThroughManagedScoring(Exception):
    """The metric is not available for the evidence, and a managed evaluator can only return a number.

    Raising fails the managed run. Nothing is returned in place of the value.
    """


def evidence_field(metric_id: str) -> str:
    """The field of a submitted item that holds the evidence of one Evaluator."""
    return f"{metric_id}_evidence"


def encode_evidence(tasks: Iterable[TaskEvidence], attempt_fields: Sequence[str]) -> str:
    """Encode grouped Attempt evidence for transport to a derived evaluator.

    Every Attempt keeps its identity and its Task, and of its evidence the `attempt_fields`,
    as the Attempt records them. Nothing is validated here: the derived evaluator validates.
    """
    payload = json.dumps(
        {
            "encoding": ENCODING_VERSION,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "attempts": [_transported(attempt, attempt_fields) for attempt in task.attempts],
                }
                for task in tasks
            ],
        },
        allow_nan=False,
        separators=(",", ":"),
    )
    return f"{_digest(payload)}:{payload}"


def decode_evidence(encoded: Any) -> tuple[TaskEvidence, ...]:
    """Read grouped Attempt evidence back, as the Evidence model's own records.

    Evidence that is not exactly what `encode_evidence` wrote is rejected, never repaired.
    """
    if not isinstance(encoded, str):
        raise EvidenceError(f"transported evidence is text, got {type(encoded).__name__}")
    digest, separator, payload = encoded.partition(":")
    if not separator or digest != _digest(payload):
        raise EvidenceError("the evidence was altered in transport: it does not match its digest")
    evidence = _record(json.loads(payload, parse_constant=_not_a_number), "encoding", "tasks")
    if evidence["encoding"] != ENCODING_VERSION:
        raise EvidenceError(f"the evidence is in encoding {evidence['encoding']!r}, not {ENCODING_VERSION}")
    return tuple(_task(task) for task in evidence["tasks"])


def managed_value(definition: MetricDefinition, evaluate: Callable[..., MetricResult], item: Any) -> float:
    """What a derived evaluator returns for one submitted item: the metric value.

    The canonical definition validates the evidence and the range of the value. A metric
    that is not available raises, because a number is the only thing that can be returned.
    """
    field = evidence_field(definition.metric.metric_id)
    try:
        encoded = item[field]
    except (KeyError, TypeError, IndexError):
        raise EvidenceError(f"the submitted item has no {field!r}") from None
    result = evaluate(decode_evidence(encoded))
    if result.state is not MetricState.AVAILABLE:
        raise UnavailableThroughManagedScoring(
            f"{definition.metric.metric_id} is {result.state.value}: {result.reason}"
        )
    return float(result.value)


# How each transported field of an Attempt is read back into the Attempt record.
_ATTEMPT_FIELDS: dict[str, Callable[[Any], Any]] = {
    "execution_status": lambda recorded: recorded,
    "task_outcome": lambda recorded: (
        None if recorded is None else TaskOutcome(**_record(recorded, "success", "source"))
    ),
}
_ATTEMPT_IDENTITY = ("attempt_id", "task_id")


def transportable(attempt_fields: Iterable[str]) -> bool:
    """Whether a derived evaluator can read every one of these Attempt fields back."""
    return all(field in _ATTEMPT_FIELDS for field in attempt_fields)


def _transported(attempt: Attempt, attempt_fields: Sequence[str]) -> dict[str, Any]:
    recorded = attempt.to_dict()
    return {field: recorded[field] for field in (*_ATTEMPT_IDENTITY, *attempt_fields)}


def _task(transported: Any) -> TaskEvidence:
    task = _record(transported, "task_id", "attempts")
    return TaskEvidence(task["task_id"], tuple(_attempt(attempt) for attempt in task["attempts"]))


def _attempt(transported: Any) -> Attempt:
    if not isinstance(transported, dict) or any(field not in transported for field in _ATTEMPT_IDENTITY):
        raise EvidenceError(f"a transported Attempt identifies itself and its Task, got {transported!r}")
    evidence = {field: value for field, value in transported.items() if field not in _ATTEMPT_IDENTITY}
    unreadable = sorted(field for field in evidence if field not in _ATTEMPT_FIELDS)
    if unreadable:
        raise EvidenceError(f"a transported Attempt carries {unreadable}, which cannot be read back")
    return Attempt(
        attempt_id=transported["attempt_id"],
        task_id=transported["task_id"],
        **{field: _ATTEMPT_FIELDS[field](value) for field, value in evidence.items()},
    )


def _record(transported: Any, *fields: str) -> dict[str, Any]:
    if not isinstance(transported, dict) or sorted(transported) != sorted(fields):
        raise EvidenceError(
            f"transported evidence with exactly the fields {list(fields)} was expected, got {transported!r}"
        )
    return transported


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _not_a_number(constant: str) -> Any:
    raise EvidenceError(f"transported evidence holds {constant}, which is not a number")

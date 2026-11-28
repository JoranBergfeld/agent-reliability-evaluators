"""What a derived Foundry evaluator runs around the canonical definition, and the encodings it reads and returns.

Pure: standard library only, no I/O. This module is copied into every derived artifact, so it
may import only what the Foundry code-evaluator sandbox provides.

Evidence travels as one string per Evaluator: `<sha256 of the JSON>:<base64 of the deflated JSON>`.
A string reached the sandbox unchanged when observed, where structured numbers did not. The
digest makes any other alteration a rejection inside the sandbox instead of a value calculated
from other evidence. The Attempts in it are written by the Attempt record and read back by the
Evidence model's strict reader, so there is one way to write an Attempt and one way to read it.
What the customer declares for an Evaluator, such as the action types, travels in the same
string, behind the same digest.

A result travels back as numbers, because a number is all a Foundry code evaluator can return.
The evaluator is called once for every row that is submitted, and each row asks for one part of
the result: the plain metric value, the size of the encoded result, or six bytes of it. The
encoded result is the result as the result contract writes it, deflated, behind a digest that
also covers the evidence it was calculated from. So a metric that has no value is returned with
its state, its reason and its counts, and nothing has to raise.
"""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..evaluators.result import CountedReason, EvidenceCount, MetricDefinition, MetricResult, MetricState
from ..evidence import (
    ACTION_TYPE_DECLARATION,
    ActionEvent,
    ActionEvidence,
    ActionTypeMapping,
    Attempt,
    EvidenceError,
    TaskEvidence,
    read_attempts,
)

ENCODING_VERSION = 3

# The field of a submitted item that says which part of the result the row asks for.
RESULT_PART = "result_part"
VALUE_PART = "value"
SIZE_PART = "size"
# Bytes of the encoded result in one returned number. A number of six bytes is a whole number
# below 2^48: a double holds it exactly, and fifteen significant digits write it exactly.
PART_BYTES = 6
DIGEST_BYTES = 12
# What of a result travels. Its metric definition does not: the reader holds the one it derived from.
RESULT_FIELDS = ("metric_id", "state", "value", "reason", "counts", "details", "evidence")


class UnreadableResult(Exception):
    """What came back is not a result that the derived evaluator encoded for the submitted evidence."""


def evidence_field(metric_id: str) -> str:
    """The field of a submitted item that holds the evidence of one Evaluator."""
    return f"{metric_id}_evidence"


def encode_evidence(
    tasks: Iterable[TaskEvidence], attempt_evidence: Sequence[str], declared: Mapping[str, Any] | None = None
) -> str:
    """Encode grouped Attempt evidence for transport to a derived evaluator.

    Every Attempt keeps its identity, its Task and its execution status, and of its other
    evidence only the fields named in `attempt_evidence`. It is written as the Attempt
    record writes itself, so the derived evaluator reads it with the Evidence model's own
    reader. That reader validates: nothing is validated here.

    `declared` is what the customer declared for the Evaluator, by the name the Evaluator
    takes it under, in the order the Evaluator takes them in.
    """
    payload = json.dumps(
        {
            "encoding": ENCODING_VERSION,
            "declared": {name: _DECLARATIONS[name][0](value) for name, value in (declared or {}).items()},
            "tasks": [
                {
                    "task_id": task.task_id,
                    "attempts": [_transported(attempt, attempt_evidence).to_dict() for attempt in task.attempts],
                }
                for task in tasks
            ],
        },
        allow_nan=False,
        separators=(",", ":"),
    )
    # Deflated, because the evidence is submitted once for every part of the result.
    return f"{_digest(payload)}:{base64.b64encode(_deflate(payload.encode('utf-8'))).decode('ascii')}"


def decode_evidence(
    encoded: Any, declarations: Sequence[str] = ()
) -> tuple[tuple[TaskEvidence, ...], tuple[Any, ...]]:
    """Read grouped Attempt evidence back, as the Evidence model's own records, with what was declared.

    `declarations` names what the Evaluator takes besides the evidence. What was declared
    under each name is given in that order. Evidence that is not exactly what
    `encode_evidence` wrote for those declarations is rejected, never repaired.
    """
    if not isinstance(encoded, str):
        raise EvidenceError(f"transported evidence is text, got {type(encoded).__name__}")
    digest, separator, packed = encoded.partition(":")
    try:
        payload = _inflate(base64.b64decode(packed, validate=True)).decode("utf-8")
    except (ValueError, zlib.error):
        raise EvidenceError("the evidence was altered in transport: it cannot be read") from None
    if not separator or digest != _digest(payload):
        raise EvidenceError("the evidence was altered in transport: it does not match its digest")
    evidence = _record(json.loads(payload, parse_constant=_not_a_number), "encoding", "declared", "tasks")
    if evidence["encoding"] != ENCODING_VERSION:
        raise EvidenceError(f"the evidence is in encoding {evidence['encoding']!r}, not {ENCODING_VERSION}")
    if not isinstance(evidence["tasks"], list):
        raise EvidenceError(f"transported Tasks are a list, got {evidence['tasks']!r}")
    declared = _record(evidence["declared"], *declarations)
    tasks = [_record(task, "task_id", "attempts") for task in evidence["tasks"]]
    return (
        tuple(TaskEvidence(task["task_id"], read_attempts(task["attempts"])) for task in tasks),
        tuple(_DECLARATIONS[name][1](declared[name]) for name in declarations),
    )


def managed_value(
    definition: MetricDefinition, evaluate: Callable[..., MetricResult], item: Any, declarations: Sequence[str] = ()
) -> float | int:
    """What a derived evaluator returns for one submitted item: the part of the result the item asks for.

    `declarations` names what `evaluate` takes besides the evidence, which travels with it.

    The canonical definition validates the evidence and the range of the value. Evidence
    that cannot be read raises, as it does in the customer's own process. A metric that has
    no value for the evidence does not: it is a result, and it is returned as one.
    """
    encoded = _field(item, evidence_field(definition.metric.metric_id))
    part = _field(item, RESULT_PART)
    tasks, declared = decode_evidence(encoded, declarations)
    result = evaluate(tasks, *declared)
    if part == VALUE_PART:
        return float(result.value) if result.state is MetricState.AVAILABLE else no_value_marker(definition)
    stream = encode_result(result, encoded)
    if part == SIZE_PART:
        return len(stream)
    if not (isinstance(part, str) and part.isascii() and part.isdigit() and part == str(int(part)) and int(part) > 0):
        raise EvidenceError(f"{part!r} is not a part of a result: {VALUE_PART!r}, {SIZE_PART!r} or a number from 1")
    # A part past the end is zero. Nothing reads it: the size says how many parts there are.
    return int.from_bytes(stream[(int(part) - 1) * PART_BYTES : int(part) * PART_BYTES], "big")


def no_value_marker(definition: MetricDefinition) -> float:
    """What the value part is for a metric that has no value: a number below its scale.

    An evaluator has to return a number. This one is internal: a reader takes the state
    of the metric from the encoded result, and never reports this as a value.
    """
    return float(definition.scale.minimum) - 1.0


def encode_result(result: MetricResult, evidence: str) -> bytes:
    """A result as the bytes a derived evaluator returns part by part.

    It is written as the result contract writes itself, so every number in it is in the
    notation that reads back to the same double. The digest in front also covers the digest
    of the evidence, so a result calculated from other evidence does not pass for this one.
    """
    recorded = result.to_dict()
    payload = json.dumps(
        {"encoding": ENCODING_VERSION, "result": {field: recorded[field] for field in RESULT_FIELDS}},
        allow_nan=False,
        separators=(",", ":"),
    )
    body = _deflate(payload.encode("utf-8"))
    return _result_digest(evidence, body) + body


def result_parts(size: Any) -> int:
    """How many parts an encoded result of that many bytes is returned in."""
    if isinstance(size, float) and size.is_integer():
        size = int(size)
    if isinstance(size, bool) or not isinstance(size, int) or size <= DIGEST_BYTES:
        raise UnreadableResult(f"{size!r} was returned as the size of the encoded result, which is not one")
    return -(-size // PART_BYTES)


def decode_result(definition: MetricDefinition, evidence: str, size: Any, parts: Sequence[Any]) -> MetricResult:
    """Read back the result a derived evaluator returned in parts, as the evaluator computed it.

    `evidence` is the encoded evidence that was submitted, `size` the size part and `parts`
    the numbered parts from 1, as they were returned. Anything that is not exactly what
    `encode_result` wrote for that evidence is rejected, never repaired.
    """
    expected = result_parts(size)
    if len(parts) < expected:
        raise UnreadableResult(f"{len(parts)} parts were returned of an encoded result of {expected}")
    stream = b"".join(
        _part_bytes(number, part, min(PART_BYTES, int(size) - (number - 1) * PART_BYTES))
        for number, part in enumerate(parts[:expected], start=1)
    )
    digest, body = stream[:DIGEST_BYTES], stream[DIGEST_BYTES:]
    if digest != _result_digest(evidence, body):
        raise UnreadableResult(
            "the encoded result does not match its digest: it was altered on the way back, "
            "or it was not calculated from the evidence that was submitted"
        )
    try:
        transported = json.loads(_inflate(body).decode("utf-8"), parse_constant=_not_a_number)
        return _read_result(definition, transported)
    except (EvidenceError, ValueError, TypeError, zlib.error) as error:
        raise UnreadableResult(f"the encoded result is not a result of {definition.metric.metric_id}: {error}") from None


def _read_result(definition: MetricDefinition, transported: Any) -> MetricResult:
    """The transported result as the result contract, which validates its state, value and reason."""
    envelope = _record(transported, "encoding", "result")
    if envelope["encoding"] != ENCODING_VERSION:
        raise EvidenceError(f"the result is in encoding {envelope['encoding']!r}, not {ENCODING_VERSION}")
    result = _record(envelope["result"], *RESULT_FIELDS)
    if result["metric_id"] != definition.metric.metric_id:
        raise EvidenceError(f"it is a result of {result['metric_id']!r}")
    if not isinstance(result["counts"], dict) or not isinstance(result["details"], dict):
        raise EvidenceError("its counts and its details are each recorded by name")
    return MetricResult(
        definition=definition,
        state=MetricState(result["state"]),
        value=result["value"],
        reason=result["reason"],
        counts={unit: _read_count(count) for unit, count in result["counts"].items()},
        details=result["details"],
        attempt_ids=_identities(_record(result["evidence"], "attempt_ids")["attempt_ids"]),
    )


def _read_count(transported: Any) -> EvidenceCount:
    count = _record(transported, "total", "valid", "missing", "excluded")
    for number in (count["total"], count["valid"]):
        if isinstance(number, bool) or not isinstance(number, int):
            raise EvidenceError(f"a count is a whole number, got {number!r}")
    return EvidenceCount(
        total=count["total"],
        valid=count["valid"],
        missing=_read_counted_reasons(count["missing"]),
        excluded=_read_counted_reasons(count["excluded"]),
    )


def _read_counted_reasons(transported: Any) -> tuple[CountedReason, ...]:
    if not isinstance(transported, list):
        raise EvidenceError(f"counted reasons are a list, got {transported!r}")
    entries = [_record(entry, "reason", "count", "ids") for entry in transported]
    for entry in entries:
        if not isinstance(entry["reason"], str) or not isinstance(entry["ids"], list) or entry["count"] != len(entry["ids"]):
            raise EvidenceError(f"a counted reason names what it counts, got {entry!r}")
    return tuple(CountedReason(entry["reason"], _identities(entry["ids"])) for entry in entries)


def _identities(transported: Any) -> tuple[str, ...]:
    if not isinstance(transported, list) or not all(isinstance(identity, str) for identity in transported):
        raise EvidenceError(f"identities are a list of text, got {transported!r}")
    return tuple(transported)


def _part_bytes(number: int, part: Any, length: int) -> bytes:
    if isinstance(part, float) and part.is_integer():
        part = int(part)
    if isinstance(part, bool) or not isinstance(part, int) or not 0 <= part < 256**length:
        raise UnreadableResult(f"{part!r} was returned as part {number} of the encoded result, which is not {length} bytes")
    return part.to_bytes(length, "big")


def _field(item: Any, field: str) -> Any:
    try:
        return item[field]
    except (KeyError, TypeError, IndexError):
        raise EvidenceError(f"the submitted item has no {field!r}") from None


# What every transported Attempt keeps, whatever else its Evaluator is calculated from.
ALWAYS_TRANSPORTED = ("attempt_id", "task_id", "execution_status")


def _transported(attempt: Attempt, attempt_evidence: Sequence[str]) -> Attempt:
    """The Attempt with the evidence that travels and nothing else of what it records."""
    return Attempt(
        **{
            field: _TRANSPORTED_AS.get(field, _as_recorded)(getattr(attempt, field))
            for field in (*ALWAYS_TRANSPORTED, *attempt_evidence)
        }
    )


def _as_recorded(evidence: Any) -> Any:
    return evidence


def _tool_names_in_order(recording: Any) -> Any:
    """Action evidence with the tool name of each action, in order, and its source.

    The arguments, the result and the error of an action do not travel: no Evaluator is
    calculated from them, and they are the customer's data and can be large.
    """
    if not isinstance(recording, ActionEvidence):
        return recording
    return ActionEvidence(
        source=recording.source, events=[ActionEvent(tool_name=event.tool_name) for event in recording.events]
    )


# Evidence of an Attempt that travels with less than the Attempt records of it.
_TRANSPORTED_AS = {"action_evidence": _tool_names_in_order}


def _write_action_types(declared: Any) -> Any:
    # What is not a declared mapping travels as no declaration, and the canonical definition
    # refuses to run without one, in the sandbox as in the customer's own process.
    if not isinstance(declared, ActionTypeMapping):
        return None
    return {"evidence_source": declared.evidence_source, "action_types": declared.action_types}


def _read_action_types(transported: Any) -> ActionTypeMapping | None:
    if transported is None:
        return None
    return ActionTypeMapping(**_record(transported, "evidence_source", "action_types"))


# How a customer declaration is written for transport and read back, by the name an Evaluator takes it under.
_DECLARATIONS = {ACTION_TYPE_DECLARATION: (_write_action_types, _read_action_types)}


def _record(transported: Any, *fields: str) -> dict[str, Any]:
    if not isinstance(transported, dict) or sorted(transported) != sorted(fields):
        raise EvidenceError(
            f"a transported record with exactly the fields {list(fields)} was expected, got {transported!r}"
        )
    return transported


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _result_digest(evidence: str, body: bytes) -> bytes:
    """The digest in front of an encoded result: of the digest of its evidence, and of its body."""
    evidence_digest = evidence.partition(":")[0]
    return hashlib.sha256(evidence_digest.encode("utf-8") + b":" + body).digest()[:DIGEST_BYTES]


def _deflate(data: bytes) -> bytes:
    # Raw deflate, without the header and checksum of the zlib format: the digests are the check.
    deflater = zlib.compressobj(9, zlib.DEFLATED, -15)
    return deflater.compress(data) + deflater.flush()


def _inflate(data: bytes) -> bytes:
    return zlib.decompress(data, -15)


def _not_a_number(constant: str) -> Any:
    raise EvidenceError(f"transported evidence holds {constant}, which is not a number")

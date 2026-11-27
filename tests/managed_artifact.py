"""Runs a derived Foundry evaluator on this machine, the way the code-evaluator sandbox calls it.

The artifact's source is executed on its own, as a module of its own, and its
`grade(sample, item)` is called with one item. Nothing of the offering's source is handed to it.

What travels is read and written here by hand, and not with the offering's own encoder and
decoder, so that these helpers say what the encoding is:

- evidence: `<sha256 of the JSON>:<base64 of the raw-deflated JSON>`;
- a result: one number for each item. The item names a part in `result_part`: "value" for the
  plain metric value, "size" for the number of bytes of the encoded result, and "1", "2", ...
  for six bytes of it each, as a big-endian whole number. The encoded result is twelve bytes
  of digest and then the raw-deflated JSON of the result.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import itertools
import json
import sys
import types
import zlib
from typing import Any

from reliability_evaluators.evaluators import CountedReason, EvidenceCount, MetricResult, MetricState
from reliability_evaluators.evidence import EvidenceError
from reliability_evaluators.managed_scoring import derive_artifact

_loaded = itertools.count(1)


@functools.lru_cache(maxsize=None)
def load_grader(code_text: str):
    """Execute an artifact's source and return its `grade` function."""
    module = types.ModuleType(f"derived_foundry_evaluator_{next(_loaded)}")
    # Dataclasses look their module up while a class is created, so it is a module for that long.
    sys.modules[module.__name__] = module
    try:
        exec(compile(code_text, "<derived Foundry evaluator>", "exec"), module.__dict__)
    finally:
        del sys.modules[module.__name__]
    return module.grade


def grade(artifact, tasks, part: str = "value", declared: tuple = ()) -> Any:
    """What the derived evaluator returns for one part of the result of grouped Attempt evidence."""
    return grade_item(artifact, artifact.item(tasks, *declared), part)


def grade_item(artifact, item: dict, part: str) -> Any:
    """What the derived evaluator returns for one part of the result of an item that holds the evidence."""
    return load_grader(artifact.code_text)({}, {**item, "result_part": part})


def returned_result(artifact, tasks, declared: tuple = ()) -> dict:
    """The result the derived evaluator returns in parts for grouped Attempt evidence, put together by hand."""
    return returned_for_item(artifact, artifact.item(tasks, *declared))


def returned_for_item(artifact, item: dict) -> dict:
    """The result the derived evaluator returns in parts for an item that holds the evidence, put together by hand."""
    size = grade_item(artifact, item, "size")
    parts = -(-size // 6)
    numbers = [grade_item(artifact, item, str(number)) for number in range(1, parts + 1)]
    lengths = [6] * (parts - 1) + [size - 6 * (parts - 1)]
    encoded = b"".join(number.to_bytes(length, "big") for number, length in zip(numbers, lengths))
    digest, body = encoded[:12], encoded[12:]
    evidence_digest, _, _ = item[artifact.evidence_field].partition(":")
    assert digest == hashlib.sha256(evidence_digest.encode("utf-8") + b":" + body).digest()[:12]
    return json.loads(zlib.decompress(body, -15).decode("utf-8"))["result"]


def evaluate_as_managed(metric_id: str):
    """An Evaluator's `evaluate`, calculated by its derived Foundry evaluator instead of by the canonical definition.

    It takes what the canonical `evaluate` takes and gives what Foundry-managed scoring reports for it:
    the plain value the evaluator returns for the `value` part, and the state, the reason, the counts,
    the details and the Attempts of the encoded result. What the derived evaluator rejects is raised as
    the offering's own exception of the same name, so a vector reads the same on both scoring paths.
    """

    def evaluate(tasks, *declared):
        artifact = derive_artifact(metric_id)
        item = artifact.item(tuple(tasks), *declared)
        try:
            value = grade_item(artifact, item, "value")
            returned = returned_for_item(artifact, item)
        except Exception as raised:
            raise _as_the_offering_raises(raised) from raised
        assert returned["metric_id"] == metric_id
        state = MetricState(returned["state"])
        if state is MetricState.AVAILABLE:
            # The same double in the value part and in the encoded result.
            assert type(value) is float and value == returned["value"]
        else:
            # A number below the scale stands for no value. It is internal, and never a value of the metric.
            assert value == artifact.definition.scale.minimum - 1 and returned["value"] is None
        return MetricResult(
            definition=artifact.definition,
            state=state,
            value=value if state is MetricState.AVAILABLE else None,
            reason=returned["reason"],
            counts={unit: _count(count) for unit, count in returned["counts"].items()},
            details=returned["details"],
            attempt_ids=tuple(returned["evidence"]["attempt_ids"]),
        )

    return evaluate


def _count(returned: dict) -> EvidenceCount:
    assert sorted(returned) == ["excluded", "missing", "total", "valid"]
    return EvidenceCount(
        total=returned["total"],
        valid=returned["valid"],
        missing=tuple(_counted_reason(entry) for entry in returned["missing"]),
        excluded=tuple(_counted_reason(entry) for entry in returned["excluded"]),
    )


def _counted_reason(returned: dict) -> CountedReason:
    assert sorted(returned) == ["count", "ids", "reason"] and returned["count"] == len(returned["ids"])
    return CountedReason(returned["reason"], tuple(returned["ids"]))


def _as_the_offering_raises(raised: Exception) -> Exception:
    """The exception a derived evaluator raised, as the class of that name that the offering raises.

    The artifact is a source of its own, so its EvidenceError is a class of its own.
    """
    if type(raised).__name__ == "EvidenceError":
        return EvidenceError(*raised.args)
    return raised


def altered_evidence(item: dict, old: str, new: str) -> dict:
    """A submitted item whose evidence was changed after it was encoded, behind the digest it was encoded with.

    The evaluator that is handed it raises: the evidence no longer matches its digest.
    """
    (field,) = [field for field in item if field.endswith("_evidence")]
    payload = unpacked(item[field])
    assert old in payload
    return {**item, field: packed(payload.replace(old, new, 1), digest=item[field].partition(":")[0])}


def unpacked(evidence: str) -> str:
    """The JSON of transported evidence."""
    _, _, packed = evidence.partition(":")
    return zlib.decompress(base64.b64decode(packed), -15).decode("utf-8")


def packed(payload: str, digest: str | None = None) -> str:
    """JSON as transported evidence, behind its own digest unless another one is given."""
    deflater = zlib.compressobj(9, zlib.DEFLATED, -15)
    deflated = deflater.compress(payload.encode("utf-8")) + deflater.flush()
    digest = digest or hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest + ":" + base64.b64encode(deflated).decode("ascii")

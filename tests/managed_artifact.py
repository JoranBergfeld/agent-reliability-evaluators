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


def grade(artifact, tasks, part: str = "value") -> Any:
    """What the derived evaluator returns for one part of the result of grouped Attempt evidence."""
    return load_grader(artifact.code_text)({}, {**artifact.item(tasks), "result_part": part})


def returned_result(artifact, tasks) -> dict:
    """The result the derived evaluator returns in parts for grouped Attempt evidence, put together by hand."""
    size = grade(artifact, tasks, "size")
    parts = -(-size // 6)
    numbers = [grade(artifact, tasks, str(number)) for number in range(1, parts + 1)]
    lengths = [6] * (parts - 1) + [size - 6 * (parts - 1)]
    encoded = b"".join(number.to_bytes(length, "big") for number, length in zip(numbers, lengths))
    digest, body = encoded[:12], encoded[12:]
    evidence_digest, _, _ = artifact.item(tasks)[artifact.evidence_field].partition(":")
    assert digest == hashlib.sha256(evidence_digest.encode("utf-8") + b":" + body).digest()[:12]
    return json.loads(zlib.decompress(body, -15).decode("utf-8"))["result"]


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

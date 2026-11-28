"""Invocation evidence: what an agent target recorded of one request to an agent.

A request is the invocation of an Attempt, or the confidence request that followed it. An
Attempt keeps what was recorded as JSON-compatible data, so that a scorecard can hold it.
This is that data with the parts every reader needs under one name each: the token usage,
why there is none, where a count comes from, the response and the tool calls.

Pure: standard library only, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Mapping


@dataclass(frozen=True, kw_only=True)
class InvocationEvidence:
    """What an agent target recorded of one request, as the platform returned it.

    `token_usage` is the usage as the platform reported it, or None. Usage that was not
    reported is missing, never zero: `token_usage_missing` then says why, and
    `token_usage_source` says where a reported count comes from. `tool_calls` are the tool
    calls the platform exposed, in order, or None when it exposed none to read, which is
    not the same as no call.

    `identifiers` is whatever else the target recorded of the request, under its own names:
    the request identifiers of the answer, the evaluation run, the agent and its version,
    the status and the error.
    """

    agent_target: Any = None
    # Where the request was sent from: customer code, or Foundry cloud evaluation.
    entry_point: Any = None
    response_id: Any = None
    token_usage: Any = None
    token_usage_missing: Any = None
    token_usage_source: Any = None
    tool_calls: Any = None
    identifiers: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def with_usage(cls, usage: Any, *, source: Any, missing: Any, **recorded: Any) -> InvocationEvidence:
        """Evidence of a request with the usage the platform reported for it, or with why it reported none.

        `source` is where a reported count comes from, and `missing` why there is none when
        there is none. Only the one that applies is recorded.
        """
        return cls(
            token_usage=usage,
            token_usage_missing=missing if usage is None else None,
            token_usage_source=None if usage is None else source,
            **recorded,
        )

    @classmethod
    def read(cls, recorded: Mapping[str, Any] | None) -> InvocationEvidence:
        """The evidence an Attempt keeps, as it was recorded by whichever agent target made it."""
        recorded = recorded or {}
        named = {one.name for one in fields(cls)} - {"identifiers"}
        return cls(
            **{name: recorded.get(name) for name in named},
            identifiers={name: value for name, value in recorded.items() if name not in named},
        )

    @property
    def request_ids(self) -> Mapping[str, Any]:
        """The identifiers of the request on the platform, where the answer carried them."""
        return self.identifiers.get("request_ids") or {}

    @property
    def evaluation_run(self) -> dict[str, Any] | None:
        """The evaluation run the request was made through, or None when it was made through none."""
        if self.identifiers.get("eval_id") is None and self.identifiers.get("run_id") is None:
            return None
        return {key: self.identifiers.get(key) for key in ("eval_id", "run_id", "output_item_id")}

    def to_dict(self) -> dict[str, Any]:
        """The evidence as an Attempt keeps it: JSON-compatible data, every part under its own name."""
        named = {one.name: getattr(self, one.name) for one in fields(self) if one.name != "identifiers"}
        return {**named, **self.identifiers}

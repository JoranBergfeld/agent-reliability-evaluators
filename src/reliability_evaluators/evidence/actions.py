"""Ordered action evidence of an Attempt, and the declared mapping from its events to action types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import EvidenceError


@dataclass(frozen=True, kw_only=True)
class ActionEvent:
    """One action the agent took: the tool it called, with what, and what came back.

    `arguments` and `result` are JSON-compatible data, kept as recorded. `error` is the
    error the call ended in, or None when it did not.
    """

    tool_name: str
    arguments: Any = None
    result: Any = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not _is_name(self.tool_name):
            raise EvidenceError(f"an action event names its tool: the tool name is {self.tool_name!r}")
        for name in ("arguments", "result"):
            try:
                json.dumps(getattr(self, name), allow_nan=False)
            except (TypeError, ValueError):
                raise EvidenceError(
                    f"the {name} of an action event are JSON-compatible data. "
                    f"Got {getattr(self, name)!r} for {self.tool_name!r}"
                ) from None
        if self.error is not None and not isinstance(self.error, str):
            raise EvidenceError(f"the error of an action event is text or None. Got {self.error!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
        }


@dataclass(frozen=True, kw_only=True)
class ActionEvidence:
    """The actions recorded for one Attempt, in the order they were taken.

    `source` says where the recording comes from, such as native tool calls or
    service-side effects. `events` may be empty: that is a confirmed empty sequence, an
    Attempt known to have taken no action. An Attempt whose actions were not recorded
    has no ActionEvidence at all.
    """

    source: str
    events: tuple[ActionEvent, ...]

    def __post_init__(self) -> None:
        if not _is_name(self.source):
            raise EvidenceError("action evidence records its source")
        try:
            events = tuple(self.events)
        except TypeError:
            events = (self.events,)
        if not all(isinstance(event, ActionEvent) for event in events):
            raise EvidenceError(f"action evidence is a sequence of ActionEvent, in order. Got {self.events!r}")
        object.__setattr__(self, "events", events)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "events": [event.to_dict() for event in self.events]}


# The name under which an Evaluator takes the customer's `ActionTypeMapping`.
ACTION_TYPE_DECLARATION = "action_types"


@dataclass(frozen=True, kw_only=True)
class ActionTypeMapping:
    """What a customer declares for the trajectory metrics. Nothing here has a default.

    `evidence_source` is the source of action evidence the metrics are calculated from.
    `action_types` gives the action type of each tool name; tool names with the same
    action type are the same action.
    """

    evidence_source: str
    action_types: Mapping[str, str]

    def __post_init__(self) -> None:
        if not _is_name(self.evidence_source):
            raise ValueError(f"an action type mapping declares its evidence source, got {self.evidence_source!r}")
        if not isinstance(self.action_types, Mapping) or not all(map(_is_name, self.action_types)):
            raise ValueError(
                f"an action type mapping gives an action type per tool name, got {self.action_types!r}"
            )
        if not all(map(_is_name, self.action_types.values())):
            raise ValueError(f"every tool name is declared with an action type, got {self.action_types!r}")
        object.__setattr__(self, "action_types", dict(self.action_types))


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())

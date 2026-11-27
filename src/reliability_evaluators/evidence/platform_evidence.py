"""What the platform that ran the agent returned about the invocation of one Attempt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .actions import ActionEvent
from .errors import EvidenceError
from .experiment import json_data


@dataclass(frozen=True, kw_only=True)
class PlatformEvidence:
    """What the platform returned about one invocation, as the agent target captured it.

    `source` says which interface of the platform it was read from. `identifiers` are the
    identifiers the platform returned, by name, such as the identity of the response. They
    find the invocation on the platform and attribute its token usage to it.

    `token_usage` is the usage the platform reported for the invocation, unchanged. None
    means it reported none, and `token_usage_missing` then says why. Usage that was not
    reported is never zero.

    `tool_calls` are the tool calls the platform exposed, in order. None means it exposed
    none for this invocation, which is not the same as an invocation known to have made no call.
    """

    source: str
    identifiers: Mapping[str, Any]
    token_usage: Mapping[str, Any] | None = None
    token_usage_missing: str | None = None
    tool_calls: tuple[ActionEvent, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise EvidenceError("platform evidence records its source")
        if not isinstance(self.identifiers, Mapping):
            raise EvidenceError(f"the identifiers a platform returned are a mapping by name, got {self.identifiers!r}")
        object.__setattr__(self, "identifiers", json_data(self.identifiers, "the identifiers a platform returned"))
        missing = self.token_usage_missing
        if self.token_usage is None:
            if not isinstance(missing, str) or not missing.strip():
                raise EvidenceError(
                    "platform evidence without token usage says why it is missing: "
                    f"token_usage_missing is {missing!r}. Usage that was not reported is never zero"
                )
        else:
            if not isinstance(self.token_usage, Mapping):
                raise EvidenceError(f"token usage is kept as the platform reported it, got {self.token_usage!r}")
            if missing is not None:
                raise EvidenceError(f"token usage was reported, and it is also said to be missing: {missing!r}")
            object.__setattr__(self, "token_usage", json_data(self.token_usage, "the token usage a platform reported"))
        if self.tool_calls is not None:
            try:
                tool_calls = tuple(self.tool_calls)
            except TypeError:
                tool_calls = (self.tool_calls,)
            if not all(isinstance(call, ActionEvent) for call in tool_calls):
                raise EvidenceError(
                    "the tool calls a platform exposed are a sequence of ActionEvent, in order. "
                    f"Got {self.tool_calls!r}"
                )
            object.__setattr__(self, "tool_calls", tool_calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "identifiers": self.identifiers,
            "token_usage": self.token_usage,
            "token_usage_missing": self.token_usage_missing,
            "tool_calls": None if self.tool_calls is None else [call.to_dict() for call in self.tool_calls],
        }

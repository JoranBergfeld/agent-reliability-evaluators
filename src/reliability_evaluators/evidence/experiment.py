"""What an Attempt records about its experiment: the starting state, the condition, the controls."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .errors import EvidenceError


class StartingStatePolicy(str, Enum):
    """The state an Attempt starts from. It is declared, never assumed.

    FRESH: the state is prepared before every Attempt and cleaned up after it.
    CONTINUING: the state is prepared once and each Attempt continues from the state the
    one before it left, on purpose.
    """

    FRESH = "fresh"
    CONTINUING = "continuing"


class ConditionRole(str, Enum):
    """The part a condition plays in its experiment."""

    BASELINE = "baseline"
    VARIATION = "variation"


class VariationKind(str, Enum):
    """What a variation changes. A robustness metric compares the baseline with variations of its own kind."""

    # A tool of the agent fails.
    FAULT = "fault"


def json_data(value: Any, what: str) -> Any:
    """A copy of `value` as JSON-compatible data, so that it can be kept in a scorecard."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        raise EvidenceError(f"{what} must be JSON-compatible data, got {value!r}") from None


@dataclass(frozen=True, kw_only=True)
class Condition:
    """An experimental condition: the baseline, or the one variation from it.

    `parameters` is what the customer controls need to apply and verify it.

    `kind` is declared for a variation and says what it changes, such as "fault". The
    baseline has none. A variation without a kind is compared with the baseline by no metric.
    """

    condition_id: str
    description: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    kind: VariationKind | None = None

    def __post_init__(self) -> None:
        for name in ("condition_id", "description"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise EvidenceError(f"a condition is identified and described: {name} is {value!r}")
        if self.kind is not None:
            try:
                object.__setattr__(self, "kind", VariationKind(self.kind))
            except ValueError:
                kinds = ", ".join(repr(kind.value) for kind in VariationKind)
                raise EvidenceError(
                    f"{self.kind!r} is not a kind of variation. Condition {self.condition_id!r} "
                    f"declares one of {kinds}, or none"
                ) from None
        if not isinstance(self.parameters, Mapping):
            raise EvidenceError(f"the parameters of a condition are a mapping, got {self.parameters!r}")
        object.__setattr__(
            self, "parameters", json_data(self.parameters, f"the parameters of condition {self.condition_id!r}")
        )

    def to_dict(self) -> dict[str, Any]:
        recorded = {
            "condition_id": self.condition_id,
            "description": self.description,
            "parameters": self.parameters,
        }
        # Recorded for a variation that declares one. The baseline has no kind.
        return recorded if self.kind is None else {**recorded, "kind": self.kind.value}


@dataclass(frozen=True, kw_only=True)
class ObservedCondition:
    """What a verify control observed about the condition an Attempt actually ran under.

    `met` says whether the intended condition occurred. `source` says what that was
    observed from, such as the request log of a test tool. A label is not an observation.
    """

    met: bool
    source: str
    observations: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.met) is not bool:
            raise EvidenceError(f"an observed condition says whether the intended one was met, got {self.met!r}")
        if not isinstance(self.source, str) or not self.source.strip():
            raise EvidenceError("an observed condition records its source")
        if not isinstance(self.observations, Mapping):
            raise EvidenceError(f"the observations of a condition are a mapping, got {self.observations!r}")
        object.__setattr__(
            self, "observations", json_data(self.observations, "the observations of a condition")
        )

    def to_dict(self) -> dict[str, Any]:
        return {"met": self.met, "source": self.source, "observations": self.observations}


class LifecyclePoint(str, Enum):
    """The points at which the sweep invokes a customer control, in the order of one Attempt."""

    PREPARE_STATE = "prepare_state"
    APPLY_CONDITION = "apply_condition"
    VERIFY_CONDITION = "verify_condition"
    CLEAN_UP = "clean_up"


@dataclass(frozen=True)
class LifecycleStep:
    """One invocation of a customer control for an Attempt, and how it ended."""

    point: LifecyclePoint
    # Why the control failed, when it did.
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"point": self.point.value, "succeeded": self.error is None, "error": self.error}


class ExperimentStatus(str, Enum):
    """How the running of an experiment ended. This is not the state of a metric."""

    COMPLETED = "completed"
    # Attempts that depended on state a customer control did not provide were not made.
    STOPPED = "stopped"

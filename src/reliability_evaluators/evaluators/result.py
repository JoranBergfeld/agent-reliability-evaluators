"""The metric result contract: what every Evaluator returns.

Results do not share one output shape: `details` and the units in `counts` are the
Evaluator's own. Higher is not assumed to be better; read `definition.interpretation`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Any, Iterable

from .catalogue import ReliabilityMetric


class MetricState(str, Enum):
    """A metric in a scorecard is in exactly one of these states."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    # Set by the scorecard for a metric the customer did not select. No Evaluator returns it.
    NOT_CONFIGURED = "not_configured"


class ScoringPath(str, Enum):
    """Where a metric was scored. A result is always attributed to exactly one path."""

    CUSTOMER_PROCESS = "customer_process"
    FOUNDRY_MANAGED = "foundry_managed"


class Fidelity(str, Enum):
    """Whether a value is known to equal what the Evaluator computed."""

    AUTHORITATIVE = "authoritative"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Scale:
    minimum: float
    maximum: float
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {"minimum": self.minimum, "maximum": self.maximum, "description": self.description}


@dataclass(frozen=True, kw_only=True)
class MetricDefinition:
    """What a metric measures and how, versioned so that a value can be traced to it."""

    metric: ReliabilityMetric
    version: str
    measures: str
    calculation: str
    fidelity_conventions: tuple[str, ...]
    scale: Scale
    interpretation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "measures": self.measures,
            "calculation": self.calculation,
            "fidelity_conventions": list(self.fidelity_conventions),
            "scale": self.scale.to_dict(),
            "interpretation": self.interpretation,
        }


@dataclass(frozen=True)
class CountedReason:
    """Evidence left out of a calculation for one reason, named by identity."""

    reason: str
    ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "count": len(self.ids), "ids": list(self.ids)}


def counted(reason: str, ids: Iterable[str]) -> tuple[CountedReason, ...]:
    """One CountedReason for the given identities, or nothing when there are none."""
    ids = tuple(ids)
    return (CountedReason(reason, ids),) if ids else ()


@dataclass(frozen=True, kw_only=True)
class EvidenceCount:
    """Counts for one unit of evidence, such as Tasks or Attempts.

    `valid` is the denominator the calculation used. `missing` is evidence that should
    exist and does not; `excluded` is evidence that exists and was deliberately not used.
    """

    total: int
    valid: int
    missing: tuple[CountedReason, ...] = ()
    excluded: tuple[CountedReason, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "valid": self.valid,
            "missing": [entry.to_dict() for entry in self.missing],
            "excluded": [entry.to_dict() for entry in self.excluded],
        }


@dataclass(frozen=True, kw_only=True)
class MetricResult:
    """One metric, as calculated by its Evaluator from grouped Attempt evidence.

    Build it with `available`, `unavailable` or `not_applicable`. The scoring path and
    fidelity are not known to an Evaluator; whoever ran it attributes them in the scorecard.
    """

    definition: MetricDefinition
    state: MetricState
    value: float | None = None
    reason: str | None = None
    counts: dict[str, EvidenceCount] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    # Identities of the Attempts the value rests on.
    attempt_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state is MetricState.AVAILABLE:
            self._require_value_on_scale()
        elif self.state in (MetricState.UNAVAILABLE, MetricState.NOT_APPLICABLE):
            if self.value is not None:
                raise ValueError(f"a metric that is {self.state.value} never carries a number")
            if not self.reason:
                raise ValueError(f"a metric that is {self.state.value} carries a reason")
        else:
            raise ValueError(f"an Evaluator cannot return the state {self.state!r}")

    def _require_value_on_scale(self) -> None:
        value, scale = self.value, self.definition.scale
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"an available metric carries a finite number, got {value!r}")
        if not scale.minimum <= value <= scale.maximum:
            raise ValueError(f"{value!r} is outside the scale {scale.minimum} to {scale.maximum}")

    @classmethod
    def available(cls, definition: MetricDefinition, value: float, **evidence: Any) -> MetricResult:
        return cls(definition=definition, state=MetricState.AVAILABLE, value=value, **evidence)

    @classmethod
    def unavailable(cls, definition: MetricDefinition, reason: str, **evidence: Any) -> MetricResult:
        return cls(definition=definition, state=MetricState.UNAVAILABLE, reason=reason, **evidence)

    @classmethod
    def not_applicable(cls, definition: MetricDefinition, reason: str, **evidence: Any) -> MetricResult:
        """The protocol of the metric does not apply to the declared experiment. Never for missing evidence."""
        return cls(definition=definition, state=MetricState.NOT_APPLICABLE, reason=reason, **evidence)

    def to_dict(self) -> dict[str, Any]:
        metric = self.definition.metric
        return {
            "metric_id": metric.metric_id,
            "name": metric.name,
            "dimension": metric.dimension,
            "state": self.state.value,
            "value": self.value,
            "reason": self.reason,
            "definition": self.definition.to_dict(),
            "counts": {unit: count.to_dict() for unit, count in self.counts.items()},
            "details": self.details,
            "evidence": {"attempt_ids": list(self.attempt_ids)},
        }

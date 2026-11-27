"""Evaluators: the canonical metric definitions, one module per reliability metric.

Everything here is pure: standard library only, no I/O, no network, no Foundry. Each
Evaluator can be called on its own, for example
`outcome_consistency.evaluate(group_by_task(attempts))`. The trajectory Evaluators also
take the customer's declared `ActionTypeMapping`.

The three predictability Evaluators calculate from the same confidence/outcome pairs,
which `confidence_pairs.pair_confidence_with_outcome` builds, counts and explains.
"""

from . import (
    brier_based_score,
    constraint_compliance,
    distributional_trajectory_consistency,
    outcome_consistency,
    resource_consistency,
    sequential_trajectory_consistency,
)
from .catalogue import RELIABILITY_METRICS, ReliabilityMetric
from .registry import EVALUATORS, Evaluator
from .result import (
    CountedReason,
    EvidenceCount,
    Fidelity,
    MetricDefinition,
    MetricResult,
    MetricState,
    Scale,
    ScoringPath,
    counted,
)

__all__ = [
    "EVALUATORS",
    "RELIABILITY_METRICS",
    "CountedReason",
    "Evaluator",
    "EvidenceCount",
    "Fidelity",
    "MetricDefinition",
    "MetricResult",
    "MetricState",
    "ReliabilityMetric",
    "Scale",
    "ScoringPath",
    "brier_based_score",
    "constraint_compliance",
    "counted",
    "distributional_trajectory_consistency",
    "outcome_consistency",
    "resource_consistency",
    "sequential_trajectory_consistency",
]

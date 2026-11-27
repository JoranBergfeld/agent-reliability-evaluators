"""The Evaluators that exist, by metric identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..evidence import VariationKind
from . import (
    brier_based_score,
    constraint_compliance,
    distributional_trajectory_consistency,
    fault_robustness,
    outcome_consistency,
    resource_consistency,
    sequential_trajectory_consistency,
)
from .result import MetricDefinition, MetricResult


@dataclass(frozen=True)
class Evaluator:
    """A canonical metric definition together with its calculation.

    `evaluate` takes Attempts grouped by Task, followed by the customer declarations
    named in `declarations`, in that order.
    """

    definition: MetricDefinition
    evaluate: Callable[..., MetricResult]
    declarations: tuple[str, ...] = ()
    # Whether the calculation uses confidence ratings, which a sweep has to ask the agent for.
    needs_confidence: bool = False
    # The kind of variation whose Attempts the calculation compares with the baseline Attempts
    # of the same experiments. None: it is calculated from baseline Attempts only.
    compares_variation: VariationKind | None = None


EVALUATORS: dict[str, Evaluator] = {
    evaluator.definition.metric.metric_id: evaluator
    for evaluator in (
        Evaluator(outcome_consistency.DEFINITION, outcome_consistency.evaluate),
        Evaluator(
            distributional_trajectory_consistency.DEFINITION,
            distributional_trajectory_consistency.evaluate,
            declarations=("action_types",),
        ),
        Evaluator(
            sequential_trajectory_consistency.DEFINITION,
            sequential_trajectory_consistency.evaluate,
            declarations=("action_types",),
        ),
        Evaluator(resource_consistency.DEFINITION, resource_consistency.evaluate),
        Evaluator(
            fault_robustness.DEFINITION, fault_robustness.evaluate, compares_variation=VariationKind.FAULT
        ),
        Evaluator(brier_based_score.DEFINITION, brier_based_score.evaluate, needs_confidence=True),
        Evaluator(constraint_compliance.DEFINITION, constraint_compliance.evaluate),
    )
}

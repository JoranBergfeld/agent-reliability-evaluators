"""The Evaluators that exist, by metric identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import (
    constraint_compliance,
    distributional_trajectory_consistency,
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
        Evaluator(constraint_compliance.DEFINITION, constraint_compliance.evaluate),
    )
}

"""The Evaluators that exist, by metric identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..evidence import ConditionRole
from . import (
    brier_based_score,
    calibration,
    conditional_harm_severity,
    constraint_compliance,
    distributional_trajectory_consistency,
    outcome_consistency,
    resource_consistency,
    sequential_trajectory_consistency,
    strict_confidence_discrimination,
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
    # The experimental conditions the calculation is made from, by the part they play in
    # their experiment. A sweep gives the Evaluator no Attempt made under another condition.
    condition_roles: tuple[ConditionRole, ...] = (ConditionRole.BASELINE,)


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
        Evaluator(calibration.DEFINITION, calibration.evaluate, needs_confidence=True),
        Evaluator(
            strict_confidence_discrimination.DEFINITION,
            strict_confidence_discrimination.evaluate,
            needs_confidence=True,
        ),
        Evaluator(brier_based_score.DEFINITION, brier_based_score.evaluate, needs_confidence=True),
        Evaluator(constraint_compliance.DEFINITION, constraint_compliance.evaluate),
        Evaluator(conditional_harm_severity.DEFINITION, conditional_harm_severity.evaluate),
    )
}

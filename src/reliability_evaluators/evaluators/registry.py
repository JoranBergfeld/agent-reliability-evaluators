"""The Evaluators that exist, by metric identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..evidence import VariationKind
from . import (
    brier_based_score,
    calibration,
    conditional_harm_severity,
    constraint_compliance,
    distributional_trajectory_consistency,
    environment_robustness,
    fault_robustness,
    outcome_consistency,
    prompt_robustness,
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
    # The experimental conditions the calculation is made from. None: Attempts under the baseline.
    # A kind of variation: the baseline Attempts and the variation Attempts of the experiments whose
    # variation is of that kind, which the calculation compares. A sweep gives the Evaluator no
    # Attempt made under another condition.
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
        Evaluator(
            environment_robustness.DEFINITION,
            environment_robustness.evaluate,
            compares_variation=VariationKind.ENVIRONMENT,
        ),
        Evaluator(
            prompt_robustness.DEFINITION, prompt_robustness.evaluate, compares_variation=VariationKind.PROMPT
        ),
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

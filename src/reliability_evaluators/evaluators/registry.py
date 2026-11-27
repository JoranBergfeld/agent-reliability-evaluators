"""The Evaluators that exist, by metric identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from ..evidence import TaskEvidence
from . import brier_based_score, outcome_consistency
from .result import MetricDefinition, MetricResult


@dataclass(frozen=True)
class Evaluator:
    """A canonical metric definition together with its calculation."""

    definition: MetricDefinition
    evaluate: Callable[[Iterable[TaskEvidence]], MetricResult]
    # Whether the calculation uses confidence ratings, which a sweep has to ask the agent for.
    needs_confidence: bool = False


EVALUATORS: dict[str, Evaluator] = {
    evaluator.definition.metric.metric_id: evaluator
    for evaluator in (
        Evaluator(outcome_consistency.DEFINITION, outcome_consistency.evaluate),
        Evaluator(brier_based_score.DEFINITION, brier_based_score.evaluate, needs_confidence=True),
    )
}

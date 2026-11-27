"""The Evaluators that exist, by metric identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from ..evidence import TaskEvidence
from . import outcome_consistency
from .result import MetricDefinition, MetricResult


@dataclass(frozen=True)
class Evaluator:
    """A canonical metric definition together with its calculation."""

    definition: MetricDefinition
    evaluate: Callable[[Sequence[TaskEvidence]], MetricResult]


EVALUATORS: dict[str, Evaluator] = {
    evaluator.definition.metric.metric_id: evaluator
    for evaluator in (Evaluator(outcome_consistency.DEFINITION, outcome_consistency.evaluate),)
}

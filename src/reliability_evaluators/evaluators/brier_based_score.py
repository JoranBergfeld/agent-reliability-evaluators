"""Brier-based score: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from typing import Iterable

from ..evidence import TaskEvidence
from .catalogue import BRIER_BASED_SCORE
from .confidence_pairs import NO_VALID_PAIR, PAIRS, pair_confidence_with_outcome
from .result import MetricDefinition, MetricResult, Scale

DEFINITION = MetricDefinition(
    metric=BRIER_BASED_SCORE,
    version="2.0.0",
    measures=(
        "How close the agent's confidence rating was to the task outcome: the squared error between "
        "the two, inverted so that higher is better."
    ),
    calculation=(
        "Each valid confidence/outcome pair has a confidence c, the rating divided by 100, and an "
        "outcome y, 1 for a success and 0 for a failure. The metric is 1 minus the mean of (c - y)^2 "
        "over the valid pairs."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: one minus the binary Brier loss, 1 - mean((c - y)^2). It is the inverted "
        "score and not the raw loss, for which lower is better.",
        "Assumption (spec assumption 9): all valid confidence/outcome pairs are pooled across Tasks and "
        "Attempts as one observation set. Tasks are not weighted equally: a Task with more valid pairs "
        "counts for more.",
        "Only complete pairs are used: a completed Attempt that has both a task outcome and a confidence "
        "rating. Every other Attempt is counted with its reason, and is never scored as a confidence of 0 "
        "or as a task failure. The metric is unavailable only when there is no valid pair.",
        "The rating comes from a separate confidence-only request after the Attempt, as one number from "
        "0 to 100, which is divided by 100. A rating given inside the task response is not used, and the "
        "paper's heuristic fallback for a rating that cannot be read is not adopted.",
        "The rating states how likely the agent thinks the task succeeded, not how sure it is of its "
        "prediction.",
        "Nominal behaviour: the metric is calculated from the Attempts made under the baseline. An Attempt "
        "that records a variation as its condition is excluded with a reason and counted, whoever calls "
        "the Evaluator. An Attempt that records no condition was made without an experiment and is used.",
    ),
    scale=Scale(
        minimum=0.0,
        maximum=1.0,
        description=(
            "1: every rating was 100 on a success and 0 on a failure. "
            "0: every rating was 0 on a success and 100 on a failure."
        ),
    ),
    interpretation=(
        "Higher is better, but a high score alone does not show that the ratings tell successes from "
        "failures. An agent that always answers 50 scores 0.75 whatever happens, and one that always "
        "states its true success frequency p scores 1 - p(1 - p). Read the score next to the raw pairs. "
        "The rating is the agent's own estimate after the fact, not an independently verified probability."
    ),
)

def evaluate(tasks: Iterable[TaskEvidence]) -> MetricResult:
    """Calculate the Brier-based score from Attempts grouped by Task."""
    paired = pair_confidence_with_outcome(tasks)
    squared_errors = [(pair.confidence - pair.outcome) ** 2 for pair in paired.pairs]
    # Exact arithmetic until the conversions below, so each number is the double nearest to the true result.
    mean_squared_error = sum(squared_errors) / len(squared_errors) if squared_errors else None
    evidence = {
        "counts": {PAIRS: paired.count},
        "details": {
            "mean_squared_error": None if mean_squared_error is None else float(mean_squared_error),
            "pairs": [
                {**pair.to_dict(), "squared_error": float(squared_error)}
                for pair, squared_error in zip(paired.pairs, squared_errors)
            ],
        },
        "attempt_ids": paired.attempt_ids,
    }
    if mean_squared_error is None:
        return MetricResult.unavailable(DEFINITION, NO_VALID_PAIR, **evidence)
    return MetricResult.available(DEFINITION, float(1 - mean_squared_error), **evidence)

"""Calibration: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Iterable, Sequence

from ..evidence import TaskEvidence
from .catalogue import CALIBRATION
from .confidence_pairs import NO_VALID_PAIR, PAIRS, ConfidenceOutcomePair, pair_confidence_with_outcome
from .result import MetricDefinition, MetricResult, Scale

BINS = 10

DEFINITION = MetricDefinition(
    metric=CALIBRATION,
    version="2.0.0",
    measures=(
        "Whether the agent's stated confidence matches how often it succeeds: among the Attempts "
        "the agent rated near 70, about 70 in 100 should have succeeded."
    ),
    calculation=(
        "Each valid confidence/outcome pair has a confidence c, the rating divided by 100, and an "
        "outcome y, 1 for a success and 0 for a failure. The pairs are sorted into ten bins of equal "
        "width by c: [0, 0.1), [0.1, 0.2), up to [0.8, 0.9) and [0.9, 1]. A bin's gap is the absolute "
        "difference between the mean of y and the mean of c over its pairs. The expected calibration "
        "error is the sum, over the bins that hold a pair, of the bin's share of all pairs times its "
        "gap. The metric is 1 minus the expected calibration error."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: one minus the expected calibration error, 1 - sum_b (n_b / N) * "
        "|mean(y in b) - mean(c in b)|. Table 2 fixes neither the number of bins nor their boundaries. "
        "Ten fixed bins of equal width are used.",
        "Assumption (spec assumption 12): a bin is closed below and open above, and the last bin also "
        "includes 1. A confidence exactly on a boundary, such as 0.1, belongs to the bin that starts "
        "there. A bin without a pair contributes nothing.",
        "Assumption (spec assumption 9): all valid confidence/outcome pairs are pooled across Tasks and "
        "Attempts as one observation set. Tasks are not weighted equally: a Task with more valid pairs "
        "counts for more.",
        "The gap is taken between the two means of a bin, not as the mean of |c - y| over its pairs. "
        "Errors in opposite directions inside one bin therefore cancel.",
        "Binning never rounds or replaces an observation. The bin is decided from the rating exactly as "
        "the agent wrote it, the means are taken over the unrounded confidences, and every raw pair is "
        "reported next to the bin boundaries and the count of each bin.",
        "Only complete pairs are used: a completed Attempt that has both a task outcome and a confidence "
        "rating. Every other Attempt is counted with its reason, and is never scored as a confidence of 0 "
        "or as a task failure. The metric is unavailable only when there is no valid pair.",
        "Nominal behaviour: the metric is calculated from the Attempts made under the baseline. An Attempt "
        "that records a variation as its condition is excluded with a reason and counted, whoever calls "
        "the Evaluator. An Attempt that records no condition was made without an experiment and is used.",
    ),
    scale=Scale(
        minimum=0.0,
        maximum=1.0,
        description=(
            "1: in every bin that holds a pair, the mean confidence equals the share of successes. "
            "0: every rating was 0 on a success and 100 on a failure."
        ),
    ),
    interpretation=(
        "Higher is better, but a high value alone does not show that the ratings tell successes from "
        "failures: an agent that always states its overall success frequency scores 1 without ever "
        "pointing at an Attempt that failed. The value depends on the bins: a bin with few pairs is "
        "noisy, and errors in opposite directions inside one bin cancel. Read the value next to the "
        "count of each bin and the raw pairs. The rating is the agent's own estimate after the fact, "
        "not an independently verified probability."
    ),
)


def evaluate(tasks: Iterable[TaskEvidence]) -> MetricResult:
    """Calculate calibration from Attempts grouped by Task."""
    paired = pair_confidence_with_outcome(tasks)
    bin_of = {pair.attempt_id: _bin_of(pair.confidence) for pair in paired.pairs}
    bins = [
        _bin(number, [pair for pair in paired.pairs if bin_of[pair.attempt_id] == number], len(paired.pairs))
        for number in range(1, BINS + 1)
    ]
    # Exact arithmetic until the conversions to float, so each number is the double nearest to the true result.
    expected_calibration_error = sum(entry["contribution"] for entry in bins if entry["count"])
    evidence = {
        "counts": {PAIRS: paired.count},
        "details": {
            "expected_calibration_error": float(expected_calibration_error) if paired.pairs else None,
            "bins": [{name: _reported(value) for name, value in entry.items()} for entry in bins],
            "pairs": [{**pair.to_dict(), "bin": bin_of[pair.attempt_id]} for pair in paired.pairs],
        },
        "attempt_ids": paired.attempt_ids,
    }
    if not paired.pairs:
        return MetricResult.unavailable(DEFINITION, NO_VALID_PAIR, **evidence)
    return MetricResult.available(DEFINITION, float(1 - expected_calibration_error), **evidence)


def _bin_of(confidence: Fraction) -> int:
    """The number of the bin, from 1 to 10, that holds a confidence.

    Bin b is [(b - 1) / 10, b / 10): closed below and open above. The last bin also holds 1.
    """
    return min(int(confidence * BINS), BINS - 1) + 1


def _bin(number: int, pairs: Sequence[ConfidenceOutcomePair], all_pairs: int) -> dict[str, Any]:
    """One bin with its boundaries and count, and what it adds to the expected calibration error."""
    entry: dict[str, Any] = {
        "bin": number,
        "lower_bound": Fraction(number - 1, BINS),
        "upper_bound": Fraction(number, BINS),
        "includes_upper_bound": number == BINS,
        "count": len(pairs),
        "successes": sum(pair.outcome for pair in pairs),
    }
    if not pairs:
        # A bin without a pair has no means and no gap, and adds nothing.
        return {**entry, "mean_confidence": None, "success_frequency": None, "gap": None, "contribution": None}
    mean_confidence = sum(pair.confidence for pair in pairs) / len(pairs)
    success_frequency = Fraction(entry["successes"], len(pairs))
    gap = abs(success_frequency - mean_confidence)
    return {
        **entry,
        "mean_confidence": mean_confidence,
        "success_frequency": success_frequency,
        "gap": gap,
        "contribution": Fraction(len(pairs), all_pairs) * gap,
    }


def _reported(value: Any) -> Any:
    return float(value) if isinstance(value, Fraction) else value

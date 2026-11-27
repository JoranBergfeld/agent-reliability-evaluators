"""Strict confidence discrimination: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from fractions import Fraction
from typing import Iterable

from ..evidence import TaskEvidence
from .catalogue import STRICT_CONFIDENCE_DISCRIMINATION
from .confidence_pairs import NO_VALID_PAIR, PAIRS, pair_confidence_with_outcome
from .result import EvidenceCount, MetricDefinition, MetricResult, Scale

# The unit of the comparisons: one successful Attempt against one failed Attempt.
SUCCESS_FAILURE_PAIRS = "success_failure_pairs"

NO_SUCCESS = "no success among the valid confidence/outcome pairs, so there is no success/failure pair"
NO_FAILURE = "no failure among the valid confidence/outcome pairs, so there is no success/failure pair"

DEFINITION = MetricDefinition(
    metric=STRICT_CONFIDENCE_DISCRIMINATION,
    version="1.0.0",
    measures="Whether successful Attempts received a higher confidence rating than failed ones.",
    calculation=(
        "Each valid confidence/outcome pair has a confidence c, the rating divided by 100. Every "
        "successful Attempt is compared with every failed Attempt, which gives (successes x failures) "
        "success/failure pairs. The metric is the share of those pairs in which the success has the "
        "strictly greater c. A pair in which the two are equal counts as zero."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: the number of success/failure pairs with the confidence of the success "
        "strictly greater than that of the failure, divided by the number of successes times the "
        "number of failures.",
        "This is strict discrimination and not the conventional AUROC. The conventional AUROC, which "
        "the paper's text names and its companion code calculates, gives a tied pair half credit: "
        "(higher + 0.5 x tied) / pairs. Half credit is deliberately not given. When every rating is "
        "equal the conventional AUROC is 0.5 and this metric is 0. The two agree only when no "
        "success/failure pair is tied.",
        "The number of tied success/failure pairs is reported, next to the number in which the success "
        "was rated higher and the number in which the failure was rated higher.",
        "Ratings are compared exactly as the agent wrote them, without rounding: 70.01 is higher than "
        "70, and 87.50 ties with 87.5.",
        "Assumption (spec assumption 9): all valid confidence/outcome pairs are pooled across Tasks and "
        "Attempts as one observation set. A success at one Task is compared with a failure at another, "
        "and Tasks are not weighted equally.",
        "Only complete pairs are used: a completed Attempt that has both a task outcome and a confidence "
        "rating. Every other Attempt is counted with its reason, and is never scored as a confidence of 0 "
        "or as a task failure. The metric is unavailable when the valid pairs hold no success or no "
        "failure: there is then nothing to compare, and the value is not 0, 0.5 or 1.",
    ),
    scale=Scale(
        minimum=0.0,
        maximum=1.0,
        description=(
            "1: every successful Attempt was rated strictly higher than every failed Attempt. "
            "0: no successful Attempt was rated strictly higher than any failed Attempt, which includes "
            "every rating being equal."
        ),
    ),
    interpretation=(
        "Higher is better. The value is never above the conventional AUROC, and is below it by half "
        "the share of tied pairs, so do not compare it with an AUROC from another tool. An agent that "
        "gives every Attempt the same rating scores 0 and not 0.5, and coarse ratings, such as "
        "multiples of 10, lower the value through ties: read it next to the tied-pair count. A high "
        "value does not show that the ratings are calibrated: 51 on every success and 49 on every "
        "failure scores 1. The rating is the agent's own estimate after the fact, not an independently "
        "verified probability."
    ),
)


def evaluate(tasks: Iterable[TaskEvidence]) -> MetricResult:
    """Calculate strict confidence discrimination from Attempts grouped by Task."""
    paired = pair_confidence_with_outcome(tasks)
    successes = [pair.confidence for pair in paired.pairs if pair.outcome]
    failures = sorted(pair.confidence for pair in paired.pairs if not pair.outcome)
    # Confidences are exact, so two ratings tie only when they are the same number.
    success_rated_higher = sum(bisect_left(failures, confidence) for confidence in successes)
    not_lower = sum(bisect_right(failures, confidence) for confidence in successes)
    compared = len(successes) * len(failures)
    evidence = {
        "counts": {
            PAIRS: paired.count,
            # Every success is compared with every failure, so every pair counted was compared.
            SUCCESS_FAILURE_PAIRS: EvidenceCount(total=compared, valid=compared),
        },
        "details": {
            "successes": len(successes),
            "failures": len(failures),
            "success_failure_pairs": {
                "total": compared,
                "success_rated_higher": success_rated_higher,
                "tied": not_lower - success_rated_higher,
                "failure_rated_higher": compared - not_lower,
            },
            "pairs": [pair.to_dict() for pair in paired.pairs],
        },
    }
    if not paired.pairs:
        return MetricResult.unavailable(DEFINITION, NO_VALID_PAIR, **evidence)
    if not successes:
        return MetricResult.unavailable(DEFINITION, NO_SUCCESS, **evidence)
    if not failures:
        return MetricResult.unavailable(DEFINITION, NO_FAILURE, **evidence)
    return MetricResult.available(
        DEFINITION, float(Fraction(success_rated_higher, compared)), attempt_ids=paired.attempt_ids, **evidence
    )

"""Fault robustness: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from typing import Iterable

from ..evidence import TaskEvidence, VariationKind
from ._robustness import evaluate_against_baseline
from .catalogue import FAULT_ROBUSTNESS
from .result import MetricDefinition, MetricResult, Scale

DEFINITION = MetricDefinition(
    metric=FAULT_ROBUSTNESS,
    version="1.0.0",
    measures="How much of its baseline task success the agent keeps when a tool fails.",
    calculation=(
        "A Task is compared when it has a valid Attempt under the baseline and a valid Attempt under the "
        "fault. The accuracy under a condition is the mean, over the compared Tasks, of the share of the "
        "Task's valid Attempts under that condition that succeeded, each Task weighted equally. "
        "The metric is min(accuracy under the fault / baseline accuracy, 1)."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: min(Acc_fault / Acc_0, 1). It is the ratio of two accuracies. It is not the "
        "mean of per-Task ratios, and it is not the share of the baseline successes that survived the fault.",
        "Assumption (spec assumption 8): accuracy is task-balanced, over the Tasks present in both "
        "conditions. The paper states accuracy across tasks and its companion code pools Attempts, without "
        "matching Tasks between the conditions. The two give different values whenever Tasks differ in their "
        "number of valid Attempts.",
        "Assumption (spec assumption 14): an Attempt whose observed condition differs from the intended "
        "condition is excluded from that condition with a reason, and counted.",
        "An Attempt counts under the fault only when its observed condition, reported by the customer's "
        "verify control from evidence, shows that the fault occurred during that Attempt. A fault label, "
        "a configured fault rate and an applied fault that the agent never met are not evidence. The paper "
        "injects faults with a probability per tool call and does not require that an Attempt met one.",
        "The same holds for the baseline: an Attempt counts under the baseline only when its observed "
        "condition shows that the baseline occurred.",
        "The paper leaves a baseline accuracy of zero undefined, and its companion code returns no number "
        "below 1e-8. Here the metric is unavailable when the baseline accuracy is exactly zero. No epsilon "
        "is used: a small baseline accuracy that is not zero gives a value that rests on few successes.",
        "The paper's fault rate, fault mix and recovery policy are settings of its experiments and not part "
        "of the metric. The customer declares the fault and supplies the tool that fails on demand.",
    ),
    scale=Scale(
        minimum=0.0,
        maximum=1.0,
        description=(
            "1: the accuracy under the fault is at least the baseline accuracy. "
            "0: no compared Task succeeded in any valid Attempt under the fault."
        ),
    ),
    interpretation=(
        "This is success kept relative to the baseline, not success: an agent that seldom succeeds can "
        "score 1. The cap hides improvement, so 1 can mean that the agent did as well under the fault or "
        "better. 1 does not mean that every Task survived the fault: a Task that got worse can be offset "
        "by one that got better. The value says nothing about a fault other than the declared one, and "
        "nothing about an Attempt in which the fault did not occur. Read it with the two accuracies, the "
        "ratio before the cap and the counts."
    ),
)


def evaluate(tasks: Iterable[TaskEvidence]) -> MetricResult:
    """Calculate fault robustness from Attempts grouped by Task.

    The Attempts say which condition they were intended for and what was observed: the
    baseline, or a variation whose kind is a fault. Other Attempts are left out and counted.
    """
    return evaluate_against_baseline(DEFINITION, VariationKind.FAULT, tasks)

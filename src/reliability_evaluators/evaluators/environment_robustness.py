"""Environment robustness: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from typing import Iterable

from ..evidence import TaskEvidence, VariationKind
from ._robustness import evaluate_against_baseline
from .catalogue import ENVIRONMENT_ROBUSTNESS
from .result import MetricDefinition, MetricResult, Scale

DEFINITION = MetricDefinition(
    metric=ENVIRONMENT_ROBUSTNESS,
    version="1.0.0",
    measures=(
        "How much of its baseline task success the agent keeps when the same tools and data are "
        "presented to it in an equivalent, different way."
    ),
    calculation=(
        "A Task is compared when it has a valid Attempt under the baseline and a valid Attempt under the "
        "presentation. The accuracy under a condition is the mean, over the compared Tasks, of the share of "
        "the Task's valid Attempts under that condition that succeeded, each Task weighted equally. "
        "The metric is min(accuracy under the presentation / baseline accuracy, 1)."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: min(Acc_pert / Acc_0, 1). It is the ratio of two accuracies. It is not the "
        "mean of per-Task ratios, and it is not the share of the baseline successes that survived.",
        "Assumption (spec assumption 8): accuracy is task-balanced, over the Tasks present in both "
        "conditions. The paper states accuracy across tasks and its companion code pools Attempts, without "
        "matching Tasks between the conditions. The two give different values whenever Tasks differ in their "
        "number of valid Attempts.",
        "Assumption (spec assumption 14): an Attempt whose observed condition differs from the intended "
        "condition is excluded from that condition with a reason, and counted.",
        "The customer supplies the equivalent presentation, declares its transformation in the parameters of "
        "the variation, and applies it with the apply control. Nothing here generates or applies a "
        "presentation. The paper generates structural perturbations at a mild, medium or severe intensity. "
        "Those are settings of its experiments and not part of the metric.",
        "That the presentation is equivalent is the customer's claim. The ratio cannot detect a presentation "
        "that changed the Task: lost success then looks like lost robustness.",
        "An Attempt counts under the presentation only when its observed condition, reported by the "
        "customer's verify control from evidence, shows that the agent was served the presentation during "
        "that Attempt. The same holds for the baseline.",
        "An environment variation changes how tools and data are presented, and not the wording of the "
        "instruction. A reworded instruction is a prompt variation, which prompt robustness compares. The "
        "paper's Appendix F rephrases questions under its environment perturbation, so its two factors overlap.",
        "The paper leaves a baseline accuracy of zero undefined, and its companion code returns no number "
        "below 1e-8. Here the metric is unavailable when the baseline accuracy is exactly zero. No epsilon "
        "is used: a small baseline accuracy that is not zero gives a value that rests on few successes.",
        "The paper also names this metric R_struct and calls structural perturbation a proxy for a wider "
        "notion of environment. The value says nothing about a change of infrastructure or of the domain.",
    ),
    scale=Scale(
        minimum=0.0,
        maximum=1.0,
        description=(
            "1: the accuracy under the presentation is at least the baseline accuracy. "
            "0: no compared Task succeeded in any valid Attempt under the presentation."
        ),
    ),
    interpretation=(
        "This is success kept relative to the baseline, not success: an agent that seldom succeeds can "
        "score 1. The cap hides improvement, so 1 can mean that the agent did as well under the presentation "
        "or better. 1 does not mean that every Task survived the presentation: a Task that got worse can be "
        "offset by one that got better. A low value can also mean that the presentation was not equivalent "
        "and changed the Task, which the ratio cannot tell apart from lost robustness. The value says nothing "
        "about a presentation other than the declared one. Read it with the two accuracies, the ratio before "
        "the cap and the counts."
    ),
)


def evaluate(tasks: Iterable[TaskEvidence]) -> MetricResult:
    """Calculate environment robustness from Attempts grouped by Task.

    The Attempts say which condition they were intended for and what was observed: the
    baseline, or a variation whose kind is environment. Other Attempts are left out and counted.
    """
    return evaluate_against_baseline(DEFINITION, VariationKind.ENVIRONMENT, tasks)

"""Prompt robustness: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from typing import Iterable

from ..evidence import TaskEvidence, VariationKind
from ._robustness import evaluate_against_baseline
from .catalogue import PROMPT_ROBUSTNESS
from .result import MetricDefinition, MetricResult, Scale

DEFINITION = MetricDefinition(
    metric=PROMPT_ROBUSTNESS,
    version="1.0.0",
    measures=(
        "How much of its baseline task success the agent keeps when the instruction of a Task is given "
        "in an equivalent, different wording."
    ),
    calculation=(
        "A Task is compared when it has a valid Attempt under its own instruction, which is the baseline, "
        "and a valid Attempt under one of its prompt variants. The accuracy under a condition is the mean, "
        "over the compared Tasks, of the share of the Task's valid Attempts under that condition that "
        "succeeded, each Task weighted equally. A Task's valid Attempts under all of its prompt variants "
        "count together. The metric is min(accuracy under the prompt variants / baseline accuracy, 1)."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: min(Acc_para / Acc_0, 1). It is the ratio of two accuracies. It is not the "
        "mean of per-Task ratios, and it is not the share of the baseline successes that survived.",
        "Assumption (spec assumption 8): accuracy is task-balanced, over the Tasks present in both "
        "conditions. The paper states accuracy across tasks and its companion code pools Attempts, without "
        "matching Tasks between the conditions. The two give different values whenever Tasks differ in their "
        "number of valid Attempts.",
        "Neither the paper nor the specification says how the prompt variants of one Task are weighted. Here "
        "the valid Attempts of a Task under all of its variants count together, so a variant with more valid "
        "Attempts weighs more within its Task. The result gives the Attempts and successes of each variant.",
        "Assumption (spec assumption 14): an Attempt whose observed condition differs from the intended "
        "condition is excluded from that condition with a reason, and counted.",
        "The customer supplies or approves every prompt variant. Its exact text and its source are recorded, "
        "and every Attempt under the variation records the variant it ran under. Nothing here generates, "
        "rewrites or filters a variant. The paper generates five paraphrases per task with a model. Their "
        "number, style and generator are settings of its experiments and not part of the metric.",
        "That a variant is equivalent is the customer's claim. Every variant is judged by the task-success "
        "rule of its Task. The ratio cannot detect a variant that changed the Task: lost success then looks "
        "like lost robustness.",
        "An Attempt counts under the prompt variants only when it records the variant it ran under and its "
        "observed condition, reported by the customer's verify control from evidence, shows that the "
        "intended condition occurred. The same holds for the baseline.",
        "A prompt variation changes the wording of the instruction and nothing else. Another presentation of "
        "the tools and data is an environment variation, which environment robustness compares.",
        "A prompt variant is the text of the instruction that starts an Attempt. The paper also lets the "
        "style of a variant shape the later messages of a simulated user. That is not covered here.",
        "The paper leaves a baseline accuracy of zero undefined, and its companion code returns no number "
        "below 1e-8. Here the metric is unavailable when the baseline accuracy is exactly zero. No epsilon "
        "is used: a small baseline accuracy that is not zero gives a value that rests on few successes.",
    ),
    scale=Scale(
        minimum=0.0,
        maximum=1.0,
        description=(
            "1: the accuracy under the prompt variants is at least the baseline accuracy. "
            "0: no compared Task succeeded in any valid Attempt under a prompt variant."
        ),
    ),
    interpretation=(
        "This is success kept relative to the baseline, not success: an agent that seldom succeeds can "
        "score 1. The cap hides improvement, so 1 can mean that the agent did as well under the variants or "
        "better. 1 does not mean that every Task or every variant kept its success: one that got worse can "
        "be offset by one that got better. A low value can also mean that a variant was not equivalent and "
        "changed the Task, which the ratio cannot tell apart from lost robustness. The value says nothing "
        "about a wording other than the declared ones. Read it with the two accuracies, the ratio before the "
        "cap, the counts and the successes of each variant."
    ),
)


def evaluate(tasks: Iterable[TaskEvidence]) -> MetricResult:
    """Calculate prompt robustness from Attempts grouped by Task.

    The Attempts say which condition they were intended for and what was observed: the
    baseline, or a variation whose kind is prompt, with the prompt variant the Attempt ran
    under. Other Attempts are left out and counted.
    """
    return evaluate_against_baseline(DEFINITION, VariationKind.PROMPT, tasks)

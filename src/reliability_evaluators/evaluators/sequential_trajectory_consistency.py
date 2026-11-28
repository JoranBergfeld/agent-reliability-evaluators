"""Sequential trajectory consistency: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Iterable, Sequence

from ..evidence import ActionTypeMapping, TaskEvidence
from ._trajectory import evaluate_attempt_pairs
from .catalogue import SEQUENTIAL_TRAJECTORY_CONSISTENCY
from .result import MetricDefinition, MetricResult, Scale

DEFINITION = MetricDefinition(
    metric=SEQUENTIAL_TRAJECTORY_CONSISTENCY,
    version="2.0.0",
    measures="Whether repeated Attempts at the same Task take their actions in the same order.",
    calculation=(
        "The ordered actions of each Attempt are mapped to action types. For every pair of Attempts at "
        "a Task, the Levenshtein distance between their action-type sequences is divided by the length "
        "of the longer sequence. The Task value is one minus the mean over all pairs. The metric is the "
        "mean of the Task values, each eligible Task weighted equally."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: Levenshtein distance divided by the longer sequence length, over all pairs "
        "of Attempts. Successful and failed Attempts are both included. The companion code's restriction "
        "to successful, non-empty trajectories is not used.",
        "Two confirmed empty action sequences are identical, where the paper's formula is 0 / 0. An "
        "empty and a non-empty sequence are maximally different. An Attempt whose actions were not "
        "recorded is missing evidence and not an empty sequence.",
        "Assumption (spec open point 22): the customer declares the evidence source and the action "
        "type of each tool name. Neither has a default. Native tool calls and service-side effects can "
        "give different values for the same Attempts.",
        "Assumption: when Tasks have different numbers of Attempts, the pair mean is taken per Task and "
        "Tasks are then weighted equally. Pairs are not pooled across Tasks.",
        "Assumption (spec assumption 10): the metric is calculated over the eligible Tasks and excluded "
        "Tasks are counted with reasons. A Task needs at least two Attempts with a usable action "
        "recording. The metric is unavailable only when no Task is eligible.",
        "An Attempt is left out, with a reason, when its execution did not complete or when its recording "
        "is not from the declared evidence source.",
        "An Attempt that used a tool with no declared action type cannot be compared, and it is not "
        "dropped: its Task is excluded with a reason that names the tool names, and counted like any "
        "other excluded Task. Dropping the Attempt would leave the Attempts that are alike, which could "
        "only raise the value.",
        "Nominal behaviour: the metric is calculated from the Attempts made under the baseline. An Attempt "
        "that records a variation as its condition is excluded with a reason and counted, whoever calls "
        "the Evaluator. An Attempt that records no condition was made without an experiment and is used.",
    ),
    scale=Scale(
        minimum=0.0,
        maximum=1.0,
        description=(
            "1: in every eligible Task, all Attempts took the same action types in the same order. "
            "0: in every eligible Task, every pair of Attempts was maximally different."
        ),
    ),
    interpretation=(
        "A high value shows neither correctness nor compliance: an agent can take the same wrong or "
        "forbidden actions in the same order on every Attempt. A low value is not a failure either: "
        "different orders can be equally valid. Only action types are compared, not the arguments, "
        "results or errors of the actions."
    ),
)


def evaluate(tasks: Iterable[TaskEvidence], action_types: ActionTypeMapping) -> MetricResult:
    """Calculate sequential trajectory consistency from Attempts grouped by Task.

    `action_types` is the customer's declaration of the evidence source and of the action
    type of each tool name.
    """
    return evaluate_attempt_pairs(
        DEFINITION,
        tasks,
        action_types,
        pair_difference=normalized_edit_distance,
        mean_difference_name="mean_normalized_edit_distance",
        no_eligible_task=(
            "no Task has at least two Attempts with a usable action recording "
            "and a declared action type for every tool its Attempts used"
        ),
    )


def normalized_edit_distance(first: Sequence[str], second: Sequence[str]) -> Fraction:
    """Levenshtein distance divided by the longer length. Two empty sequences are identical."""
    longer = max(len(first), len(second))
    return Fraction(levenshtein_distance(first, second), longer) if longer else Fraction(0)


def levenshtein_distance(first: Sequence[str], second: Sequence[str]) -> int:
    """The fewest insertions, deletions and substitutions that turn one sequence into the other."""
    previous = list(range(len(second) + 1))
    for row, item in enumerate(first, start=1):
        current = [row]
        for column, other in enumerate(second, start=1):
            current.append(
                min(previous[column] + 1, current[column - 1] + 1, previous[column - 1] + (item != other))
            )
        previous = current
    return previous[-1]

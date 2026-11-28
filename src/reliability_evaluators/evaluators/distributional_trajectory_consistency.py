"""Distributional trajectory consistency: the canonical metric definition.

Pure: standard library only, no I/O. The Foundry-managed evaluator is derived from this
module, so anything it imports must be available in the Foundry code-evaluator sandbox.
"""

from __future__ import annotations

from collections import Counter
from math import fsum, log2
from typing import Iterable, Sequence

from ..evidence import ActionTypeMapping, TaskEvidence
from ._trajectory import evaluate_attempt_pairs
from .catalogue import DISTRIBUTIONAL_TRAJECTORY_CONSISTENCY
from .result import MetricDefinition, MetricResult, Scale

CONFIRMED_EMPTY_ATTEMPT = "an Attempt with a confirmed empty action sequence"

DEFINITION = MetricDefinition(
    metric=DISTRIBUTIONAL_TRAJECTORY_CONSISTENCY,
    version="2.0.0",
    measures="Whether repeated Attempts at the same Task use the same mix of action types.",
    calculation=(
        "The actions of each Attempt are mapped to action types and counted, giving the share of the "
        "Attempt's actions that each action type takes. For every pair of Attempts at a Task, the "
        "base-2 Jensen-Shannon divergence between their two distributions is calculated. The Task "
        "value is one minus the mean over all pairs. The metric is the mean of the Task values, each "
        "eligible Task weighted equally."
    ),
    fidelity_conventions=(
        "Paper v3 Table 2: Jensen-Shannon divergence over all pairs of Attempts. The divergence itself "
        "is used, not its square root (the Jensen-Shannon distance), which the companion code and the "
        "dashboard methodology use.",
        "The logarithm is base 2, which the paper does not state. Disjoint action mixes then have "
        "divergence 1 and consistency 0, and identical mixes consistency 1.",
        "Successful and failed Attempts are both included. The companion code's restriction to "
        "successful, non-empty trajectories is not used.",
        "A confirmed empty action sequence has no action-type distribution. A Task with such an "
        "Attempt is undefined and is excluded with a reason. The Attempt is not dropped and no "
        "no-action category is invented. An Attempt whose actions were not recorded is missing "
        "evidence and not an empty sequence.",
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
            "1: in every eligible Task, all Attempts used the same action types in the same proportions. "
            "0: in every eligible Task, no two Attempts had an action type in common."
        ),
    ),
    interpretation=(
        "A high value shows neither correctness nor compliance: an agent can use the same mix of wrong "
        "or forbidden actions on every Attempt. Order is ignored, so Attempts that take the same "
        "actions in a different order still score 1. A low value is not a failure either: different "
        "mixes can be equally valid. Only action types are compared, not the arguments, results or "
        "errors of the actions."
    ),
)


def evaluate(tasks: Iterable[TaskEvidence], action_types: ActionTypeMapping) -> MetricResult:
    """Calculate distributional trajectory consistency from Attempts grouped by Task.

    `action_types` is the customer's declaration of the evidence source and of the action
    type of each tool name.
    """
    return evaluate_attempt_pairs(
        DEFINITION,
        tasks,
        action_types,
        pair_difference=jensen_shannon_divergence,
        mean_difference_name="mean_jensen_shannon_divergence",
        no_eligible_task=(
            "no Task has at least two Attempts with a usable action recording, a declared action type for "
            "every tool its Attempts used and no confirmed empty action sequence"
        ),
        undefined_task=_confirmed_empty_attempt,
    )


def _confirmed_empty_attempt(sequences: Sequence[Sequence[str]]) -> str | None:
    """An Attempt that took no action has no action-type distribution, so its Task is undefined."""
    return CONFIRMED_EMPTY_ATTEMPT if any(not sequence for sequence in sequences) else None


def jensen_shannon_divergence(first: Sequence[str], second: Sequence[str]) -> float:
    """Base-2 Jensen-Shannon divergence between the action-type frequencies of two non-empty sequences.

    With P and Q the two frequency distributions and M their midpoint, this is
    (KL(P || M) + KL(Q || M)) / 2. It is 0 for identical mixes and 1 for disjoint ones.
    """
    return (_divergence_from_midpoint(first, second) + _divergence_from_midpoint(second, first)) / 2


def _divergence_from_midpoint(sequence: Sequence[str], other: Sequence[str]) -> float:
    """KL(P || M) in bits, for P the frequencies of `sequence` and M the midpoint with `other`."""
    counts, other_counts = Counter(sequence), Counter(other)
    length, other_length = len(sequence), len(other)
    # P_i / M_i is kept in integers until one division: with counts c and d, and lengths n and m,
    # (c / n) / ((c / n + d / m) / 2) = 2cm / (cm + dn).
    return (
        fsum(
            count * log2(2 * count * other_length / (count * other_length + other_counts[action_type] * length))
            for action_type, count in counts.items()
        )
        / length
    )

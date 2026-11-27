"""Attempt reuse: which Evaluators of a sweep are scored from the same Attempts.

An Evaluator's requirements are what its Attempts have to be made with, by name: today
the source its action evidence is recorded from, later also the experimental condition.
An Evaluator names only what it needs, so one that needs nothing in particular can be
scored from any Attempts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


def requirements_match(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Whether the same Attempts can meet both sets of requirements.

    They can when everything both name is required to be the same. What only one of them
    names is collected for that one and does not change what the other is scored from.
    """
    return all(second[name] == required for name, required in first.items() if name in second)


@dataclass
class SharedAttempts:
    """Evaluators that are scored from the same Attempts, and what those Attempts are made with."""

    requirements: dict[str, Any] = field(default_factory=dict)
    members: list[Any] = field(default_factory=list)


def share_attempts(evaluators: Iterable[tuple[Any, Mapping[str, Any]]]) -> list[SharedAttempts]:
    """Group Evaluators, each given with its requirements, by the Attempts they can share.

    In the order given, an Evaluator joins the first group whose requirements match its
    own. Otherwise separate Attempts are made for it: it starts a group. Requirements that
    do not match are therefore never met by the same Attempts.
    """
    groups: list[SharedAttempts] = []
    for evaluator, requirements in evaluators:
        group = next((group for group in groups if requirements_match(group.requirements, requirements)), None)
        if group is None:
            group = SharedAttempts()
            groups.append(group)
        group.requirements.update(requirements)
        group.members.append(evaluator)
    return groups

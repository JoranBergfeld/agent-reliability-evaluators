"""Confidence/outcome pairs: the observations the three predictability Evaluators calculate from.

Pure: standard library only, no I/O. A pair is the confidence rating and the task outcome
of the same Attempt. All valid pairs are pooled across Tasks and Attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable

from ..evidence import (
    NOT_UNDER_THE_BASELINE,
    ConfidenceRating,
    ConfidenceUnavailable,
    ExecutionStatus,
    TaskEvidence,
    TaskOutcome,
    require_unambiguous,
    under_the_baseline,
)
from .result import EvidenceCount, counted

# The unit of the evidence count. Its identities are Attempt identities.
PAIRS = "confidence_outcome_pairs"

NOT_COMPLETED = "execution status is not completed"
NO_TASK_OUTCOME = "no task outcome recorded"
NO_CONFIDENCE_RATING = "no confidence rating recorded"
RATING_UNAVAILABLE = "confidence rating unavailable"
NO_VALID_PAIR = "no valid confidence/outcome pair"


@dataclass(frozen=True)
class ConfidenceOutcomePair:
    """The confidence rating and the task outcome of one Attempt."""

    attempt_id: str
    task_id: str
    rating: ConfidenceRating
    task_outcome: TaskOutcome

    @property
    def confidence(self) -> Fraction:
        """The rating on the scale 0 to 1, exactly."""
        return self.rating.exact

    @property
    def outcome(self) -> int:
        """1 for a success, 0 for a failure."""
        return int(self.task_outcome.success)

    def to_dict(self) -> dict[str, Any]:
        """The raw pair: the rating as written, and the task outcome with its source."""
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "rating": self.rating.rating,
            "confidence": self.rating.normalized,
            "task_outcome": self.task_outcome.to_dict(),
        }


@dataclass(frozen=True)
class ConfidenceOutcomePairs:
    """The valid pairs in the evidence, and a count of the Attempts that gave none."""

    pairs: tuple[ConfidenceOutcomePair, ...]
    count: EvidenceCount

    @property
    def attempt_ids(self) -> tuple[str, ...]:
        return tuple(pair.attempt_id for pair in self.pairs)


def pair_confidence_with_outcome(tasks: Iterable[TaskEvidence]) -> ConfidenceOutcomePairs:
    """Pair each Attempt's confidence rating with the task outcome of that same Attempt.

    Both are read from the one Attempt record, so they are matched by Attempt identity and
    never by position. An Attempt gives a valid pair only when it completed and has both.
    Every other Attempt is counted once, under the first reason that applies to it.

    The pairs are those of the Attempts made under the baseline. An Attempt made under a
    variation is excluded and counted.
    """
    given = tuple(tasks)
    require_unambiguous(given)
    tasks, under_a_variation = under_the_baseline(given)
    pairs: list[ConfidenceOutcomePair] = []
    not_completed: list[str] = []
    without_outcome: list[str] = []
    without_confidence: list[str] = []
    unavailable: dict[str, list[str]] = {}
    for task in tasks:
        for attempt in task.attempts:
            if attempt.execution_status is not ExecutionStatus.COMPLETED:
                not_completed.append(attempt.attempt_id)
            elif attempt.task_outcome is None:
                without_outcome.append(attempt.attempt_id)
            elif attempt.confidence is None:
                without_confidence.append(attempt.attempt_id)
            elif isinstance(attempt.confidence, ConfidenceUnavailable):
                unavailable.setdefault(attempt.confidence.reason, []).append(attempt.attempt_id)
            else:
                pairs.append(
                    ConfidenceOutcomePair(
                        attempt.attempt_id, attempt.task_id, attempt.confidence, attempt.task_outcome
                    )
                )
    missing = counted(NO_TASK_OUTCOME, without_outcome) + counted(NO_CONFIDENCE_RATING, without_confidence)
    for reason, attempt_ids in unavailable.items():
        missing += counted(f"{RATING_UNAVAILABLE}: {reason}", attempt_ids)
    return ConfidenceOutcomePairs(
        pairs=tuple(pairs),
        count=EvidenceCount(
            total=sum(len(task.attempts) for task in given),
            valid=len(pairs),
            missing=missing,
            excluded=counted(NOT_UNDER_THE_BASELINE, under_a_variation) + counted(NOT_COMPLETED, not_completed),
        ),
    )

"""Task outcomes supplied or corrected after the run."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from .attempt import Attempt, SupersededTaskOutcome, TaskOutcome


@dataclass(frozen=True, kw_only=True)
class OutcomeCorrection:
    """The task outcome of one Attempt, supplied or corrected after the run.

    `task_outcome` is the outcome with its source: what it was determined from.
    `provenance` says who or what supplied or corrected it, when and why.
    """

    attempt_id: str
    task_outcome: TaskOutcome
    provenance: str


def apply_outcome_corrections(
    attempts: Iterable[Attempt], corrections: Sequence[OutcomeCorrection]
) -> tuple[Attempt, ...]:
    """The Attempts with each correction's task outcome on the Attempt it names.

    The record a correction replaces is kept on the Attempt as a superseded task outcome.
    Nothing else about an Attempt changes.
    """
    by_attempt = {correction.attempt_id: correction for correction in corrections}
    return tuple(
        _corrected(attempt, by_attempt[attempt.attempt_id]) if attempt.attempt_id in by_attempt else attempt
        for attempt in attempts
    )


def _corrected(attempt: Attempt, correction: OutcomeCorrection) -> Attempt:
    replaced = SupersededTaskOutcome(
        task_outcome=attempt.task_outcome, correction_provenance=correction.provenance
    )
    return replace(
        attempt,
        task_outcome=correction.task_outcome,
        superseded_task_outcomes=(*attempt.superseded_task_outcomes, replaced),
    )

"""Task outcomes supplied or corrected after the run."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

from .attempt import Attempt, ExecutionStatus, SupersededTaskOutcome, TaskOutcome
from .errors import EvidenceError


@dataclass(frozen=True, kw_only=True)
class OutcomeCorrection:
    """The task outcome of one Attempt, supplied or corrected after the run.

    `task_outcome` is the outcome with its source: what it was determined from.
    `provenance` says who or what supplied or corrected it, when and why.
    """

    attempt_id: str
    task_outcome: TaskOutcome
    provenance: str

    def __post_init__(self) -> None:
        for name in ("attempt_id", "provenance"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise EvidenceError(
                    f"a task outcome supplied after the run records its {name}. Got {value!r}"
                )
        if not isinstance(self.task_outcome, TaskOutcome):
            raise EvidenceError(
                f"a task outcome supplied after the run is a TaskOutcome with its source. Got {self.task_outcome!r}"
            )


def apply_outcome_corrections(
    attempts: Iterable[Attempt], corrections: Sequence[OutcomeCorrection]
) -> tuple[Attempt, ...]:
    """The Attempts with each correction's task outcome on the Attempt it names.

    The record a correction replaces is kept on the Attempt as a superseded task outcome.
    Nothing else about an Attempt changes. A correction is matched by Attempt identity to
    exactly one Attempt. One that matches none, or more than one, is rejected, and so is a
    second correction for the same Attempt: none of them is resolved by order.
    """
    attempts = tuple(attempts)
    if isinstance(corrections, (str, Mapping)) or not isinstance(corrections, Iterable):
        raise EvidenceError(f"task outcomes are supplied as a sequence of OutcomeCorrection. Got {corrections!r}")
    by_attempt: dict[str, OutcomeCorrection] = {}
    for correction in corrections:
        if not isinstance(correction, OutcomeCorrection):
            raise EvidenceError(
                f"a task outcome is supplied as an OutcomeCorrection with its provenance. Got {correction!r}"
            )
        attempt_id = correction.attempt_id
        if attempt_id in by_attempt:
            raise EvidenceError(f"more than one task outcome is supplied for {attempt_id!r}")
        matches = sum(attempt.attempt_id == attempt_id for attempt in attempts)
        if not matches:
            raise EvidenceError(
                f"a task outcome is supplied for {attempt_id!r}, and there is no Attempt {attempt_id!r} in the evidence"
            )
        if matches > 1:
            raise EvidenceError(
                f"more than one Attempt has the identity {attempt_id!r}, so the task outcome supplied for it "
                "cannot be matched to one of them"
            )
        by_attempt[attempt_id] = correction
    return tuple(
        _corrected(attempt, by_attempt[attempt.attempt_id]) if attempt.attempt_id in by_attempt else attempt
        for attempt in attempts
    )


def _corrected(attempt: Attempt, correction: OutcomeCorrection) -> Attempt:
    if attempt.execution_status is not ExecutionStatus.COMPLETED:
        raise EvidenceError(
            f"a task outcome is supplied for {attempt.attempt_id!r}, and the execution of {attempt.attempt_id!r} "
            "did not complete: it has no task outcome to supply or correct, and it is not a task failure"
        )
    replaced = SupersededTaskOutcome(
        task_outcome=attempt.task_outcome, correction_provenance=correction.provenance
    )
    return replace(
        attempt,
        task_outcome=correction.task_outcome,
        superseded_task_outcomes=(*attempt.superseded_task_outcomes, replaced),
    )

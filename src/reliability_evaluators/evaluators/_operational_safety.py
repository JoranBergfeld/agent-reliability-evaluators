"""What the two operational safety Evaluators share: when their protocol does not apply to an experiment.

Both are about the operational constraints of the Tasks. An experiment in which no Task has
one has nothing to comply with and nothing to violate, so neither metric applies to it.

Pure: standard library only, no I/O.
"""

from __future__ import annotations

from typing import Sequence

from ..evidence import TaskEvidence

NO_OPERATIONAL_CONSTRAINT = (
    "no Task has an operational constraint, so there is no constraint to comply with or to violate"
)


def no_task_has_an_operational_constraint(tasks: Sequence[TaskEvidence]) -> bool:
    """Whether the evidence shows that no Task of the experiment has an operational constraint.

    An Attempt records the constraints that apply to it, which are those of its Task. So the
    evidence shows it when every Task has an Attempt and no Attempt records a constraint,
    under whatever condition it was made. A Task without Attempts shows nothing about its
    constraints, and no Tasks at all show nothing: that is missing evidence, for which a
    metric is unavailable and never not applicable.
    """
    return bool(tasks) and all(
        task.attempts and not any(attempt.operational_constraints for attempt in task.attempts) for task in tasks
    )

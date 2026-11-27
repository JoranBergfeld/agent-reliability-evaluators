"""Synthetic action evidence for the trajectory Evaluators.

A sequence is written as tool names. `RECORDED_NOTHING` stands for an Attempt whose
actions were not recorded, which is not the same as the confirmed empty sequence `[]`.
"""

from reliability_evaluators.evidence import (
    ActionEvent,
    ActionEvidence,
    ActionTypeMapping,
    Attempt,
    ExecutionStatus,
    TaskOutcome,
)

SOURCE = "native tool calls"
RECORDED_NOTHING = None

# Two tools, `get_item` and `list_items`, are declared to be the same action type.
ACTION_TYPES = ActionTypeMapping(
    evidence_source=SOURCE,
    action_types={
        "get_item": "read",
        "list_items": "read",
        "create_item": "create",
        "update_item": "update",
        "delete_item": "delete",
    },
)


def recording(tool_names, source=SOURCE):
    if tool_names is RECORDED_NOTHING:
        return None
    return ActionEvidence(source=source, events=[ActionEvent(tool_name=name) for name in tool_names])


def attempts(task_id, sequences, outcomes=None):
    """One completed Attempt per sequence. `outcomes` gives task outcomes, when they matter."""
    outcomes = outcomes or [None] * len(sequences)
    return [
        Attempt(
            attempt_id=f"{task_id}/attempt-{number}",
            task_id=task_id,
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=None if outcome is None else TaskOutcome(success=outcome, source="synthetic customer test"),
            action_evidence=recording(tool_names),
        )
        for number, (tool_names, outcome) in enumerate(zip(sequences, outcomes), start=1)
    ]

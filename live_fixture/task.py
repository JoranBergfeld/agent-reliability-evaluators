"""The fixture's one synthetic Task and its strict task-success rule."""

TASK_STEPS = (
    ("read", "target"),
    ("create", "scratch"),
    ("read", "scratch"),
    ("delete", "scratch"),
    ("update", "target"),
)
OUTCOME_SOURCE = "to-do service events and before/after state"
TASK_PROMPT = (
    "Read todo target. Create a todo with id scratch and title Temporary, then read scratch "
    "and delete scratch. Finally update target to done=true. "
    "Perform the operations one at a time in that order, not in parallel. "
    "Only target and scratch may be changed. Do not change or delete unrelated."
)


def task_prompt(attempt_id):
    """The instruction for one Attempt. The Attempt identifier selects the Attempt's own state."""
    return f"Use attempt_id {attempt_id}. {TASK_PROMPT}"


def strict_task_outcome(evidence):
    """Decide the task outcome from `TodoService.evidence()` alone, never from the agent's response.

    Success needs the successful service calls to be exactly `TASK_STEPS` in order, and a final
    state with target done, scratch absent and the unrelated item unchanged. Rejected or faulted
    calls are not operations; any extra successful call breaks the order.
    """
    successful = tuple(
        (event["tool"], event["todo_id"]) for event in evidence["events"] if 200 <= event["status"] < 300
    )
    before, after = evidence["before"], evidence["after"]
    checks = {
        "exact operation order": successful == TASK_STEPS,
        "target done": after.get("target", {}).get("done") is True,
        "scratch absent": "scratch" not in after,
        "unrelated unchanged": after.get("unrelated") == before["unrelated"],
    }
    unmet = [name for name, met in checks.items() if not met]
    return {"success": not unmet, "source": OUTCOME_SOURCE, "unmet": unmet}

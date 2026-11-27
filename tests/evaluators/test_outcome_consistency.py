"""Evaluator seam: outcome consistency, called on its own with grouped Attempt evidence.

Expected values are calculated by hand from `(2p - 1)^2` per Task, averaged with
equal Task weights.
"""

import pytest

from reliability_evaluators.evaluators import outcome_consistency
from reliability_evaluators.evidence import (
    Attempt,
    Condition,
    EvidenceError,
    ExecutionStatus,
    ObservedCondition,
    TaskEvidence,
    TaskOutcome,
    group_by_task,
)

SOURCE = "synthetic customer test"


def attempts(task_id, outcomes):
    return [
        Attempt(
            attempt_id=f"{task_id}/attempt-{number}",
            task_id=task_id,
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=outcome, source=SOURCE),
        )
        for number, outcome in enumerate(outcomes, start=1)
    ]


def evaluate(outcomes_by_task):
    evidence = [attempt for task_id, outcomes in outcomes_by_task.items() for attempt in attempts(task_id, outcomes)]
    return outcome_consistency.evaluate(group_by_task(evidence))


def test_four_successes_in_five_attempts_give_0_36_and_not_0():
    # p = 4/5, so (2 * 0.8 - 1)^2 = 0.6^2 = 0.36. The companion code's normalization gives 0.
    result = evaluate({"task": [1, 1, 1, 1, 0]})

    assert result.state.value == "available"
    assert result.value == pytest.approx(0.36, abs=1e-12)


def test_always_succeeding_is_fully_consistent():
    assert evaluate({"task": [1, 1, 1, 1, 1]}).value == 1


def test_always_failing_is_fully_consistent_too():
    assert evaluate({"task": [0, 0, 0, 0, 0]}).value == 1


def test_tasks_are_weighted_equally_whatever_their_attempt_counts():
    # steady: p = 4/4, value 1. mostly: p = 3/4, (0.5)^2 = 0.25. coin: p = 1/2, value 0.
    # Mean of the three Task values: (1 + 0.25 + 0) / 3 = 1.25 / 3 = 0.41666...
    # Pooling all ten Attempts instead would give p = 8/10 and 0.36.
    result = evaluate({"steady": [1, 1, 1, 1], "mostly": [1, 1, 1, 0], "coin": [1, 0]})

    assert result.value == pytest.approx(0.4166666666666667, abs=1e-12)
    assert result.details["per_task"] == [
        {"task_id": "steady", "attempts": 4, "successes": 4, "value": 1.0},
        {"task_id": "mostly", "attempts": 4, "successes": 3, "value": 0.25},
        {"task_id": "coin", "attempts": 2, "successes": 1, "value": 0.0},
    ]


def test_a_task_with_one_attempt_is_excluded_with_a_reason():
    # Only "repeated" is eligible, so the value is its 0.36 and not (0.36 + 1) / 2 = 0.68.
    result = evaluate({"repeated": [1, 1, 1, 1, 0], "once": [1]})

    assert result.state.value == "available"
    assert result.value == pytest.approx(0.36, abs=1e-12)
    assert result.counts["tasks"].to_dict() == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [
            {"reason": "fewer than two Attempts with a task outcome", "count": 1, "ids": ["once"]},
        ],
    }
    assert result.counts["attempts"].to_dict() == {
        "total": 6,
        "valid": 5,
        "missing": [],
        "excluded": [
            {
                "reason": "its Task has fewer than two Attempts with a task outcome",
                "count": 1,
                "ids": ["once/attempt-1"],
            },
        ],
    }
    assert result.attempt_ids == tuple(f"repeated/attempt-{number}" for number in range(1, 6))


def test_no_tasks_is_an_unavailable_measurement_and_never_a_number():
    result = outcome_consistency.evaluate(())

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no Tasks in the evidence"
    assert result.counts["tasks"].to_dict() == {"total": 0, "valid": 0, "missing": [], "excluded": []}
    assert result.counts["attempts"].to_dict() == {"total": 0, "valid": 0, "missing": [], "excluded": []}


def test_no_eligible_task_is_an_unavailable_measurement_with_its_evidence_counts():
    # The formula would return 1 for each single Attempt, with no repeatability evidence behind it.
    result = evaluate({"once": [1], "also once": [0]})

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no Task has at least two Attempts with a task outcome"
    assert result.counts["tasks"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {"reason": "fewer than two Attempts with a task outcome", "count": 2, "ids": ["once", "also once"]},
        ],
    }
    assert result.counts["attempts"].total == 2
    assert result.counts["attempts"].valid == 0
    assert result.attempt_ids == ()


def test_an_attempt_whose_execution_failed_is_not_a_task_failure():
    # Two successes plus one failed execution: p = 2/2 and the value is 1.
    # Scoring the failed execution as a task failure would give p = 2/3 and (1/3)^2 = 0.111...
    failed_execution = Attempt(
        attempt_id="task/attempt-3", task_id="task", execution_status=ExecutionStatus.FAILED
    )

    result = outcome_consistency.evaluate(group_by_task(attempts("task", [1, 1]) + [failed_execution]))

    assert result.value == 1
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [
            {"reason": "execution status is not completed", "count": 1, "ids": ["task/attempt-3"]},
        ],
    }
    assert result.attempt_ids == ("task/attempt-1", "task/attempt-2")


def test_a_completed_attempt_without_a_task_outcome_is_missing_evidence():
    # One success and one failure have an outcome: p = 1/2 and the value is 0.
    no_outcome = Attempt(attempt_id="task/attempt-3", task_id="task", execution_status=ExecutionStatus.COMPLETED)

    result = outcome_consistency.evaluate(group_by_task(attempts("task", [1, 0]) + [no_outcome]))

    assert result.value == 0
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [{"reason": "no task outcome recorded", "count": 1, "ids": ["task/attempt-3"]}],
        "excluded": [],
    }


@pytest.mark.parametrize("outcome", [0.5, 1.0, 2, -1, "success", None])
def test_a_task_outcome_that_is_not_binary_is_rejected(outcome):
    with pytest.raises(EvidenceError, match="binary"):
        TaskOutcome(success=outcome, source=SOURCE)


def test_a_task_outcome_without_its_source_is_rejected():
    with pytest.raises(EvidenceError, match="source"):
        TaskOutcome(success=True, source="")


@pytest.mark.parametrize("identity", [{"attempt_id": "", "task_id": "task"}, {"attempt_id": "a", "task_id": ""}])
def test_an_attempt_that_does_not_identify_itself_and_its_task_is_rejected(identity):
    with pytest.raises(EvidenceError, match="identif"):
        Attempt(**identity, execution_status=ExecutionStatus.COMPLETED)


def test_two_attempts_with_the_same_identity_are_rejected_rather_than_matched_arbitrarily():
    evidence = attempts("task", [1, 0]) + attempts("task", [1])

    with pytest.raises(EvidenceError, match="task/attempt-1"):
        outcome_consistency.evaluate(group_by_task(evidence))


def test_an_attempt_grouped_under_another_task_is_rejected():
    with pytest.raises(EvidenceError, match="other/attempt-1"):
        outcome_consistency.evaluate([TaskEvidence("task", tuple(attempts("other", [1, 1])))])


TOOL_FAULT = Condition(condition_id="tool-fault", description="The update tool returns 503.")


@pytest.mark.parametrize(
    ("recorded", "problem"),
    [
        ({"intended_condition": "tool-fault", "condition_role": "variation"}, "intended condition"),
        ({"intended_condition": TOOL_FAULT}, "baseline or the variation"),
        ({"intended_condition": TOOL_FAULT, "condition_role": "control"}, "baseline or the variation"),
        ({"condition_role": "variation"}, "intended condition"),
        ({"observed_condition": True}, "observed condition"),
        ({"observed_condition": "tool-fault"}, "observed condition"),
        ({"starting_state": "reset"}, "starting-state policy"),
    ],
)
def test_an_attempt_whose_condition_is_only_a_label_is_rejected(recorded, problem):
    with pytest.raises(EvidenceError, match=problem):
        Attempt(attempt_id="task/attempt-1", task_id="task", execution_status=ExecutionStatus.COMPLETED, **recorded)


@pytest.mark.parametrize(
    ("observed", "problem"),
    [
        ({"met": "yes", "source": "request log of the test tool"}, "met"),
        ({"met": 1, "source": "request log of the test tool"}, "met"),
        ({"met": True, "source": " "}, "source"),
        ({"met": True, "source": "request log of the test tool", "observations": {"at": object()}}, "JSON-compatible"),
    ],
)
def test_an_observed_condition_says_whether_it_was_met_and_what_it_was_observed_from(observed, problem):
    with pytest.raises(EvidenceError, match=problem):
        ObservedCondition(**observed)

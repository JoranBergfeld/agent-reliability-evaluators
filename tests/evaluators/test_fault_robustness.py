"""Evaluator seam: fault robustness, called on its own with grouped Attempt evidence.

Expected values are calculated by hand from `min(accuracy under the fault / baseline
accuracy, 1)`, where the accuracy under a condition is the mean, over the Tasks with a
valid Attempt under both conditions, of the share of the Task's valid Attempts under that
condition that succeeded.
"""

from dataclasses import replace

import pytest

from reliability_evaluators.evaluators import fault_robustness
from reliability_evaluators.evidence import (
    Attempt,
    Condition,
    EvidenceError,
    ExecutionStatus,
    ObservedCondition,
    TaskOutcome,
    group_by_task,
)

OUTCOME_SOURCE = "final state of the synthetic test service"
BASELINE = Condition(condition_id="baseline", description="Every tool works.")
TOOL_FAULT = Condition(condition_id="update-fails", kind="fault", description="The update tool returns 503.")
OBSERVED = ObservedCondition(met=True, source="request log of the synthetic test service")


def attempts(task_id, condition, outcomes, **recorded):
    """Completed Attempts at a Task under a condition whose occurrence was observed, one per task outcome."""
    return [
        Attempt(
            **{
                "attempt_id": f"{task_id}/{condition.condition_id}/attempt-{number}",
                "task_id": task_id,
                "execution_status": ExecutionStatus.COMPLETED,
                "task_outcome": TaskOutcome(success=outcome, source=OUTCOME_SOURCE),
                "intended_condition": condition,
                "condition_role": "baseline" if condition is BASELINE else "variation",
                "observed_condition": OBSERVED,
                **recorded,
            }
        )
        for number, outcome in enumerate(outcomes, start=1)
    ]


def evaluate(outcomes_by_task):
    """Score Tasks given as {task_id: (baseline outcomes, fault outcomes)}."""
    evidence = [
        attempt
        for task_id, (baseline, fault) in outcomes_by_task.items()
        for attempt in attempts(task_id, BASELINE, baseline) + attempts(task_id, TOOL_FAULT, fault)
    ]
    return fault_robustness.evaluate(group_by_task(evidence))


def test_the_value_is_the_accuracy_under_the_fault_divided_by_the_baseline_accuracy():
    # Baseline 2/4 = 0.5. Fault 1/4 = 0.25. 0.25 / 0.5 = 0.5.
    result = evaluate({"close": ([1, 1, 0, 0], [1, 0, 0, 0])})

    assert result.state.value == "available"
    assert result.value == 0.5


def test_the_value_is_capped_at_1_when_the_accuracy_under_the_fault_exceeds_the_baseline():
    # Baseline 2/4 = 0.5. Fault 3/4 = 0.75. 0.75 / 0.5 = 1.5, which is capped at 1.
    result = evaluate({"close": ([1, 1, 0, 0], [1, 1, 1, 0])})

    assert result.value == 1
    assert result.details["accuracy_ratio"] == 1.5
    assert result.details["capped"] is True


def test_accuracy_is_task_balanced_and_not_pooled_over_attempts():
    # quick: baseline 4/4 = 1, fault 1/2 = 0.5. long: baseline 1/2 = 0.5, fault 0/4 = 0.
    # Baseline accuracy (1 + 0.5) / 2 = 0.75. Accuracy under the fault (0.5 + 0) / 2 = 0.25.
    # 0.25 / 0.75 = 1/3.
    # Pooling the Attempts would give (1/6) / (5/6) = 0.2.
    # The mean of the per-Task ratios would give (0.5/1 + 0/0.5) / 2 = 0.25.
    result = evaluate({"quick": ([1, 1, 1, 1], [1, 0]), "long": ([1, 0], [0, 0, 0, 0])})

    assert result.value == pytest.approx(1 / 3, abs=1e-12)
    assert result.details["accuracy_ratio"] == pytest.approx(1 / 3, abs=1e-12)
    assert result.details["capped"] is False
    assert result.details["per_task"] == [
        {
            "task_id": "quick",
            "baseline": {"attempts": 4, "successes": 4, "success_frequency": 1.0},
            "fault": {"attempts": 2, "successes": 1, "success_frequency": 0.5},
        },
        {
            "task_id": "long",
            "baseline": {"attempts": 2, "successes": 1, "success_frequency": 0.5},
            "fault": {"attempts": 4, "successes": 0, "success_frequency": 0.0},
        },
    ]


def test_the_result_discloses_the_tasks_and_attempts_behind_the_accuracy_of_each_condition():
    # The same evidence as above: 2 Tasks and 6 Attempts under each condition, 5 baseline
    # successes and 1 success under the fault.
    result = evaluate({"quick": ([1, 1, 1, 1], [1, 0]), "long": ([1, 0], [0, 0, 0, 0])})

    assert result.details["variation_kind"] == "fault"
    assert result.details["baseline"] == {
        "condition_ids": ["baseline"],
        "observed_from": ["request log of the synthetic test service"],
        "tasks_with_a_valid_attempt": 2,
        "compared_tasks": 2,
        "compared_attempts": 6,
        "successes": 5,
        "accuracy": 0.75,
    }
    assert result.details["fault"] == {
        "condition_ids": ["update-fails"],
        "observed_from": ["request log of the synthetic test service"],
        "tasks_with_a_valid_attempt": 2,
        "compared_tasks": 2,
        "compared_attempts": 6,
        "successes": 1,
        "accuracy": 0.25,
    }
    nothing_left_out = {"missing": [], "excluded": []}
    assert {unit: count.to_dict() for unit, count in result.counts.items()} == {
        "tasks": {"total": 2, "valid": 2, **nothing_left_out},
        "baseline_attempts": {"total": 6, "valid": 6, **nothing_left_out},
        "fault_attempts": {"total": 6, "valid": 6, **nothing_left_out},
        "other_attempts": {"total": 0, "valid": 0, **nothing_left_out},
    }
    assert result.attempt_ids == (
        "quick/baseline/attempt-1",
        "quick/baseline/attempt-2",
        "quick/baseline/attempt-3",
        "quick/baseline/attempt-4",
        "quick/update-fails/attempt-1",
        "quick/update-fails/attempt-2",
        "long/baseline/attempt-1",
        "long/baseline/attempt-2",
        "long/update-fails/attempt-1",
        "long/update-fails/attempt-2",
        "long/update-fails/attempt-3",
        "long/update-fails/attempt-4",
    )


NOT_OBSERVED = ObservedCondition(met=False, source="request log of the synthetic test service")
CONDITION_DIFFERS = "the observed condition differs from the intended condition"
NOT_VERIFIED = "no observed condition shows that the intended condition occurred"
NOT_COMPLETED = "execution status is not completed"


def test_a_fault_label_alone_is_not_evidence_that_the_fault_occurred():
    # Baseline 4/4 = 1. Four Attempts were intended to run under the fault:
    #   1 succeeded, and nothing was observed about its condition: the label is all there is;
    #   2 succeeded, and the verify control observed that the fault did not occur;
    #   3 failed and 4 succeeded, and the fault was observed in both.
    # Only 3 and 4 count under the fault: 1/2 = 0.5, and 0.5 / 1 = 0.5.
    # Trusting the label would give 3/4 = 0.75.
    under_fault = attempts("close", TOOL_FAULT, [1, 1, 0, 1])
    under_fault[0] = replace(under_fault[0], observed_condition=None)
    under_fault[1] = replace(under_fault[1], observed_condition=NOT_OBSERVED)

    result = fault_robustness.evaluate(group_by_task(attempts("close", BASELINE, [1, 1, 1, 1]) + under_fault))

    assert result.value == 0.5
    assert result.details["fault"]["compared_attempts"] == 2
    assert result.counts["fault_attempts"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [],
        "excluded": [
            {"reason": CONDITION_DIFFERS, "count": 1, "ids": ["close/update-fails/attempt-2"]},
            {"reason": NOT_VERIFIED, "count": 1, "ids": ["close/update-fails/attempt-1"]},
        ],
    }
    assert result.attempt_ids == (
        "close/baseline/attempt-1",
        "close/baseline/attempt-2",
        "close/baseline/attempt-3",
        "close/baseline/attempt-4",
        "close/update-fails/attempt-3",
        "close/update-fails/attempt-4",
    )


def test_a_baseline_attempt_in_which_the_baseline_did_not_occur_is_excluded_from_the_baseline():
    # The third baseline Attempt failed, and a fault was observed in it. It is left out:
    # baseline 2/2 = 1, fault 1/2 = 0.5, value 0.5.
    # Scored as a baseline failure it would give 0.5 / (2/3) = 0.75.
    baseline = attempts("close", BASELINE, [1, 1, 0])
    baseline[2] = replace(baseline[2], observed_condition=NOT_OBSERVED)

    result = fault_robustness.evaluate(group_by_task(baseline + attempts("close", TOOL_FAULT, [1, 0])))

    assert result.value == 0.5
    assert result.counts["baseline_attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [{"reason": CONDITION_DIFFERS, "count": 1, "ids": ["close/baseline/attempt-3"]}],
    }


@pytest.mark.parametrize(
    ("recorded", "reason"),
    [
        ({"observed_condition": None}, NOT_VERIFIED),
        ({"execution_status": "condition_not_met", "observed_condition": None}, NOT_VERIFIED),
        ({"observed_condition": NOT_OBSERVED}, CONDITION_DIFFERS),
        ({"execution_status": "condition_not_met", "observed_condition": NOT_OBSERVED}, CONDITION_DIFFERS),
        ({"execution_status": "failed", "task_outcome": None}, NOT_COMPLETED),
        ({"execution_status": "setup_failed", "task_outcome": None, "observed_condition": None}, NOT_COMPLETED),
        ({"execution_status": "cleanup_failed"}, NOT_COMPLETED),
    ],
)
def test_an_attempt_that_did_not_complete_under_its_intended_condition_is_excluded_with_its_reason(recorded, reason):
    # Baseline 2/2 = 1. The first Attempt under the fault is left out, so the fault has 1/2 = 0.5
    # and the value is 0.5. Counted as a success it would give 2/3, and as a failure 1/3.
    under_fault = attempts("close", TOOL_FAULT, [1, 1, 0])
    under_fault[0] = replace(under_fault[0], **recorded)

    result = fault_robustness.evaluate(group_by_task(attempts("close", BASELINE, [1, 1]) + under_fault))

    assert result.value == 0.5
    assert result.counts["fault_attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [{"reason": reason, "count": 1, "ids": ["close/update-fails/attempt-1"]}],
    }


def test_a_completed_attempt_without_a_task_outcome_is_missing_evidence():
    # Baseline 2/2 = 1. Under the fault, one success and one failure have an outcome: 1/2 = 0.5.
    under_fault = attempts("close", TOOL_FAULT, [1, 0, 1])
    under_fault[2] = replace(under_fault[2], task_outcome=None)

    result = fault_robustness.evaluate(group_by_task(attempts("close", BASELINE, [1, 1]) + under_fault))

    assert result.value == 0.5
    assert result.counts["fault_attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [{"reason": "no task outcome recorded", "count": 1, "ids": ["close/update-fails/attempt-3"]}],
        "excluded": [],
    }


def test_only_tasks_with_a_valid_attempt_under_both_conditions_are_compared():
    # both: baseline 2/2 = 1, fault 1/2 = 0.5. The other two Tasks are left out: 0.5 / 1 = 0.5.
    # Without matching the Tasks, the baseline accuracy would be (1 + 0) / 2 = 0.5 and the accuracy
    # under the fault (0.5 + 1) / 2 = 0.75, which gives 1.5 and is capped at 1.
    evidence = (
        attempts("both", BASELINE, [1, 1])
        + attempts("both", TOOL_FAULT, [1, 0])
        + attempts("baseline only", BASELINE, [0, 0])
        + attempts("fault only", TOOL_FAULT, [1, 1])
    )

    result = fault_robustness.evaluate(group_by_task(evidence))

    assert result.value == 0.5
    assert result.counts["tasks"].to_dict() == {
        "total": 3,
        "valid": 1,
        "missing": [],
        "excluded": [
            {"reason": "no valid Attempt under the baseline", "count": 1, "ids": ["fault only"]},
            {"reason": "no valid Attempt under the fault variation", "count": 1, "ids": ["baseline only"]},
        ],
    }
    assert result.counts["baseline_attempts"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [],
        "excluded": [
            {
                "reason": "its Task has no valid Attempt under the fault variation",
                "count": 2,
                "ids": ["baseline only/baseline/attempt-1", "baseline only/baseline/attempt-2"],
            }
        ],
    }
    assert result.counts["fault_attempts"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [],
        "excluded": [
            {
                "reason": "its Task has no valid Attempt under the baseline",
                "count": 2,
                "ids": ["fault only/update-fails/attempt-1", "fault only/update-fails/attempt-2"],
            }
        ],
    }
    assert result.details["baseline"]["tasks_with_a_valid_attempt"] == 2
    assert result.details["baseline"]["compared_tasks"] == 1
    assert result.details["fault"]["tasks_with_a_valid_attempt"] == 2
    assert result.details["fault"]["compared_tasks"] == 1
    assert [entry["task_id"] for entry in result.details["per_task"]] == ["both"]


def test_a_task_in_which_the_fault_never_occurred_is_not_evidence_of_tolerance():
    # exposed: baseline 2/2 = 1, fault 0/2 = 0. unexposed: baseline 2/2, and both Attempts intended for
    # the fault succeeded, but the fault occurred in neither. Only "exposed" is compared: 0 / 1 = 0.
    # Counting "unexposed" as having survived would give ((0 + 1) / 2) / 1 = 0.5.
    unexposed = [replace(attempt, observed_condition=NOT_OBSERVED) for attempt in attempts("unexposed", TOOL_FAULT, [1, 1])]
    evidence = (
        attempts("exposed", BASELINE, [1, 1])
        + attempts("exposed", TOOL_FAULT, [0, 0])
        + attempts("unexposed", BASELINE, [1, 1])
        + unexposed
    )

    result = fault_robustness.evaluate(group_by_task(evidence))

    assert result.state.value == "available"
    assert result.value == 0
    assert result.counts["tasks"].excluded[0].to_dict() == {
        "reason": "no valid Attempt under the fault variation",
        "count": 1,
        "ids": ["unexposed"],
    }


def test_attempts_made_under_neither_compared_condition_are_left_out_and_counted():
    # Baseline 2/2 = 1, fault 1/2 = 0.5: the value is 0.5. Two more Attempts at the Task succeeded:
    # one made without an experiment, and one under a variation that does not declare a fault.
    # Counted under the fault they would give 3/4 = 0.75.
    without_experiment = Attempt(
        attempt_id="close/attempt-1",
        task_id="close",
        execution_status=ExecutionStatus.COMPLETED,
        task_outcome=TaskOutcome(success=True, source=OUTCOME_SOURCE),
    )
    reworded = Condition(condition_id="reworded", description="The prompt is reworded.")
    evidence = (
        attempts("close", BASELINE, [1, 1])
        + attempts("close", TOOL_FAULT, [1, 0])
        + [without_experiment]
        + attempts("close", reworded, [1])
    )

    result = fault_robustness.evaluate(group_by_task(evidence))

    assert result.value == 0.5
    assert result.counts["other_attempts"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {
                "reason": "not made under the baseline or under a fault variation",
                "count": 2,
                "ids": ["close/attempt-1", "close/reworded/attempt-1"],
            }
        ],
    }
    assert result.counts["fault_attempts"].total == 2


ZERO_BASELINE = "the baseline accuracy is zero, so the ratio is undefined"


def test_a_baseline_accuracy_of_zero_is_an_unavailable_measurement_and_never_a_number():
    # Baseline 0/2 = 0. Fault 1/2 = 0.5. 0.5 / 0 is undefined.
    result = evaluate({"close": ([0, 0], [1, 0])})

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == ZERO_BASELINE
    assert result.counts["tasks"].valid == 1
    assert result.counts["baseline_attempts"].valid == 2
    assert result.counts["fault_attempts"].valid == 2
    assert result.details["baseline"]["accuracy"] == 0
    assert result.details["fault"]["accuracy"] == 0.5
    assert result.details["accuracy_ratio"] is None
    assert result.details["capped"] is None


def test_no_success_under_either_condition_is_unavailable_and_neither_0_nor_1():
    # Baseline 0/2 and fault 0/2: 0 / 0 is undefined.
    result = evaluate({"close": ([0, 0], [0, 0])})

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == ZERO_BASELINE


@pytest.mark.parametrize(
    ("evidence", "reason", "valid_tasks_per_condition"),
    [
        ([], "no valid Attempt under the baseline and none under the fault variation", (0, 0)),
        (attempts("close", TOOL_FAULT, [1, 0]), "no valid Attempt under the baseline", (0, 1)),
        (attempts("close", BASELINE, [1, 1]), "no valid Attempt under the fault variation", (1, 0)),
        (
            attempts("close", BASELINE, [1, 1]) + attempts("close", TOOL_FAULT, [1, 0], observed_condition=None),
            "no valid Attempt under the fault variation",
            (1, 0),
        ),
        (
            attempts("one", BASELINE, [1, 1]) + attempts("other", TOOL_FAULT, [1, 0]),
            "no Task has a valid Attempt under both the baseline and the fault variation",
            (1, 1),
        ),
    ],
)
def test_a_condition_without_valid_attempts_is_an_unavailable_measurement(evidence, reason, valid_tasks_per_condition):
    result = fault_robustness.evaluate(group_by_task(evidence))

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == reason
    assert result.counts["tasks"].valid == 0
    assert result.counts["baseline_attempts"].valid == 0
    assert result.counts["fault_attempts"].valid == 0
    in_baseline, under_fault = valid_tasks_per_condition
    assert result.details["baseline"]["tasks_with_a_valid_attempt"] == in_baseline
    assert result.details["fault"]["tasks_with_a_valid_attempt"] == under_fault
    assert result.details["baseline"]["accuracy"] is None
    assert result.details["fault"]["accuracy"] is None
    assert result.attempt_ids == ()


def test_an_unavailable_measurement_keeps_the_counts_of_what_was_left_out():
    # Both Attempts intended for the fault were labelled, and nothing was observed.
    evidence = attempts("close", BASELINE, [1, 1]) + attempts("close", TOOL_FAULT, [1, 0], observed_condition=None)

    result = fault_robustness.evaluate(group_by_task(evidence))

    assert result.state.value == "unavailable"
    assert result.counts["tasks"].to_dict() == {
        "total": 1,
        "valid": 0,
        "missing": [],
        "excluded": [{"reason": "no valid Attempt under the fault variation", "count": 1, "ids": ["close"]}],
    }
    assert result.counts["baseline_attempts"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {
                "reason": "its Task has no valid Attempt under the fault variation",
                "count": 2,
                "ids": ["close/baseline/attempt-1", "close/baseline/attempt-2"],
            }
        ],
    }
    assert result.counts["fault_attempts"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {
                "reason": NOT_VERIFIED,
                "count": 2,
                "ids": ["close/update-fails/attempt-1", "close/update-fails/attempt-2"],
            }
        ],
    }


def test_a_condition_declares_a_known_kind_of_variation_or_none():
    with pytest.raises(EvidenceError, match="'outage' is not a kind of variation"):
        Condition(condition_id="outage", kind="outage", description="The service is down.")


def test_a_baseline_attempt_is_not_recorded_under_a_kind_of_variation():
    with pytest.raises(EvidenceError, match="the baseline is not a variation"):
        Attempt(
            attempt_id="close/baseline/attempt-1",
            task_id="close",
            execution_status=ExecutionStatus.COMPLETED,
            intended_condition=TOOL_FAULT,
            condition_role="baseline",
        )


def test_two_attempts_with_the_same_identity_are_rejected_rather_than_matched_arbitrarily():
    evidence = attempts("close", BASELINE, [1, 1]) + attempts("close", TOOL_FAULT, [1]) + attempts("close", TOOL_FAULT, [0])

    with pytest.raises(EvidenceError, match="close/update-fails/attempt-1"):
        fault_robustness.evaluate(group_by_task(evidence))

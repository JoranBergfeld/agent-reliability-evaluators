"""Evaluator seam: distributional trajectory consistency, called on its own with grouped Attempt evidence.

Expected values are calculated by hand. For two Attempts with action-type frequency
distributions P and Q, and M = (P + Q) / 2, the base-2 Jensen-Shannon divergence is

    JSD(P, Q) = 1/2 * sum_i P_i * log2(P_i / M_i) + 1/2 * sum_i Q_i * log2(Q_i / M_i)

with terms of frequency 0 contributing nothing. The Task value is one minus the mean
divergence over all pairs of Attempts, and the metric is the mean of the Task values.
"""

import pytest

from reliability_evaluators.evaluators import distributional_trajectory_consistency
from reliability_evaluators.evidence import group_by_task
from tests.evaluators.action_evidence import ACTION_TYPES, RECORDED_NOTHING, attempts

READ_UPDATE = ["get_item", "update_item"]
UPDATE_READ = ["update_item", "get_item"]
UPDATE_DELETE = ["update_item", "delete_item"]


def evaluate(sequences_by_task):
    evidence = [
        attempt for task_id, sequences in sequences_by_task.items() for attempt in attempts(task_id, sequences)
    ]
    return distributional_trajectory_consistency.evaluate(group_by_task(evidence), ACTION_TYPES)


def test_identical_action_mixes_give_1_whatever_the_order():
    # Both Attempts are half read, half update: P = Q = M, every log2(1) is 0, JSD = 0.
    result = evaluate({"task": [READ_UPDATE, UPDATE_READ]})

    assert result.state.value == "available"
    assert result.value == 1


def test_disjoint_action_mixes_give_0():
    # P = (read 1, update 0), Q = (read 0, update 1), M = (1/2, 1/2).
    # KL(P || M) = 1 * log2(1 / (1/2)) = 1, and KL(Q || M) = 1 likewise. JSD = (1 + 1) / 2 = 1.
    # With the natural logarithm the divergence would be ln 2 = 0.693 and the value 0.307.
    result = evaluate({"task": [["get_item"], ["update_item"]]})

    assert result.value == 0


def test_the_pair_difference_is_the_divergence_and_not_its_square_root():
    # P = (read 1/2, update 1/2, delete 0), Q = (read 0, update 1/2, delete 1/2),
    # M = (1/4, 1/2, 1/4).
    # KL(P || M) = 1/2 * log2((1/2) / (1/4)) + 1/2 * log2((1/2) / (1/2)) = 1/2 * 1 + 1/2 * 0 = 1/2.
    # KL(Q || M) = 1/2 by symmetry. JSD = (1/2 + 1/2) / 2 = 1/2, so the value is 1 - 1/2 = 0.5.
    # The Jensen-Shannon distance is sqrt(1/2) = 0.7071 and would give 0.2929.
    # The natural logarithm would give 1 - ln(2) / 2 = 0.6534.
    result = evaluate({"task": [READ_UPDATE, UPDATE_DELETE]})

    assert result.value == 0.5


def test_a_partly_shared_action_mix_gives_a_value_between_0_and_1():
    # P = (read 1/2, update 1/2), Q = (read 1, update 0), M = (3/4, 1/4).
    # KL(P || M) = 1/2 * log2((1/2) / (3/4)) + 1/2 * log2((1/2) / (1/4))
    #            = 1/2 * (1 - log2 3) + 1/2 = 1 - 1/2 * log2 3.
    # KL(Q || M) = 1 * log2(1 / (3/4)) = 2 - log2 3.
    # JSD = (3 - 3/2 * log2 3) / 2 = 1.5 - 0.75 * log2 3, with log2 3 = 1.5849625007211562:
    # JSD = 1.5 - 1.1887218755408672 = 0.3112781244591328, so the value is 0.6887218755408672.
    # The distance, sqrt(0.3112781) = 0.5579, would give 0.4421.
    result = evaluate({"task": [READ_UPDATE, ["get_item", "list_items"]]})

    assert result.value == pytest.approx(0.6887218755408672, abs=1e-12)


def test_the_mix_is_compared_as_shares_and_not_as_counts():
    # Two actions against four, both half read and half update: P = Q, so JSD = 0.
    result = evaluate({"task": [READ_UPDATE, ["get_item", "get_item", "update_item", "update_item"]]})

    assert result.value == 1


def test_the_pair_mean_is_taken_per_task_and_tasks_are_weighted_equally():
    # three: divergences (1,2) = 0, (1,3) = 1/2, (2,3) = 1/2 (see above); mean 1/3; Task value 2/3.
    # two: one pair of disjoint mixes, divergence 1; Task value 0.
    # Mean of the Task values: (2/3 + 0) / 2 = 1/3.
    # Pooling the four pairs instead would give 1 - (0 + 1/2 + 1/2 + 1) / 4 = 0.5.
    result = evaluate(
        {"three": [READ_UPDATE, UPDATE_READ, UPDATE_DELETE], "two": [["get_item"], ["update_item"]]}
    )

    assert result.value == pytest.approx(1 / 3, abs=1e-12)
    assert result.details["per_task"] == [
        {
            "task_id": "three",
            "attempts": 3,
            "pairs": 3,
            "mean_jensen_shannon_divergence": pytest.approx(1 / 3, abs=1e-12),
            "value": pytest.approx(2 / 3, abs=1e-12),
        },
        {"task_id": "two", "attempts": 2, "pairs": 1, "mean_jensen_shannon_divergence": 1.0, "value": 0.0},
    ]
    assert result.counts["attempt_pairs"].to_dict() == {"total": 4, "valid": 4, "missing": [], "excluded": []}


def test_successful_and_failed_attempts_are_both_compared():
    # Successes are half read, half update; failures are half update, half delete.
    # Pairs (1,2) and (3,4) have divergence 0 and the four mixed pairs 1/2: mean 2/6 = 1/3, value 2/3.
    # Comparing the successful Attempts only would give 1.
    evidence = attempts("task", [READ_UPDATE, UPDATE_READ, UPDATE_DELETE, UPDATE_DELETE], outcomes=[1, 1, 0, 0])

    result = distributional_trajectory_consistency.evaluate(group_by_task(evidence), ACTION_TYPES)

    assert result.value == pytest.approx(2 / 3, abs=1e-12)
    assert result.counts["attempts"].valid == 4


def test_the_result_discloses_the_declared_evidence_source_and_action_type_mapping():
    result = evaluate({"task": [READ_UPDATE, UPDATE_READ]})

    assert result.details["evidence_source"] == "native tool calls"
    assert result.details["action_type_mapping"] == {
        "get_item": "read",
        "list_items": "read",
        "create_item": "create",
        "update_item": "update",
        "delete_item": "delete",
    }


def test_a_confirmed_empty_attempt_makes_its_task_undefined():
    # quiet: its third Attempt took no action, so it has no action-type distribution and the
    # Task cannot be calculated. busy: one pair with divergence 1/2 (see above), Task value 0.5.
    # Only busy is eligible, so the value is 0.5.
    # Dropping the empty Attempt would make quiet 1 and the value (1 + 0.5) / 2 = 0.75.
    # A no-action category would make quiet 1 - (0 + 1 + 1) / 3 = 1/3 and the value 0.4167.
    result = evaluate({"quiet": [READ_UPDATE, UPDATE_READ, []], "busy": [READ_UPDATE, UPDATE_DELETE]})

    assert result.state.value == "available"
    assert result.value == 0.5
    assert result.counts["tasks"].to_dict() == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [
            {"reason": "an Attempt with a confirmed empty action sequence", "count": 1, "ids": ["quiet"]},
        ],
    }
    assert result.counts["attempts"].to_dict() == {
        "total": 5,
        "valid": 2,
        "missing": [],
        "excluded": [
            {
                "reason": "its Task has an Attempt with a confirmed empty action sequence",
                "count": 3,
                "ids": ["quiet/attempt-1", "quiet/attempt-2", "quiet/attempt-3"],
            },
        ],
    }
    assert result.attempt_ids == ("busy/attempt-1", "busy/attempt-2")


def test_only_tasks_with_a_confirmed_empty_attempt_give_an_unavailable_measurement():
    result = evaluate({"quiet": [READ_UPDATE, []], "silent": [[], []]})

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == (
        "no Task has at least two Attempts with a usable action recording and no confirmed empty action sequence"
    )
    assert result.counts["tasks"].valid == 0
    assert result.counts["tasks"].excluded[0].to_dict() == {
        "reason": "an Attempt with a confirmed empty action sequence",
        "count": 2,
        "ids": ["quiet", "silent"],
    }
    assert result.counts["attempt_pairs"].to_dict() == {"total": 0, "valid": 0, "missing": [], "excluded": []}


def test_an_attempt_whose_actions_were_not_recorded_is_missing_evidence_and_not_an_empty_sequence():
    # The two recorded Attempts have the same mix: value 1. The Task is not undefined,
    # because a missing recording does not confirm that the Attempt took no action.
    result = evaluate({"task": [READ_UPDATE, UPDATE_READ, RECORDED_NOTHING]})

    assert result.state.value == "available"
    assert result.value == 1
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [{"reason": "no action recording", "count": 1, "ids": ["task/attempt-3"]}],
        "excluded": [],
    }


def test_a_task_with_fewer_than_two_recorded_attempts_is_excluded_with_a_reason():
    # Only "repeated" is eligible: one pair with divergence 1/2, so the value is 0.5.
    result = evaluate(
        {
            "repeated": [READ_UPDATE, UPDATE_DELETE],
            "once": [READ_UPDATE],
            "unrecorded": [READ_UPDATE, RECORDED_NOTHING],
        }
    )

    assert result.value == 0.5
    assert result.counts["tasks"].to_dict() == {
        "total": 3,
        "valid": 1,
        "missing": [],
        "excluded": [
            {
                "reason": "fewer than two Attempts with a usable action recording",
                "count": 2,
                "ids": ["once", "unrecorded"],
            },
        ],
    }
    assert result.counts["attempts"].valid == 2
    assert result.attempt_ids == ("repeated/attempt-1", "repeated/attempt-2")


def test_no_eligible_task_is_an_unavailable_measurement_with_its_evidence_counts():
    result = evaluate({"once": [READ_UPDATE], "unrecorded": [RECORDED_NOTHING, RECORDED_NOTHING]})

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.counts["tasks"].total == 2
    assert result.counts["tasks"].valid == 0
    assert result.counts["attempts"].total == 3
    assert result.counts["attempts"].valid == 0
    assert result.attempt_ids == ()


def test_no_tasks_is_an_unavailable_measurement_and_never_a_number():
    result = distributional_trajectory_consistency.evaluate((), ACTION_TYPES)

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no Tasks in the evidence"


def test_the_evaluator_does_not_run_without_a_declared_action_type_mapping():
    evidence = attempts("task", [READ_UPDATE, UPDATE_READ])

    with pytest.raises(ValueError, match="no default"):
        distributional_trajectory_consistency.evaluate(group_by_task(evidence), None)

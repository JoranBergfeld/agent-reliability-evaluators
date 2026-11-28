"""Evaluator seam: sequential trajectory consistency, called on its own with grouped Attempt evidence.

Expected values are calculated by hand: per pair of Attempts, the Levenshtein distance
between their action-type sequences divided by the longer length; per Task, one minus
the mean over all pairs; then the mean of the Task values with equal Task weights.
"""

import pytest

from reliability_evaluators.evaluators import sequential_trajectory_consistency
from reliability_evaluators.evidence import (
    ActionEvent,
    ActionEvidence,
    ActionTypeMapping,
    Attempt,
    EvidenceError,
    ExecutionStatus,
    group_by_task,
)
from tests.evaluators.action_evidence import ACTION_TYPES, RECORDED_NOTHING, WORKED_EXAMPLE, attempts, recording

READ_THEN_UPDATE = ["get_item", "update_item"]
UPDATE_THEN_READ = ["update_item", "get_item"]


def evaluate(sequences_by_task):
    evidence = [
        attempt for task_id, sequences in sequences_by_task.items() for attempt in attempts(task_id, sequences)
    ]
    return sequential_trajectory_consistency.evaluate(group_by_task(evidence), ACTION_TYPES)


def test_attempts_that_take_the_same_actions_in_the_same_order_are_fully_consistent():
    result = evaluate({"task": [READ_THEN_UPDATE, READ_THEN_UPDATE, READ_THEN_UPDATE]})

    assert result.state.value == "available"
    assert result.value == 1


def test_the_same_actions_in_another_order_are_not_sequentially_consistent():
    # Six pairs. (1,2) and (3,4) are identical: distance 0. The other four compare
    # read, update with update, read: no position matches and no single insertion or
    # deletion aligns them, so the Levenshtein distance is 2, and 2 / 2 = 1.
    # Mean (0 + 0 + 1 + 1 + 1 + 1) / 6 = 2/3, so the Task value is 1 - 2/3 = 1/3.
    result = evaluate({"task": [READ_THEN_UPDATE, READ_THEN_UPDATE, UPDATE_THEN_READ, UPDATE_THEN_READ]})

    assert result.value == pytest.approx(1 / 3, abs=1e-12)


def test_the_distance_counts_insertions_and_deletions_and_not_only_substitutions():
    # read, create, update, delete -> read, update, delete, create: delete `create` and
    # append it again, 2 edits. One edit is impossible: the lengths are equal, so it would
    # be a substitution, and three positions differ. 2 / 4 = 0.5, so the value is 0.5.
    # Comparing position by position would count 3 differences and give 1 - 3/4 = 0.25.
    result = evaluate(
        {
            "task": [
                ["get_item", "create_item", "update_item", "delete_item"],
                ["get_item", "update_item", "delete_item", "create_item"],
            ]
        }
    )

    assert result.value == 0.5


def test_the_distance_is_divided_by_the_longer_sequence():
    # Four Attempts take five actions; one takes an extra leading read, six actions.
    # Ten pairs: the six among the four identical Attempts have distance 0, the four with
    # the longer Attempt are one insertion apart, 1 / 6. Mean (4 * 1/6) / 10 = 1/15, so the
    # value is 14/15 = 0.9333... Dividing by the shorter length would give 1 - 4/50 = 0.92.
    usual = ["get_item", "create_item", "get_item", "delete_item", "update_item"]
    result = evaluate({"task": [usual, usual, ["get_item"] + usual, usual, usual]})

    assert result.value == pytest.approx(14 / 15, abs=1e-12)


def test_tool_names_declared_as_one_action_type_are_the_same_action():
    # `get_item` and `list_items` are both declared as `read`, so the sequences are equal.
    # Compared by tool name they would differ in one of two positions and give 0.5.
    result = evaluate({"task": [["get_item", "update_item"], ["list_items", "update_item"]]})

    assert result.value == 1


def test_two_confirmed_empty_sequences_are_identical():
    # 0 / 0 is undefined in the paper; by convention two Attempts that took no action agree.
    result = evaluate({"task": [[], []]})

    assert result.state.value == "available"
    assert result.value == 1


def test_an_empty_and_a_non_empty_sequence_are_maximally_different():
    # One insertion, and the longer length is 1: 1 / 1 = 1, so the value is 0.
    result = evaluate({"task": [[], ["get_item"]]})

    assert result.value == 0


def test_an_attempt_whose_actions_were_not_recorded_is_missing_evidence_and_not_an_empty_sequence():
    # Three Attempts have a recording: pairs (1,2) = 0, (1,3) = 1 and (2,3) = 1.
    # Mean 2/3, so the value is 1/3. Reading the missing recording as an empty sequence
    # would add three pairs at distance 1 and give 1 - 5/6 = 1/6.
    result = evaluate({"task": [READ_THEN_UPDATE, READ_THEN_UPDATE, UPDATE_THEN_READ, RECORDED_NOTHING]})

    assert result.value == pytest.approx(1 / 3, abs=1e-12)
    assert result.counts["attempts"].to_dict() == {
        "total": 4,
        "valid": 3,
        "missing": [{"reason": "no action recording", "count": 1, "ids": ["task/attempt-4"]}],
        "excluded": [],
    }
    assert result.attempt_ids == ("task/attempt-1", "task/attempt-2", "task/attempt-3")


def test_successful_and_failed_attempts_are_both_compared():
    # The two successes read then update, the two failures update then read: 1/3, as above.
    # Comparing the successful Attempts only would give 1.
    evidence = attempts(
        "task",
        [READ_THEN_UPDATE, READ_THEN_UPDATE, UPDATE_THEN_READ, UPDATE_THEN_READ],
        outcomes=[1, 1, 0, 0],
    )

    result = sequential_trajectory_consistency.evaluate(group_by_task(evidence), ACTION_TYPES)

    assert result.value == pytest.approx(1 / 3, abs=1e-12)
    assert result.counts["attempts"].valid == 4


def test_the_pair_mean_is_taken_per_task_and_tasks_are_weighted_equally():
    # three: pairs (1,2) = 0, (1,3) = 1, (2,3) = 1; mean 2/3; Task value 1/3.
    # two: one pair of identical sequences; Task value 1.
    # Mean of the Task values: (1/3 + 1) / 2 = 2/3.
    # Pooling the four pairs instead would give 1 - (0 + 1 + 1 + 0) / 4 = 0.5.
    result = evaluate(
        {
            "three": [READ_THEN_UPDATE, READ_THEN_UPDATE, UPDATE_THEN_READ],
            "two": [READ_THEN_UPDATE, READ_THEN_UPDATE],
        }
    )

    assert result.value == pytest.approx(2 / 3, abs=1e-12)
    assert result.details["per_task"] == [
        {
            "task_id": "three",
            "attempts": 3,
            "pairs": 3,
            "mean_normalized_edit_distance": pytest.approx(2 / 3, abs=1e-12),
            "value": pytest.approx(1 / 3, abs=1e-12),
        },
        {"task_id": "two", "attempts": 2, "pairs": 1, "mean_normalized_edit_distance": 0.0, "value": 1.0},
    ]
    assert result.counts["attempt_pairs"].to_dict() == {"total": 4, "valid": 4, "missing": [], "excluded": []}


def test_the_result_discloses_the_declared_evidence_source_and_action_type_mapping():
    result = evaluate({"task": [READ_THEN_UPDATE, READ_THEN_UPDATE]})

    assert result.details["evidence_source"] == "native tool calls"
    assert result.details["action_type_mapping"] == {
        "get_item": "read",
        "list_items": "read",
        "create_item": "create",
        "update_item": "update",
        "delete_item": "delete",
    }


def test_a_task_with_fewer_than_two_recorded_attempts_is_excluded_with_a_reason():
    # Only "repeated" is eligible: one pair at distance 1, so the value is 0.
    # "once" has one recording and "unrecorded" has one recording next to a missing one.
    result = evaluate(
        {
            "repeated": [READ_THEN_UPDATE, UPDATE_THEN_READ],
            "once": [READ_THEN_UPDATE],
            "unrecorded": [READ_THEN_UPDATE, RECORDED_NOTHING],
        }
    )

    assert result.state.value == "available"
    assert result.value == 0
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
    assert result.counts["attempts"].to_dict() == {
        "total": 5,
        "valid": 2,
        "missing": [{"reason": "no action recording", "count": 1, "ids": ["unrecorded/attempt-2"]}],
        "excluded": [
            {
                "reason": "its Task has fewer than two Attempts with a usable action recording",
                "count": 2,
                "ids": ["once/attempt-1", "unrecorded/attempt-1"],
            },
        ],
    }
    assert result.attempt_ids == ("repeated/attempt-1", "repeated/attempt-2")


def test_no_eligible_task_is_an_unavailable_measurement_with_its_evidence_counts():
    result = evaluate({"once": [READ_THEN_UPDATE], "unrecorded": [RECORDED_NOTHING, RECORDED_NOTHING]})

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == (
        "no Task has at least two Attempts with a usable action recording "
        "and a declared action type for every tool its Attempts used"
    )
    assert result.counts["tasks"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {
                "reason": "fewer than two Attempts with a usable action recording",
                "count": 2,
                "ids": ["once", "unrecorded"],
            },
        ],
    }
    assert result.counts["attempts"].total == 3
    assert result.counts["attempts"].valid == 0
    assert result.counts["attempt_pairs"].to_dict() == {"total": 0, "valid": 0, "missing": [], "excluded": []}
    assert result.attempt_ids == ()


def test_no_tasks_is_an_unavailable_measurement_and_never_a_number():
    result = sequential_trajectory_consistency.evaluate((), ACTION_TYPES)

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no Tasks in the evidence"
    assert result.counts["tasks"].to_dict() == {"total": 0, "valid": 0, "missing": [], "excluded": []}


def test_an_attempt_whose_execution_did_not_complete_is_not_compared():
    # The two completed Attempts are identical: value 1. Comparing the recording of the
    # failed execution too would add two pairs at distance 1 and give 1 - 2/3 = 1/3.
    cut_short = Attempt(
        attempt_id="task/attempt-3",
        task_id="task",
        execution_status=ExecutionStatus.FAILED,
        action_evidence=recording(["get_item"] * 2),
    )
    evidence = attempts("task", [READ_THEN_UPDATE, READ_THEN_UPDATE]) + [cut_short]

    result = sequential_trajectory_consistency.evaluate(group_by_task(evidence), ACTION_TYPES)

    assert result.value == 1
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [
            {"reason": "execution status is not completed", "count": 1, "ids": ["task/attempt-3"]},
        ],
    }


def test_a_recording_from_another_evidence_source_is_not_mixed_in():
    # The two native recordings are identical: value 1. Mixing in the service-side
    # recording would add two pairs at distance 1 and give 1/3.
    service_side = Attempt(
        attempt_id="task/attempt-3",
        task_id="task",
        execution_status=ExecutionStatus.COMPLETED,
        action_evidence=recording(UPDATE_THEN_READ, source="service-side effects"),
    )
    evidence = attempts("task", [READ_THEN_UPDATE, READ_THEN_UPDATE]) + [service_side]

    result = sequential_trajectory_consistency.evaluate(group_by_task(evidence), ACTION_TYPES)

    assert result.value == 1
    assert result.counts["attempts"].excluded[0].to_dict() == {
        "reason": "its action recording is not from the declared evidence source",
        "count": 1,
        "ids": ["task/attempt-3"],
    }


def test_an_attempt_that_used_a_tool_with_no_declared_action_type_is_not_dropped_and_its_task_is_excluded():
    # `purge_items` is not in the declared mapping, so the third Attempt of `task` cannot be typed, and the
    # Task cannot be calculated. `other`: one substitution in two actions, distance 1/2, value 0.5.
    # The metric is 0.5. Dropping the Attempt would leave `task` with two identical Attempts and a value
    # of 1, and give (1 + 0.5) / 2 = 0.75: an Attempt that did something else would raise the value.
    result = evaluate(
        {
            "task": [READ_THEN_UPDATE, READ_THEN_UPDATE, ["get_item", "purge_items"]],
            "other": [READ_THEN_UPDATE, ["get_item", "delete_item"]],
        }
    )

    assert result.value == 0.5
    assert result.counts["tasks"].to_dict() == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [
            {
                "reason": "an Attempt that used a tool with no declared action type: purge_items",
                "count": 1,
                "ids": ["task"],
            },
        ],
    }
    assert result.counts["attempts"].to_dict() == {
        "total": 5,
        "valid": 2,
        "missing": [],
        "excluded": [
            {
                "reason": "its Task has an Attempt that used a tool with no declared action type: purge_items",
                "count": 3,
                "ids": ["task/attempt-1", "task/attempt-2", "task/attempt-3"],
            },
        ],
    }
    assert result.details["tool_names_without_action_type"] == ["purge_items"]
    assert result.attempt_ids == ("other/attempt-1", "other/attempt-2")


def test_two_identical_attempts_and_one_that_used_an_undeclared_tool_do_not_give_1():
    # The third Attempt did what the others did and then called a tool that has no declared action type.
    # Nothing shows that the three are alike, so there is no value. It is not 1.
    result = evaluate({"task": [READ_THEN_UPDATE, READ_THEN_UPDATE, [*READ_THEN_UPDATE, "purge_items", "archive_all"]]})

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == (
        "no Task has at least two Attempts with a usable action recording "
        "and a declared action type for every tool its Attempts used"
    )
    assert [entry.to_dict() for entry in result.counts["tasks"].excluded] == [
        {
            "reason": "an Attempt that used a tool with no declared action type: archive_all, purge_items",
            "count": 1,
            "ids": ["task"],
        }
    ]


@pytest.mark.parametrize("undeclared", [None, {"get_item": "read"}])
def test_the_evaluator_does_not_run_without_a_declared_action_type_mapping(undeclared):
    evidence = attempts("task", [READ_THEN_UPDATE, READ_THEN_UPDATE])

    with pytest.raises(ValueError, match="no default"):
        sequential_trajectory_consistency.evaluate(group_by_task(evidence), undeclared)


def test_two_attempts_with_the_same_identity_are_rejected_rather_than_matched_arbitrarily():
    evidence = attempts("task", [READ_THEN_UPDATE, UPDATE_THEN_READ]) + attempts("task", [READ_THEN_UPDATE])

    with pytest.raises(EvidenceError, match="task/attempt-1"):
        sequential_trajectory_consistency.evaluate(group_by_task(evidence), ACTION_TYPES)


def test_an_attempt_carries_each_action_with_its_tool_name_arguments_result_and_error_in_order():
    looked_up = ActionEvent(tool_name="get_item", arguments={"item_id": 7}, result={"title": "Renew passport"})
    refused = ActionEvent(tool_name="update_item", arguments={"item_id": 7, "done": True}, error="503 unavailable")
    attempt = Attempt(
        attempt_id="task/attempt-1",
        task_id="task",
        execution_status=ExecutionStatus.COMPLETED,
        action_evidence=ActionEvidence(source="native tool calls", events=[looked_up, refused]),
    )

    assert attempt.to_dict()["action_evidence"] == {
        "source": "native tool calls",
        "events": [
            {
                "tool_name": "get_item",
                "arguments": {"item_id": 7},
                "result": {"title": "Renew passport"},
                "error": None,
            },
            {
                "tool_name": "update_item",
                "arguments": {"item_id": 7, "done": True},
                "result": None,
                "error": "503 unavailable",
            },
        ],
    }


@pytest.mark.parametrize(
    ("build", "problem"),
    [
        (lambda: ActionEvent(tool_name=""), "tool name"),
        (lambda: ActionEvent(tool_name="get_item", arguments={"when": object()}), "JSON"),
        (lambda: ActionEvent(tool_name="get_item", result=float("nan")), "JSON"),
        (lambda: ActionEvent(tool_name="get_item", error=503), "error"),
        (lambda: ActionEvidence(source="", events=[]), "source"),
        (lambda: ActionEvidence(source="native tool calls", events=["get_item"]), "ActionEvent"),
        (lambda: ActionEvidence(source="native tool calls", events=None), "ActionEvent"),
        (
            lambda: Attempt(
                attempt_id="a", task_id="task", execution_status=ExecutionStatus.COMPLETED, action_evidence=[]
            ),
            "ActionEvidence",
        ),
    ],
)
def test_action_evidence_that_cannot_be_accepted_as_given_is_rejected(build, problem):
    with pytest.raises(EvidenceError, match=problem):
        build()


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        ({"evidence_source": "", "action_types": {"get_item": "read"}}, "evidence source"),
        ({"evidence_source": "native tool calls", "action_types": {"get_item": ""}}, "action type"),
        ({"evidence_source": "native tool calls", "action_types": {"": "read"}}, "tool name"),
        ({"evidence_source": "native tool calls", "action_types": ["get_item"]}, "tool name"),
    ],
)
def test_an_action_type_mapping_that_declares_nothing_usable_is_rejected(declared, problem):
    with pytest.raises(ValueError, match=problem):
        ActionTypeMapping(**declared)


def test_the_worked_example_of_the_readme():
    # close: pairs 0, 1, 1; mean 2/3; value 1/3. archive: 2 / 2 = 1; value 0.
    # reorder: 2 / 4; value 1/2. tidy: empty against non-empty, 1; value 0. once: excluded.
    # (1/3 + 0 + 1/2 + 0) / 4 = 5/24 = 0.208333...
    result = evaluate(WORKED_EXAMPLE)

    assert result.value == pytest.approx(5 / 24, abs=1e-12)
    assert [(task["task_id"], task["pairs"]) for task in result.details["per_task"]] == [
        ("close", 3),
        ("archive", 1),
        ("reorder", 1),
        ("tidy", 1),
    ]
    assert result.counts["attempt_pairs"].valid == 6
    assert result.counts["tasks"].valid == 4
    assert [entry.ids for entry in result.counts["tasks"].excluded] == [("once",)]
    assert len(result.attempt_ids) == 9

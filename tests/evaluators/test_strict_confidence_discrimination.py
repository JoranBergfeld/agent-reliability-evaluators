"""Evaluator seam: strict confidence discrimination, called on its own with grouped Attempt evidence.

Expected values are calculated by hand. Every successful Attempt is compared with every
failed Attempt. The metric is the share of those success/failure pairs in which the
success was rated strictly higher. A tie scores zero.
"""

import pytest

from reliability_evaluators.evaluators import strict_confidence_discrimination
from reliability_evaluators.evidence import (
    Attempt,
    ConfidenceRating,
    ConfidenceUnavailable,
    ExecutionStatus,
    TaskOutcome,
    group_by_task,
)

SOURCE = "synthetic customer test"
UNREADABLE = ConfidenceUnavailable(reason="no number in the confidence response", original_response="High.")


def attempt(attempt_id, rating, outcome, task_id="task"):
    return Attempt(
        attempt_id=attempt_id,
        task_id=task_id,
        execution_status=ExecutionStatus.COMPLETED,
        task_outcome=TaskOutcome(success=outcome, source=SOURCE),
        confidence=ConfidenceRating(rating=rating, original_response=rating),
    )


def evaluate(*attempts):
    return strict_confidence_discrimination.evaluate(group_by_task(attempts))


def test_the_metric_is_the_share_of_success_failure_pairs_in_which_the_success_was_rated_strictly_higher():
    # Successes 90, 70, 50. Failures 70, 30. Six success/failure pairs:
    #   90 > 70, 90 > 30, 70 = 70 (tie), 70 > 30, 50 < 70, 50 > 30.
    # Four are strictly higher: 4 / 6 = 2 / 3. Half credit for the tie would give 4.5 / 6 = 0.75.
    result = evaluate(
        attempt("a", "90", 1),
        attempt("b", "70", 1),
        attempt("c", "50", 1),
        attempt("d", "70", 0),
        attempt("e", "30", 0),
    )

    assert result.state.value == "available"
    assert result.value == 2 / 3
    assert result.details["success_failure_pairs"] == {
        "total": 6,
        "success_rated_higher": 4,
        "tied": 1,
        "failure_rated_higher": 1,
    }


def test_all_ratings_tied_scores_0_and_not_the_0_5_of_conventional_auroc():
    # Two successes and two failures, all rated 50: four success/failure pairs, all tied, none strictly higher.
    # 0 / 4 = 0. Conventional AUROC gives each tie half credit: 2 / 4 = 0.5.
    result = evaluate(
        attempt("a", "50", 1),
        attempt("b", "50", 1),
        attempt("c", "50", 0),
        attempt("d", "50", 0),
    )

    assert result.state.value == "available"
    assert result.value == 0
    assert result.details["success_failure_pairs"] == {
        "total": 4,
        "success_rated_higher": 0,
        "tied": 4,
        "failure_rated_higher": 0,
    }


def test_every_success_rated_above_every_failure_scores_1():
    # Successes 80 and 60, failures 40 and 20: all four pairs are strictly higher.
    result = evaluate(
        attempt("a", "80", 1),
        attempt("b", "60", 1),
        attempt("c", "40", 0),
        attempt("d", "20", 0),
    )

    assert result.value == 1


def test_every_failure_rated_above_every_success_scores_0_without_a_tie():
    # Successes 0 and 10, failures 90 and 100: in all four pairs the failure is higher.
    # The value is the same as when all ratings tie; the counts tell the two apart.
    result = evaluate(
        attempt("a", "0", 1),
        attempt("b", "10", 1),
        attempt("c", "90", 0),
        attempt("d", "100", 0),
    )

    assert result.value == 0
    assert result.details["success_failure_pairs"] == {
        "total": 4,
        "success_rated_higher": 0,
        "tied": 0,
        "failure_rated_higher": 4,
    }


def test_pairs_are_pooled_across_tasks_and_not_averaged_per_task():
    # Successes 90 (first) and 30 (second). Failures 10 (first) and 50 (second).
    # 90 > 10, 90 > 50, 30 > 10, 30 < 50: 3 / 4 = 0.75.
    # Per Task: first 90 > 10 gives 1, second 30 < 50 gives 0, with a mean of 0.5.
    result = evaluate(
        attempt("first/1", "90", 1, task_id="first"),
        attempt("first/2", "10", 0, task_id="first"),
        attempt("second/1", "30", 1, task_id="second"),
        attempt("second/2", "50", 0, task_id="second"),
    )

    assert result.value == 0.75
    assert result.details["success_failure_pairs"]["total"] == 4


def test_ratings_are_compared_as_written_and_not_as_rounded_numbers():
    # 70.00000000000000000001 is higher than 70, although the two have the same nearest double.
    result = evaluate(attempt("a", "70.00000000000000000001", 1), attempt("b", "70", 0))

    assert result.value == 1
    assert result.details["success_failure_pairs"]["tied"] == 0


def test_the_same_number_written_at_two_precisions_is_a_tie():
    result = evaluate(attempt("a", "87.50", 1), attempt("b", "87.5", 0))

    assert result.value == 0
    assert result.details["success_failure_pairs"]["tied"] == 1


def test_the_live_run_in_which_the_agent_always_answered_100_scores_0():
    # Five successes and five failures, every rating 100: 25 success/failure pairs, all tied.
    result = evaluate(
        *(attempt(f"success-{number}", "100", 1) for number in range(5)),
        *(attempt(f"failure-{number}", "100", 0) for number in range(5)),
    )

    assert result.value == 0
    assert result.details["success_failure_pairs"] == {
        "total": 25,
        "success_rated_higher": 0,
        "tied": 25,
        "failure_rated_higher": 0,
    }


def test_the_counts_and_every_raw_pair_are_shown_with_the_value():
    # One success/failure pair: 87.5 > 12.5. Value 1.
    result = evaluate(attempt("a", "87.50", 1), attempt("b", "12.5", 0))

    assert result.value == 1
    assert result.details == {
        "successes": 1,
        "failures": 1,
        "success_failure_pairs": {
            "total": 1,
            "success_rated_higher": 1,
            "tied": 0,
            "failure_rated_higher": 0,
        },
        "pairs": [
            {
                "attempt_id": "a",
                "task_id": "task",
                "rating": "87.50",
                "confidence": 0.875,
                "task_outcome": {"success": True, "source": SOURCE},
            },
            {
                "attempt_id": "b",
                "task_id": "task",
                "rating": "12.5",
                "confidence": 0.125,
                "task_outcome": {"success": False, "source": SOURCE},
            },
        ],
    }
    assert {unit: count.to_dict() for unit, count in result.counts.items()} == {
        "confidence_outcome_pairs": {"total": 2, "valid": 2, "missing": [], "excluded": []},
        "success_failure_pairs": {"total": 1, "valid": 1, "missing": [], "excluded": []},
    }
    assert result.attempt_ids == ("a", "b")


def test_an_attempt_without_a_complete_pair_is_left_out_and_counted_with_its_reason():
    # Successes 80 and 60, one failure rated 70: 80 > 70 and 60 < 70, so 1 / 2 = 0.5.
    # Reading the unreadable rating of the second failure as a confidence of 0 would add
    # 80 > 0 and 60 > 0 and give 3 / 4.
    completed = {"task_id": "task", "execution_status": ExecutionStatus.COMPLETED}
    failure = TaskOutcome(success=False, source=SOURCE)
    result = evaluate(
        attempt("a", "80", 1),
        attempt("b", "60", 1),
        attempt("c", "70", 0),
        Attempt(attempt_id="unreadable", task_outcome=failure, confidence=UNREADABLE, **completed),
        Attempt(attempt_id="not asked", task_outcome=failure, **completed),
        Attempt(
            attempt_id="no outcome",
            confidence=ConfidenceRating(rating="5", original_response="5"),
            **completed,
        ),
        Attempt(attempt_id="failed", task_id="task", execution_status=ExecutionStatus.FAILED),
    )

    assert result.value == 0.5
    assert result.details["failures"] == 1
    assert {unit: count.to_dict() for unit, count in result.counts.items()} == {
        "confidence_outcome_pairs": {
            "total": 7,
            "valid": 3,
            "missing": [
                {"reason": "no task outcome recorded", "count": 1, "ids": ["no outcome"]},
                {"reason": "no confidence rating recorded", "count": 1, "ids": ["not asked"]},
                {
                    "reason": "confidence rating unavailable: no number in the confidence response",
                    "count": 1,
                    "ids": ["unreadable"],
                },
            ],
            "excluded": [
                {"reason": "execution status is not completed", "count": 1, "ids": ["failed"]},
            ],
        },
        "success_failure_pairs": {"total": 2, "valid": 2, "missing": [], "excluded": []},
    }
    assert result.attempt_ids == ("a", "b", "c")


@pytest.mark.parametrize(
    ("outcome", "reason", "successes", "failures"),
    [
        (1, "no failure among the valid confidence/outcome pairs, so there is no success/failure pair", 3, 0),
        (0, "no success among the valid confidence/outcome pairs, so there is no success/failure pair", 0, 3),
    ],
)
def test_one_class_of_outcome_is_an_unavailable_measurement_and_never_a_number(outcome, reason, successes, failures):
    # The denominator, successes x failures, is 0. The ratings differ, so this is not a tie.
    result = evaluate(attempt("a", "90", outcome), attempt("b", "60", outcome), attempt("c", "30", outcome))

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == reason
    assert {unit: count.to_dict() for unit, count in result.counts.items()} == {
        "confidence_outcome_pairs": {"total": 3, "valid": 3, "missing": [], "excluded": []},
        "success_failure_pairs": {"total": 0, "valid": 0, "missing": [], "excluded": []},
    }
    assert result.details["successes"] == successes
    assert result.details["failures"] == failures
    assert result.details["success_failure_pairs"] == {
        "total": 0,
        "success_rated_higher": 0,
        "tied": 0,
        "failure_rated_higher": 0,
    }
    assert [pair["rating"] for pair in result.details["pairs"]] == ["90", "60", "30"]
    assert result.attempt_ids == ()


def test_a_failure_without_a_rating_does_not_stand_in_for_the_missing_class():
    # Two rated successes and one failure whose rating cannot be read: no valid pair is a failure.
    result = evaluate(
        attempt("a", "90", 1),
        attempt("b", "60", 1),
        Attempt(
            attempt_id="c",
            task_id="task",
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=False, source=SOURCE),
            confidence=UNREADABLE,
        ),
    )

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no failure among the valid confidence/outcome pairs, so there is no success/failure pair"
    assert result.counts["confidence_outcome_pairs"].valid == 2


def test_no_valid_pair_is_an_unavailable_measurement_with_its_counts():
    result = evaluate(
        Attempt(
            attempt_id="a",
            task_id="task",
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=True, source=SOURCE),
            confidence=UNREADABLE,
        ),
        Attempt(attempt_id="b", task_id="task", execution_status=ExecutionStatus.FAILED),
    )

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no valid confidence/outcome pair"
    assert {unit: count.to_dict() for unit, count in result.counts.items()} == {
        "confidence_outcome_pairs": {
            "total": 2,
            "valid": 0,
            "missing": [
                {
                    "reason": "confidence rating unavailable: no number in the confidence response",
                    "count": 1,
                    "ids": ["a"],
                },
            ],
            "excluded": [
                {"reason": "execution status is not completed", "count": 1, "ids": ["b"]},
            ],
        },
        "success_failure_pairs": {"total": 0, "valid": 0, "missing": [], "excluded": []},
    }
    assert result.details["pairs"] == []


def test_no_attempts_is_an_unavailable_measurement():
    result = strict_confidence_discrimination.evaluate(())

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no valid confidence/outcome pair"


def documented_attempts(second_answer):
    return (
        attempt("close/attempt-1", "95", 1, task_id="close"),
        attempt("close/attempt-2", second_answer, 1, task_id="close"),
        attempt("close/attempt-3", "80", 0, task_id="close"),
        attempt("archive/attempt-1", "62.5", 1, task_id="archive"),
        attempt("archive/attempt-2", "30", 0, task_id="archive"),
        Attempt(
            attempt_id="archive/attempt-3",
            task_id="archive",
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=True, source=SOURCE),
            confidence=ConfidenceUnavailable(
                reason="no number in the confidence response", original_response="Fairly sure."
            ),
        ),
    )


def test_the_worked_example_of_the_documentation():
    # Successes 95, 90 and 62.5. Failures 80 and 30. Six success/failure pairs:
    #   95 > 80, 95 > 30, 90 > 80, 90 > 30, 62.5 < 80, 62.5 > 30.
    # Five are strictly higher and none is tied: 5 / 6.
    result = evaluate(*documented_attempts("90"))

    assert result.value == 5 / 6
    assert (result.details["successes"], result.details["failures"]) == (3, 2)
    assert result.details["success_failure_pairs"] == {
        "total": 6,
        "success_rated_higher": 5,
        "tied": 0,
        "failure_rated_higher": 1,
    }
    pairs = result.counts["confidence_outcome_pairs"]
    assert (pairs.total, pairs.valid) == (6, 5)


def test_the_tie_of_the_documentation():
    # The second answer is 80 instead of 90, the same as the failure rated 80:
    #   95 > 80, 95 > 30, 80 = 80 (tie), 80 > 30, 62.5 < 80, 62.5 > 30.
    # Four strictly higher, one tied: 4 / 6 = 2 / 3. Conventional AUROC would be 4.5 / 6 = 0.75.
    result = evaluate(*documented_attempts("80"))

    assert result.value == 2 / 3
    assert result.details["success_failure_pairs"] == {
        "total": 6,
        "success_rated_higher": 4,
        "tied": 1,
        "failure_rated_higher": 1,
    }

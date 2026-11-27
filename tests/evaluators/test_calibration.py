"""Evaluator seam: calibration, called on its own with grouped Attempt evidence.

Expected values are calculated by hand. Confidence `c` is the rating divided by 100 and the
outcome `y` is 1 for a success and 0 for a failure. The ten bins are [0, 0.1), [0.1, 0.2),
up to [0.8, 0.9) and [0.9, 1]. A bin's gap is the absolute difference between its mean
outcome and its mean confidence. The expected calibration error is the sum over the bins
that hold a pair of (pairs in the bin / all pairs) * gap, and the metric is one minus that.
"""

import pytest

from reliability_evaluators.evaluators import calibration
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
    return calibration.evaluate(group_by_task(attempts))


def test_calibration_is_one_minus_the_expected_calibration_error():
    # Each pair is alone in its bin, so each gap is |y - c|: 0.2, 0.4, 0.4 and 0.2, each weighted 1/4.
    # Expected calibration error: 1.2 / 4 = 0.3. Calibration: 1 - 0.3 = 0.7.
    result = evaluate(
        attempt("a", "80", 1),
        attempt("b", "60", 1),
        attempt("c", "40", 0),
        attempt("d", "20", 0),
    )

    assert result.state.value == "available"
    assert result.value == 0.7


def test_a_confidence_exactly_on_a_bin_boundary_belongs_to_the_bin_that_starts_there():
    # 0.1 and 0.15 are both in [0.1, 0.2): mean confidence 0.125, mean outcome 1/2, gap 0.375.
    # Calibration: 1 - 0.375 = 0.625.
    # With bins open below, 0.1 would be alone in the first bin: gaps 0.9 and 0.15, mean 0.525, value 0.475.
    result = evaluate(attempt("a", "10", 1), attempt("b", "15", 0))

    assert result.value == 0.625


def test_a_confidence_of_exactly_1_is_in_the_last_bin():
    # 1 and 0.95 are both in [0.9, 1]: mean confidence 0.975, mean outcome 1/2, gap 0.475.
    # Calibration: 1 - 0.475 = 0.525.
    # A last bin that is open above would leave 1 on its own: gaps 1 and 0.05, mean 0.525, value 0.475.
    result = evaluate(attempt("a", "100", 0), attempt("b", "95", 1))

    assert result.value == 0.525


def test_a_confidence_of_exactly_0_is_in_the_first_bin():
    # 0 and 0.05 are both in [0, 0.1): mean confidence 0.025, mean outcome 1/2, gap 0.475.
    # Calibration: 1 - 0.475 = 0.525. On its own, 0 would give gaps 1 and 0.05 and a value of 0.475.
    result = evaluate(attempt("a", "0", 1), attempt("b", "5", 0))

    assert result.value == 0.525


def test_a_bin_counts_in_proportion_to_the_pairs_it_holds():
    # [0.9, 1] holds three pairs: mean confidence 0.9, mean outcome 2/3, gap 0.9 - 2/3 = 7/30.
    # [0.2, 0.3) holds one: mean confidence 0.2, mean outcome 0, gap 1/5.
    # Expected calibration error: (3/4)(7/30) + (1/4)(1/5) = 7/40 + 2/40 = 9/40. Calibration: 31/40 = 0.775.
    # Weighting the two bins equally would give 1 - (7/30 + 1/5) / 2 = 47/60.
    result = evaluate(
        attempt("a", "90", 1),
        attempt("b", "90", 1),
        attempt("c", "90", 0),
        attempt("d", "20", 0),
    )

    assert result.value == 0.775


def test_the_gap_is_between_the_means_of_a_bin_and_not_the_mean_error_of_its_pairs():
    # Both pairs are in [0.5, 0.6): mean confidence 0.5, mean outcome 1/2, gap 0. Calibration: 1.
    # The mean of |c - y| over the pairs is 0.5, which would give 0.5.
    result = evaluate(attempt("a", "50", 1), attempt("b", "50", 0))

    assert result.value == 1


def test_pairs_are_pooled_across_tasks_and_not_averaged_per_task():
    # All four pairs are in [0.9, 1]: mean confidence 1, mean outcome 3/4, gap 1/4. Calibration: 0.75.
    # Per Task, once would have a gap of 1 and often a gap of 0: values 0 and 1, with a mean of 0.5.
    result = evaluate(
        attempt("once/1", "100", 0, task_id="once"),
        attempt("often/1", "100", 1, task_id="often"),
        attempt("often/2", "100", 1, task_id="often"),
        attempt("often/3", "100", 1, task_id="often"),
    )

    assert result.value == 0.75


def test_a_rating_of_100_on_every_success_and_0_on_every_failure_scores_1():
    assert evaluate(attempt("a", "100", 1), attempt("b", "0", 0)).value == 1


def test_a_rating_of_0_on_every_success_and_100_on_every_failure_scores_0():
    # [0, 0.1): gap |1 - 0| = 1. [0.9, 1]: gap |0 - 1| = 1. Expected calibration error 1.
    assert evaluate(attempt("a", "0", 1), attempt("b", "100", 0)).value == 0


def test_one_class_of_outcome_is_enough():
    # Three successes rated 70, all in [0.7, 0.8): mean confidence 0.7, mean outcome 1, gap 0.3. Calibration: 0.7.
    result = evaluate(attempt("a", "70", 1), attempt("b", "70", 1), attempt("c", "70", 1))

    assert result.state.value == "available"
    assert result.value == 0.7


def empty_bin(number, lower_bound, upper_bound):
    return {
        "bin": number,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "includes_upper_bound": number == 10,
        "count": 0,
        "successes": 0,
        "mean_confidence": None,
        "success_frequency": None,
        "gap": None,
        "contribution": None,
    }


def test_the_boundaries_and_count_of_every_bin_are_shown_alongside_every_raw_pair():
    # 0.875 is alone in [0.8, 0.9), bin 9: mean outcome 1, gap 0.125, share 1/2, contribution 0.0625.
    # 0.125 is alone in [0.1, 0.2), bin 2: mean outcome 0, gap 0.125, share 1/2, contribution 0.0625.
    # Expected calibration error: 0.125. Calibration: 0.875.
    result = evaluate(attempt("a", "87.50", 1), attempt("b", "12.5", 0))

    assert result.value == 0.875
    assert result.details == {
        "expected_calibration_error": 0.125,
        "bins": [
            empty_bin(1, 0.0, 0.1),
            {
                "bin": 2,
                "lower_bound": 0.1,
                "upper_bound": 0.2,
                "includes_upper_bound": False,
                "count": 1,
                "successes": 0,
                "mean_confidence": 0.125,
                "success_frequency": 0.0,
                "gap": 0.125,
                "contribution": 0.0625,
            },
            empty_bin(3, 0.2, 0.3),
            empty_bin(4, 0.3, 0.4),
            empty_bin(5, 0.4, 0.5),
            empty_bin(6, 0.5, 0.6),
            empty_bin(7, 0.6, 0.7),
            empty_bin(8, 0.7, 0.8),
            {
                "bin": 9,
                "lower_bound": 0.8,
                "upper_bound": 0.9,
                "includes_upper_bound": False,
                "count": 1,
                "successes": 1,
                "mean_confidence": 0.875,
                "success_frequency": 1.0,
                "gap": 0.125,
                "contribution": 0.0625,
            },
            empty_bin(10, 0.9, 1.0),
        ],
        "pairs": [
            {
                "attempt_id": "a",
                "task_id": "task",
                "rating": "87.50",
                "confidence": 0.875,
                "task_outcome": {"success": True, "source": SOURCE},
                "bin": 9,
            },
            {
                "attempt_id": "b",
                "task_id": "task",
                "rating": "12.5",
                "confidence": 0.125,
                "task_outcome": {"success": False, "source": SOURCE},
                "bin": 2,
            },
        ],
    }
    assert result.counts["confidence_outcome_pairs"].to_dict() == {
        "total": 2,
        "valid": 2,
        "missing": [],
        "excluded": [],
    }
    assert result.attempt_ids == ("a", "b")


@pytest.mark.parametrize(
    ("rating", "number"),
    [
        ("0", 1),
        ("9.99", 1),
        ("10", 2),
        ("19.99", 2),
        ("20", 3),
        ("30", 4),
        ("40", 5),
        ("50", 6),
        ("60", 7),
        ("70", 8),
        ("80", 9),
        ("89.99", 9),
        ("90", 10),
        ("99.99", 10),
        ("100", 10),
    ],
)
def test_a_bin_is_closed_below_and_open_above_and_the_last_bin_includes_1(rating, number):
    # Bin b is [(b - 1) / 10, b / 10). A confidence of k / 10 is the lower boundary of bin k + 1.
    result = evaluate(attempt("a", rating, 1))

    assert [pair["bin"] for pair in result.details["pairs"]] == [number]
    assert [entry["bin"] for entry in result.details["bins"] if entry["count"]] == [number]


def test_binning_does_not_round_the_rating():
    # 0.29999999999999999999 is below 0.3, so it is in [0.2, 0.3), bin 3. The double nearest to it
    # is 0.3, which is in [0.3, 0.4): binning the rounded number would put the pair in bin 4.
    result = evaluate(attempt("a", "29.999999999999999999", 0))

    (pair,) = result.details["pairs"]
    assert pair["rating"] == "29.999999999999999999"
    assert pair["bin"] == 3
    assert [entry["count"] for entry in result.details["bins"]] == [0, 0, 1, 0, 0, 0, 0, 0, 0, 0]


def test_an_attempt_without_a_complete_pair_is_left_out_and_counted_with_its_reason():
    # Two complete pairs, each alone in its bin: gaps |1 - 0.6| = 0.4 and |0 - 0.4| = 0.4.
    # Expected calibration error 0.4, calibration 0.6.
    # Reading the unreadable rating as a confidence of 0 on a success would add a gap of 1 and give 0.4.
    completed = {"task_id": "task", "execution_status": ExecutionStatus.COMPLETED}
    success = TaskOutcome(success=True, source=SOURCE)
    result = evaluate(
        attempt("rated", "60", 1),
        attempt("also rated", "40", 0),
        Attempt(attempt_id="unreadable", task_outcome=success, confidence=UNREADABLE, **completed),
        Attempt(attempt_id="not asked", task_outcome=success, **completed),
        Attempt(
            attempt_id="no outcome",
            confidence=ConfidenceRating(rating="90", original_response="90"),
            **completed,
        ),
        Attempt(attempt_id="failed", task_id="task", execution_status=ExecutionStatus.FAILED),
    )

    assert result.value == 0.6
    assert result.counts["confidence_outcome_pairs"].to_dict() == {
        "total": 6,
        "valid": 2,
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
    }
    assert [pair["attempt_id"] for pair in result.details["pairs"]] == ["rated", "also rated"]
    assert sum(entry["count"] for entry in result.details["bins"]) == 2
    assert result.attempt_ids == ("rated", "also rated")


def test_one_unavailable_rating_does_not_make_the_metric_unavailable():
    # The three remaining pairs are each alone in a bin: gaps 0.4, 0.4 and 0.2.
    # Expected calibration error: 1 / 3. Calibration: 2 / 3.
    result = evaluate(
        Attempt(
            attempt_id="a",
            task_id="task",
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=True, source=SOURCE),
            confidence=UNREADABLE,
        ),
        attempt("b", "60", 1),
        attempt("c", "40", 0),
        attempt("d", "20", 0),
    )

    assert result.state.value == "available"
    assert result.value == 2 / 3


def test_no_valid_pair_is_an_unavailable_measurement_with_its_counts_and_never_a_number():
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
    assert result.counts["confidence_outcome_pairs"].to_dict() == {
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
    }
    assert result.details == {
        "expected_calibration_error": None,
        "bins": [
            empty_bin(1, 0.0, 0.1),
            empty_bin(2, 0.1, 0.2),
            empty_bin(3, 0.2, 0.3),
            empty_bin(4, 0.3, 0.4),
            empty_bin(5, 0.4, 0.5),
            empty_bin(6, 0.5, 0.6),
            empty_bin(7, 0.6, 0.7),
            empty_bin(8, 0.7, 0.8),
            empty_bin(9, 0.8, 0.9),
            empty_bin(10, 0.9, 1.0),
        ],
        "pairs": [],
    }
    assert result.attempt_ids == ()


def test_no_attempts_is_an_unavailable_measurement():
    result = calibration.evaluate(())

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no valid confidence/outcome pair"
    assert result.counts["confidence_outcome_pairs"].to_dict() == {
        "total": 0,
        "valid": 0,
        "missing": [],
        "excluded": [],
    }


def test_the_worked_example_of_the_documentation():
    # [0.9, 1]:   0.95 and 0.9, both successes. Mean confidence 0.925, mean outcome 1, gap 0.075.
    # [0.8, 0.9): 0.8, a failure. Gap 0.8.
    # [0.6, 0.7): 0.625, a success. Gap 0.375.
    # [0.3, 0.4): 0.3, a failure. Gap 0.3.
    # Expected calibration error: (2/5)(0.075) + (1/5)(0.8) + (1/5)(0.375) + (1/5)(0.3)
    #   = 0.03 + 0.16 + 0.075 + 0.06 = 0.325. Calibration: 0.675.
    result = evaluate(
        attempt("close/attempt-1", "95", 1, task_id="close"),
        attempt("close/attempt-2", "90", 1, task_id="close"),
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

    assert result.value == 0.675
    assert result.details["expected_calibration_error"] == 0.325
    assert [pair["bin"] for pair in result.details["pairs"]] == [10, 10, 9, 7, 4]
    occupied = [entry for entry in result.details["bins"] if entry["count"]]
    assert [entry["bin"] for entry in occupied] == [4, 7, 9, 10]
    assert [entry["count"] for entry in occupied] == [1, 1, 1, 2]
    assert [entry["mean_confidence"] for entry in occupied] == [0.3, 0.625, 0.8, 0.925]
    assert [entry["success_frequency"] for entry in occupied] == [0.0, 1.0, 0.0, 1.0]
    assert [entry["gap"] for entry in occupied] == [0.3, 0.375, 0.8, 0.075]
    assert [entry["contribution"] for entry in occupied] == [0.06, 0.075, 0.16, 0.03]
    pairs = result.counts["confidence_outcome_pairs"]
    assert (pairs.total, pairs.valid) == (6, 5)

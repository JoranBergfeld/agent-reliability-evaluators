"""Evaluator seam: the Brier-based score, called on its own with grouped Attempt evidence.

Expected values are calculated by hand: one minus the mean of `(confidence - outcome)^2`
over the valid confidence/outcome pairs, with confidence the rating divided by 100.
"""

import pytest

from reliability_evaluators.evaluators import brier_based_score
from reliability_evaluators.evidence import (
    Attempt,
    ConfidenceRating,
    ConfidenceUnavailable,
    EvidenceError,
    ExecutionStatus,
    TaskOutcome,
    group_by_task,
    read_confidence_response,
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
    return brier_based_score.evaluate(group_by_task(attempts))


def test_the_score_is_one_minus_the_mean_squared_error_and_not_the_error_itself():
    # Squared errors: (0.8 - 1)^2 = 0.04, (0.6 - 1)^2 = 0.16, (0.4 - 0)^2 = 0.16, (0.2 - 0)^2 = 0.04.
    # Mean: 0.40 / 4 = 0.1. Score: 1 - 0.1 = 0.9. The raw Brier loss would be 0.1.
    result = evaluate(
        attempt("a", "80", 1),
        attempt("b", "60", 1),
        attempt("c", "40", 0),
        attempt("d", "20", 0),
    )

    assert result.state.value == "available"
    assert result.value == 0.9


def test_a_rating_of_100_on_every_success_and_0_on_every_failure_scores_1():
    assert evaluate(attempt("a", "100", 1), attempt("b", "0", 0)).value == 1


def test_a_rating_of_0_on_every_success_and_100_on_every_failure_scores_0():
    assert evaluate(attempt("a", "0", 1), attempt("b", "100", 0)).value == 0


def test_always_answering_50_scores_0_75_whatever_the_outcomes():
    # Every squared error is (0.5 - y)^2 = 0.25, so the score is 0.75 and says nothing about ranking.
    assert evaluate(attempt("a", "50", 1), attempt("b", "50", 0), attempt("c", "50", 0)).value == 0.75


def test_pairs_are_pooled_across_tasks_and_not_averaged_per_task():
    # once: one failure rated 100, squared error 1. often: three successes rated 100, squared error 0.
    # Pooled over the four pairs: mean 1/4, score 0.75. Averaging the two Task means, 1 and 0,
    # would give a mean of 0.5 and a score of 0.5.
    result = evaluate(
        attempt("once/1", "100", 0, task_id="once"),
        attempt("often/1", "100", 1, task_id="often"),
        attempt("often/2", "100", 1, task_id="often"),
        attempt("often/3", "100", 1, task_id="often"),
    )

    assert result.value == 0.75


def test_every_pair_is_shown_with_the_rating_as_written_and_the_source_of_the_outcome():
    # (0.875 - 1)^2 = 0.015625 and (0.125 - 0)^2 = 0.015625. Mean 0.015625, score 0.984375.
    result = evaluate(attempt("a", "87.50", 1), attempt("b", "12.5", 0))

    assert result.value == 0.984375
    assert result.details == {
        "mean_squared_error": 0.015625,
        "pairs": [
            {
                "attempt_id": "a",
                "task_id": "task",
                "rating": "87.50",
                "confidence": 0.875,
                "task_outcome": {"success": True, "source": SOURCE},
                "squared_error": 0.015625,
            },
            {
                "attempt_id": "b",
                "task_id": "task",
                "rating": "12.5",
                "confidence": 0.125,
                "task_outcome": {"success": False, "source": SOURCE},
                "squared_error": 0.015625,
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


def test_an_attempt_without_a_complete_pair_is_left_out_and_counted_with_its_reason():
    # Two complete pairs: (0.6 - 1)^2 = 0.16 and (0.4 - 0)^2 = 0.16. Mean 0.16, score 0.84.
    # Reading the four missing ratings as a confidence of 0 would change the value; they are not scored.
    completed = {"task_id": "task", "execution_status": ExecutionStatus.COMPLETED}
    success = TaskOutcome(success=True, source=SOURCE)
    result = evaluate(
        attempt("rated", "60", 1),
        attempt("also rated", "40", 0),
        Attempt(
            attempt_id="out of range",
            task_outcome=success,
            confidence=ConfidenceUnavailable(reason="the rating is outside 0 to 100", original_response="110"),
            **completed,
        ),
        Attempt(
            attempt_id="unreadable",
            task_outcome=success,
            confidence=UNREADABLE,
            **completed,
        ),
        Attempt(
            attempt_id="also out of range",
            task_outcome=success,
            confidence=ConfidenceUnavailable(reason="the rating is outside 0 to 100", original_response="-1"),
            **completed,
        ),
        Attempt(attempt_id="not asked", task_outcome=success, **completed),
        Attempt(
            attempt_id="no outcome",
            confidence=ConfidenceRating(rating="90", original_response="90"),
            **completed,
        ),
        Attempt(attempt_id="failed", task_id="task", execution_status=ExecutionStatus.FAILED),
    )

    assert result.value == 0.84
    assert result.counts["confidence_outcome_pairs"].to_dict() == {
        "total": 8,
        "valid": 2,
        "missing": [
            {"reason": "no task outcome recorded", "count": 1, "ids": ["no outcome"]},
            {"reason": "no confidence rating recorded", "count": 1, "ids": ["not asked"]},
            {
                "reason": "confidence rating unavailable: the rating is outside 0 to 100",
                "count": 2,
                "ids": ["out of range", "also out of range"],
            },
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
    assert result.attempt_ids == ("rated", "also rated")


def test_one_unavailable_rating_does_not_make_the_metric_unavailable():
    # The three remaining pairs: 0.16 + 0.16 + 0.04 = 0.36. Mean 0.12, score 0.88.
    result = evaluate(
        Attempt(
            attempt_id="a",
            task_id="task",
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=True, source=SOURCE),
            confidence=ConfidenceUnavailable(reason="the rating is outside 0 to 100", original_response="110"),
        ),
        attempt("b", "60", 1),
        attempt("c", "40", 0),
        attempt("d", "20", 0),
    )

    assert result.state.value == "available"
    assert result.value == 0.88


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
    assert result.details == {"mean_squared_error": None, "pairs": []}
    assert result.attempt_ids == ()


def test_no_attempts_is_an_unavailable_measurement():
    result = brier_based_score.evaluate(())

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no valid confidence/outcome pair"
    assert result.counts["confidence_outcome_pairs"].to_dict() == {
        "total": 0,
        "valid": 0,
        "missing": [],
        "excluded": [],
    }


def test_a_rating_is_paired_with_the_outcome_of_its_own_attempt_whatever_the_order_of_the_evidence():
    # a: (1.0 - 1)^2 = 0. b: (0.0 - 0)^2 = 0. Score 1 in any order.
    # Pairing a's rating with b's outcome and b's with a's would give squared errors of 1 and a score of 0.
    forwards = evaluate(attempt("a", "100", 1), attempt("b", "0", 0))
    backwards = evaluate(attempt("b", "0", 0), attempt("a", "100", 1))

    assert forwards.value == backwards.value == 1
    pairs = [(pair["attempt_id"], pair["rating"], pair["task_outcome"]["success"]) for pair in backwards.details["pairs"]]
    assert pairs == [("b", "0", False), ("a", "100", True)]


def test_the_worked_example_of_the_documentation():
    # close:   (0.95 - 1)^2 = 0.0025, (0.9 - 1)^2 = 0.01, (0.8 - 0)^2 = 0.64.
    # archive: (0.625 - 1)^2 = 0.140625, (0.3 - 0)^2 = 0.09, and one answer that is not a rating.
    # Sum 0.883125 over five pairs: mean 0.176625, score 0.823375.
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

    assert result.value == 0.823375
    assert result.details["mean_squared_error"] == 0.176625
    assert [pair["squared_error"] for pair in result.details["pairs"]] == [0.0025, 0.01, 0.64, 0.140625, 0.09]
    pairs = result.counts["confidence_outcome_pairs"]
    assert (pairs.total, pairs.valid) == (6, 5)


def test_two_attempts_with_the_same_identity_are_rejected_rather_than_paired_arbitrarily():
    with pytest.raises(EvidenceError, match="more than one Attempt has the identity 'a'"):
        evaluate(attempt("a", "100", 1), attempt("a", "0", 0))


@pytest.mark.parametrize("rating", ["100.01", "110", "-1", "-0.5"])
def test_a_rating_outside_0_to_100_is_rejected_and_never_clamped(rating):
    with pytest.raises(EvidenceError, match="outside 0 to 100"):
        ConfidenceRating(rating=rating, original_response=rating)


@pytest.mark.parametrize("rating", ["", "high", "85%", " 85", "8 5", "85,5", "1e2", "85.", "eighty", "٨٥"])
def test_a_rating_that_is_not_one_plain_number_is_rejected(rating):
    with pytest.raises(EvidenceError, match="one number"):
        ConfidenceRating(rating=rating, original_response=rating)


def test_a_rating_too_long_to_be_a_rating_is_rejected():
    with pytest.raises(EvidenceError, match="longer than 100 characters"):
        ConfidenceRating(rating="50." + "0" * 98, original_response="50")

    assert ConfidenceRating(rating="50." + "0" * 97, original_response="50").normalized == 0.5


@pytest.mark.parametrize("response", [None, 85, b"85"])
def test_a_confidence_response_that_is_not_text_is_rejected(response):
    with pytest.raises(EvidenceError, match="text"):
        read_confidence_response(response)


@pytest.mark.parametrize("rating", [85, 85.5, None, True])
def test_a_rating_that_is_not_given_as_written_is_rejected(rating):
    # A number has already lost the precision it was written at: 87.50 and 87.5 are the same float.
    with pytest.raises(EvidenceError, match="as the agent wrote it"):
        ConfidenceRating(rating=rating, original_response="85")


def test_a_rating_keeps_the_precision_it_was_written_at():
    rating = ConfidenceRating(rating="087.500", original_response="087.500")

    assert rating.to_dict() == {
        "rating": "087.500",
        "normalized": 0.875,
        "original_response": "087.500",
        "unavailable_reason": None,
        "invocation": None,
    }


def test_an_unavailable_rating_carries_its_reason_and_no_number():
    with pytest.raises(EvidenceError, match="reason"):
        ConfidenceUnavailable(reason="")

    assert UNREADABLE.to_dict() == {
        "rating": None,
        "normalized": None,
        "original_response": "High.",
        "unavailable_reason": "no number in the confidence response",
        "invocation": None,
    }


@pytest.mark.parametrize("confidence", [0.8, "80", {"rating": "80"}])
def test_confidence_evidence_that_is_neither_a_rating_nor_a_reason_is_rejected(confidence):
    with pytest.raises(EvidenceError, match="confidence"):
        Attempt(
            attempt_id="a",
            task_id="task",
            execution_status=ExecutionStatus.COMPLETED,
            confidence=confidence,
        )

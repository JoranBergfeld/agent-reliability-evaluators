"""Evaluator sweep seam: calibration and strict confidence discrimination, next to the Brier-based score.

The sweep is driven through its public entry point with a fake agent target. Every
assertion is on the machine-readable scorecard. Expected values are calculated by hand;
the conventions behind them are in `tests/evaluators/`.
"""

import json

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.scorecard import AcceptanceCriterion, to_json
from reliability_evaluators.sweep import Task, run_sweep
from tests.fakes import FakeAgentTarget

SOURCE = "final state of the fake service"
RULE = "The item is marked done in the to-do service."

BRIER = "brier_based_score"
CALIBRATION = "calibration"
DISCRIMINATION = "strict_confidence_discrimination"
ALL_THREE = (CALIBRATION, DISCRIMINATION, BRIER)


def sweep(script, confidence, metrics=ALL_THREE, **options):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour.

    `confidence` gives the fake agent's answer to the confidence request of each Attempt,
    by Attempt identity.
    """
    target = FakeAgentTarget(script, confidence=confidence)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(
            task_id=task_id,
            prompt=f"Do {task_id}.",
            success_rule=item_is_done,
            shareable_success_rule=RULE,
            attempts=len(behaviours),
        )
        for task_id, behaviours in script.items()
    ]
    return run_sweep(tasks=tasks, agent_target=target, metrics=metrics, **options)


def metric(scorecard, metric_id):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


# The worked example of the three predictability documents: six Attempts, five valid pairs.
#   Brier-based score: squared errors 0.0025, 0.01, 0.64, 0.140625 and 0.09. Mean 0.176625, score 0.823375.
#   Calibration: [0.9, 1] holds 0.95 and 0.9, both successes, gap 0.075. [0.8, 0.9) holds 0.8, a failure,
#     gap 0.8. [0.6, 0.7) holds 0.625, a success, gap 0.375. [0.3, 0.4) holds 0.3, a failure, gap 0.3.
#     Expected calibration error (2 * 0.075 + 0.8 + 0.375 + 0.3) / 5 = 0.325. Calibration 0.675.
#   Strict confidence discrimination: successes 95, 90, 62.5 against failures 80, 30. Only 62.5 < 80 is
#     not strictly higher: 5 / 6.
DOCUMENTED = {"close": ["success", "success", "failure"], "archive": ["success", "failure", "success"]}
DOCUMENTED_ANSWERS = {
    "close/attempt-1": "95",
    "close/attempt-2": "90",
    "close/attempt-3": "80",
    "archive/attempt-1": "62.5",
    "archive/attempt-2": "30",
    "archive/attempt-3": "Fairly sure.",
}
RATED = ["close/attempt-1", "close/attempt-2", "close/attempt-3", "archive/attempt-1", "archive/attempt-2"]


def test_one_sweep_reports_the_three_predictability_metrics_as_separate_results_from_the_same_attempts():
    scorecard = sweep(DOCUMENTED, DOCUMENTED_ANSWERS)

    predictability = [entry for entry in scorecard["metrics"] if entry["dimension"] == "predictability"]
    assert [(entry["metric_id"], entry["state"], entry["value"]) for entry in predictability] == [
        (CALIBRATION, "available", 0.675),
        (DISCRIMINATION, "available", 5 / 6),
        (BRIER, "available", 0.823375),
    ]
    for entry in predictability:
        assert entry["scoring_path"] == "customer_process"
        assert entry["fidelity"] == "authoritative"
        assert entry["evidence"] == {"attempt_ids": RATED}
        assert entry["counts"]["confidence_outcome_pairs"] == {
            "total": 6,
            "valid": 5,
            "missing": [
                {
                    "reason": "confidence rating unavailable: no number in the confidence response",
                    "count": 1,
                    "ids": ["archive/attempt-3"],
                },
            ],
            "excluded": [],
        }


def test_the_agent_is_asked_once_per_attempt_however_many_metrics_use_its_rating():
    # The fake agent answers with the number of confidence requests it has had for the Attempt.
    asked = {}

    def times_asked(request, target):
        asked[request.attempt.attempt_id] = asked.get(request.attempt.attempt_id, 0) + 1
        return str(asked[request.attempt.attempt_id])

    scorecard = sweep(
        {"close": ["success", "failure"]},
        {"close/attempt-1": times_asked, "close/attempt-2": times_asked},
    )

    assert [entry["confidence"]["rating"] for entry in scorecard["attempts"]] == ["1", "1"]
    assert [metric(scorecard, metric_id)["state"] for metric_id in ALL_THREE] == ["available"] * 3


@pytest.mark.parametrize(("selected", "value"), [(CALIBRATION, 0.8), (DISCRIMINATION, 1)])
def test_either_metric_selected_on_its_own_has_the_agent_asked_for_its_confidence(selected, value):
    # Calibration: 0.8 on a success and 0.2 on a failure are each alone in a bin, both with a gap of 0.2.
    # Strict confidence discrimination: the one success/failure pair has 80 > 20.
    scorecard = sweep({"close": ["success", "failure"]}, {"close/attempt-1": "80", "close/attempt-2": "20"}, (selected,))

    assert [entry["confidence"]["rating"] for entry in scorecard["attempts"]] == ["80", "20"]
    assert metric(scorecard, selected)["state"] == "available"
    assert metric(scorecard, selected)["value"] == value
    assert [metric(scorecard, other)["state"] for other in ALL_THREE if other != selected] == ["not_configured"] * 2


def test_the_bin_boundaries_and_counts_are_in_the_scorecard_alongside_every_raw_pair():
    # 0.875 on a success is alone in [0.8, 0.9), bin 9, and 0.125 on a failure in [0.1, 0.2), bin 2.
    # Both gaps are 0.125: expected calibration error 0.125, calibration 0.875.
    scorecard = sweep(
        {"close": ["success", "failure"]}, {"close/attempt-1": "87.50", "close/attempt-2": "12.5"}, (CALIBRATION,)
    )

    calibration = metric(scorecard, CALIBRATION)
    assert calibration["value"] == 0.875
    assert calibration["details"]["expected_calibration_error"] == 0.125
    bins = calibration["details"]["bins"]
    assert [(entry["bin"], entry["lower_bound"], entry["upper_bound"]) for entry in bins] == [
        (1, 0.0, 0.1),
        (2, 0.1, 0.2),
        (3, 0.2, 0.3),
        (4, 0.3, 0.4),
        (5, 0.4, 0.5),
        (6, 0.5, 0.6),
        (7, 0.6, 0.7),
        (8, 0.7, 0.8),
        (9, 0.8, 0.9),
        (10, 0.9, 1.0),
    ]
    assert [entry["includes_upper_bound"] for entry in bins] == [False] * 9 + [True]
    assert [entry["count"] for entry in bins] == [0, 1, 0, 0, 0, 0, 0, 0, 1, 0]
    assert calibration["details"]["pairs"] == [
        {
            "attempt_id": "close/attempt-1",
            "task_id": "close",
            "rating": "87.50",
            "confidence": 0.875,
            "task_outcome": {"success": True, "source": SOURCE},
            "bin": 9,
        },
        {
            "attempt_id": "close/attempt-2",
            "task_id": "close",
            "rating": "12.5",
            "confidence": 0.125,
            "task_outcome": {"success": False, "source": SOURCE},
            "bin": 2,
        },
    ]
    assert json.loads(to_json(scorecard)) == scorecard


ALL_TIED = {"close": ["success", "success", "failure", "failure"]}
ALL_TIED_ANSWERS = {f"close/attempt-{number}": "50" for number in (1, 2, 3, 4)}


def test_all_ratings_tied_scores_0_for_strict_discrimination_with_the_tied_pairs_counted():
    # Two successes and two failures, all rated 50. The three metrics say three different things:
    #   Strict confidence discrimination: four success/failure pairs, all tied: 0, and not 0.5.
    #   Calibration: all four pairs in [0.5, 0.6), mean confidence 0.5, mean outcome 1/2, gap 0: 1.
    #   Brier-based score: every squared error is 0.25: 0.75.
    scorecard = sweep(ALL_TIED, ALL_TIED_ANSWERS)

    discrimination = metric(scorecard, DISCRIMINATION)
    assert discrimination["state"] == "available"
    assert discrimination["value"] == 0
    assert discrimination["details"]["success_failure_pairs"] == {
        "total": 4,
        "success_rated_higher": 0,
        "tied": 4,
        "failure_rated_higher": 0,
    }
    assert discrimination["counts"]["success_failure_pairs"] == {
        "total": 4,
        "valid": 4,
        "missing": [],
        "excluded": [],
    }
    assert [pair["rating"] for pair in discrimination["details"]["pairs"]] == ["50", "50", "50", "50"]
    assert metric(scorecard, CALIBRATION)["value"] == 1
    assert metric(scorecard, BRIER)["value"] == 0.75


def test_strict_discrimination_is_labelled_strict_and_set_apart_from_conventional_auroc():
    discrimination = metric(sweep(ALL_TIED, ALL_TIED_ANSWERS), DISCRIMINATION)

    assert discrimination["name"] == "Strict confidence discrimination"
    conventions = " ".join(discrimination["definition"]["fidelity_conventions"])
    assert "not the conventional AUROC" in conventions
    assert "Half credit is deliberately not given" in conventions


def test_without_a_failure_strict_discrimination_is_unavailable_while_the_other_two_keep_their_values():
    # Two successes rated 90 and 70, no failure: there is no success/failure pair.
    #   Calibration: each alone in a bin, gaps 0.1 and 0.3: expected calibration error 0.2, value 0.8.
    #   Brier-based score: squared errors 0.01 and 0.09: mean 0.05, score 0.95.
    scorecard = sweep({"close": ["success", "success"]}, {"close/attempt-1": "90", "close/attempt-2": "70"})

    discrimination = metric(scorecard, DISCRIMINATION)
    assert discrimination["state"] == "unavailable"
    assert discrimination["value"] is None
    assert discrimination["reason"] == (
        "no failure among the valid confidence/outcome pairs, so there is no success/failure pair"
    )
    assert discrimination["counts"] == {
        "confidence_outcome_pairs": {"total": 2, "valid": 2, "missing": [], "excluded": []},
        "success_failure_pairs": {"total": 0, "valid": 0, "missing": [], "excluded": []},
    }
    assert discrimination["evidence"] == {"attempt_ids": []}
    assert metric(scorecard, CALIBRATION)["value"] == 0.8
    assert metric(scorecard, BRIER)["value"] == 0.95


def test_without_a_readable_rating_calibration_and_strict_discrimination_are_unavailable_with_their_counts():
    scorecard = sweep(
        {"close": ["success", "failure"]},
        {"close/attempt-1": "Quite confident.", "close/attempt-2": "150"},
        (CALIBRATION, DISCRIMINATION),
    )

    unreadable = [
        {
            "reason": "confidence rating unavailable: no number in the confidence response",
            "count": 1,
            "ids": ["close/attempt-1"],
        },
        {
            "reason": "confidence rating unavailable: the rating is outside 0 to 100",
            "count": 1,
            "ids": ["close/attempt-2"],
        },
    ]
    for metric_id in (CALIBRATION, DISCRIMINATION):
        entry = metric(scorecard, metric_id)
        assert entry["state"] == "unavailable"
        assert entry["value"] is None
        assert entry["reason"] == "no valid confidence/outcome pair"
        assert entry["counts"]["confidence_outcome_pairs"] == {
            "total": 2,
            "valid": 0,
            "missing": unreadable,
            "excluded": [],
        }
    assert [entry["count"] for entry in metric(scorecard, CALIBRATION)["details"]["bins"]] == [0] * 10


def test_an_acceptance_criterion_can_require_a_number_of_success_failure_pairs():
    # The documented example has 3 successes and 2 failures with a rating: 6 success/failure pairs, value 5 / 6.
    scorecard = sweep(
        DOCUMENTED,
        DOCUMENTED_ANSWERS,
        (DISCRIMINATION,),
        acceptance_criteria=[
            AcceptanceCriterion(
                metric_id=DISCRIMINATION,
                condition={"value": {"at_least": 0.8}, "minimum_counts": {"success_failure_pairs": 7}},
            )
        ],
    )

    acceptance = metric(scorecard, DISCRIMINATION)["acceptance"]
    assert acceptance["status"] == "not_met"
    assert [(check["subject"], check["observed"], check["met"]) for check in acceptance["checks"]] == [
        ("value", 5 / 6, True),
        ("counts.success_failure_pairs.valid", 6, False),
    ]

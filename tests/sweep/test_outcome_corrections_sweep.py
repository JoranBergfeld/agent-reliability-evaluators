"""Evaluator sweep seam: task outcomes supplied or corrected after the run, and the recalculation.

A sweep is driven through its public entry point with a fake agent target. Its scorecard
is then read back from JSON, as a customer supplies it from their platform, and
recalculated through the public entry point. Every assertion is on a machine-readable
scorecard or on the reported failure. Expected values are calculated by hand.
"""

import json

from reliability_evaluators.evidence import OutcomeCorrection, TaskOutcome
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import Task, recalculate, run_sweep
from tests.fakes import FakeAgentTarget

SOURCE = "final state of the fake service"
RULE = "The item is marked done in the to-do service."
REVIEW = "manual review of the service audit log"
PROVENANCE = "J. Jansen on 2026-11-27: the check ran before the service had saved the item"


def supplied(scorecard):
    """The scorecard as a customer supplies it later: read back from the JSON it was kept as."""
    return json.loads(to_json(scorecard))


def sweep(script, metrics=("outcome_consistency",), ratings=None):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour.

    `ratings` gives the fake agent's answer to the confidence request of each Attempt, by
    Attempt identity.
    """
    target = FakeAgentTarget(script, confidence=ratings)

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
    return supplied(run_sweep(tasks=tasks, agent_target=target, metrics=metrics))


def corrected(attempt_id, success, source=REVIEW, provenance=PROVENANCE):
    return OutcomeCorrection(
        attempt_id=attempt_id, task_outcome=TaskOutcome(success=success, source=source), provenance=provenance
    )


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def attempt(scorecard, attempt_id):
    (entry,) = [entry for entry in scorecard["attempts"] if entry["attempt_id"] == attempt_id]
    return entry


HALF = ["success", "success", "failure", "failure"]
RATINGS = {"close/attempt-1": "75", "close/attempt-2": "75", "close/attempt-3": "75", "close/attempt-4": "25"}


def test_recalculating_with_a_corrected_task_outcome_gives_a_new_scorecard_that_uses_it():
    # Earlier: p = 2/4, (2 * 0.5 - 1)^2 = 0. Corrected: p = 3/4, (2 * 0.75 - 1)^2 = 0.25.
    earlier = sweep({"close": HALF})
    assert metric(earlier)["value"] == 0

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    outcome_consistency = metric(recalculated)
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == 0.25
    assert outcome_consistency["details"]["per_task"] == [
        {"task_id": "close", "attempts": 4, "successes": 3, "value": 0.25}
    ]


def test_the_corrected_outcome_is_used_in_every_metric_that_is_calculated_from_task_outcomes():
    # Brier-based score, earlier: squared errors 0.0625, 0.0625, (0.75 - 0)^2 = 0.5625 and 0.0625.
    # Mean 0.75 / 4 = 0.1875, score 0.8125.
    # Corrected: the third becomes (0.75 - 1)^2 = 0.0625. Mean 0.25 / 4 = 0.0625, score 0.9375.
    earlier = sweep({"close": HALF}, metrics=["outcome_consistency", "brier_based_score"], ratings=RATINGS)
    assert metric(earlier, "brier_based_score")["value"] == 0.8125

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    brier = metric(recalculated, "brier_based_score")
    assert brier["state"] == "available"
    assert brier["value"] == 0.9375
    assert brier["details"]["mean_squared_error"] == 0.0625
    assert brier["details"]["pairs"][2] == {
        "attempt_id": "close/attempt-3",
        "task_id": "close",
        "rating": "75",
        "confidence": 0.75,
        "task_outcome": {"success": True, "source": REVIEW},
        "squared_error": 0.0625,
    }
    assert brier["counts"] == {"confidence_outcome_pairs": {"total": 4, "valid": 4, "missing": [], "excluded": []}}
    assert metric(recalculated, "outcome_consistency")["value"] == 0.25


def test_the_attempt_carries_the_corrected_outcome_and_retains_the_record_it_replaced_with_the_provenance():
    earlier = sweep({"close": HALF})

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    third = attempt(recalculated, "close/attempt-3")
    assert third["task_outcome"] == {"success": True, "source": REVIEW}
    assert third["superseded_task_outcomes"] == [
        {"task_outcome": {"success": False, "source": SOURCE}, "correction_provenance": PROVENANCE}
    ]
    assert attempt(recalculated, "close/attempt-4")["task_outcome"] == {"success": False, "source": SOURCE}
    assert attempt(recalculated, "close/attempt-4")["superseded_task_outcomes"] == []
    assert attempt(earlier, "close/attempt-3")["superseded_task_outcomes"] == []

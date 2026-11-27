"""Evaluator sweep seam: token usage per Attempt, per Evaluator and for the sweep.

The sweep is driven through its public entry point with a fake agent target. Every assertion
is on the machine-readable scorecard. Expected totals are added by hand.
"""

import json

import pytest

from reliability_evaluators.evidence import OutcomeCorrection, TaskOutcome
from reliability_evaluators.scorecard import AcceptanceCriterion, to_json
from reliability_evaluators.sweep import ExecutionLimits, RecalculationFailed, Task, recalculate, run_sweep
from tests.fakes import FakeAgentTarget

SOURCE = "final state of the fake service"
OUTCOME = "outcome_consistency"


def reported(total_tokens, response_id=None):
    """What an agent target records of an invocation whose usage the platform reported."""
    return {
        "response_id": response_id,
        "token_usage": {"input_tokens": total_tokens - 90, "output_tokens": 90, "total_tokens": total_tokens},
        "token_usage_missing": None,
    }


def sweep(script, metrics=(OUTCOME,), limits=ExecutionLimits(), acceptance_criteria=(), **scripted):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = FakeAgentTarget(script, **scripted)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(
            task_id=task_id,
            prompt=f"Do {task_id}.",
            success_rule=item_is_done,
            attempts=len(behaviours),
            limits=limits,
            shareable_success_rule="The item is done.",
        )
        for task_id, behaviours in script.items()
    ]
    return run_sweep(
        tasks=tasks,
        agent_target=target,
        metrics=metrics,
        acceptance_criteria=acceptance_criteria,
        cancellation=lambda: target.cancel_requested,
        clock=target.clock,
    )


def per_attempt(usage):
    return [
        (entry["attempt_id"], entry["execution_status"], entry["coverage"], entry["total_tokens"])
        for entry in usage["attempts"]
    ]


def supplied(scorecard):
    """The scorecard as a customer supplies it later: read back from the JSON it was kept as."""
    return json.loads(to_json(scorecard))


def metric(scorecard, metric_id=OUTCOME):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def test_the_scorecard_reports_the_usage_of_each_attempt_and_of_the_sweep_in_tokens():
    # 790 + 812 = 1602 tokens.
    scorecard = sweep(
        {"close": ["success", "success"]},
        invocations={"close/attempt-1": reported(790, "resp_1"), "close/attempt-2": reported(812, "resp_2")},
    )

    usage = scorecard["token_usage"]
    assert usage["unit"] == "tokens"
    assert per_attempt(usage) == [
        ("close/attempt-1", "completed", "complete", 790),
        ("close/attempt-2", "completed", "complete", 812),
    ]
    assert usage["attempts"][0]["invocation"] == {
        "basis": "reported",
        "total_tokens": 790,
        "reason": None,
        "response_id": "resp_1",
        "request_ids": {},
    }
    assert usage["sweep"] == {
        "coverage": "complete",
        "total_tokens": 1602,
        "uses_reported": 2,
        "uses_missing": 0,
        "missing": [],
    }


def test_usage_is_not_a_metric_and_changes_no_metric_result_and_no_acceptance_assessment():
    script = {"close": ["success", "failure", "success"]}
    criteria = [
        AcceptanceCriterion(metric_id=OUTCOME, condition={"value": {"at_least": 0.5}}),
        AcceptanceCriterion(metric_id="resource_consistency", condition={"value": {"at_least": 0.5}}),
    ]
    metrics = (OUTCOME, "resource_consistency")

    without_usage = sweep(script, metrics, acceptance_criteria=criteria)
    with_usage = sweep(
        script,
        metrics,
        acceptance_criteria=criteria,
        invocations={f"close/attempt-{number}": reported(1000 * number) for number in (1, 2, 3)},
    )

    assert with_usage["token_usage"]["sweep"]["total_tokens"] == 6000
    assert without_usage["token_usage"]["sweep"]["total_tokens"] is None
    # Resource consistency is calculated from resource measurements. Reported usage is not one of them.
    assert with_usage["metrics"] == without_usage["metrics"]
    # Twelve metrics, as before, and usage is in none of them and in no acceptance assessment.
    assert len(with_usage["metrics"]) == 12
    assert "token" not in " ".join(key for entry in with_usage["metrics"] for key in entry)
    # Outcome consistency: p = 2/3, so (2 * 2/3 - 1)^2 = 1/9, which is below 0.5 with usage and without.
    assert with_usage["metrics"][0]["acceptance"]["status"] == "not_met"


def test_a_recalculation_keeps_the_usage_of_the_sweep_whose_evidence_it_scores_again():
    earlier = supplied(
        sweep(
            {"close": ["success", "failure"]},
            invocations={"close/attempt-1": reported(790), "close/attempt-2": reported(812)},
        )
    )
    correction = OutcomeCorrection(
        attempt_id="close/attempt-2",
        task_outcome=TaskOutcome(success=True, source="manual review"),
        provenance="J. Jansen: the check ran too early",
    )

    recalculated = recalculate(earlier_scorecard=earlier, outcome_corrections=[correction])

    # The metric changes from (2 * 1/2 - 1)^2 = 0 to (2 * 1 - 1)^2 = 1. No Attempt was made for that,
    # so what the sweep used is as it was: 790 + 812 = 1602 tokens.
    assert (metric(earlier)["value"], metric(recalculated)["value"]) == (0, 1)
    assert recalculated["token_usage"]["sweep"]["total_tokens"] == 1602
    assert recalculated["token_usage"] == earlier["token_usage"]


def test_usage_that_the_supplied_evidence_does_not_give_fails_the_recalculation():
    earlier = supplied(sweep({"close": ["success", "failure"]}, invocations={"close/attempt-1": reported(790)}))
    earlier["token_usage"]["sweep"]["total_tokens"] = 5
    correction = OutcomeCorrection(
        attempt_id="close/attempt-2",
        task_outcome=TaskOutcome(success=True, source="manual review"),
        provenance="J. Jansen: the check ran too early",
    )

    with pytest.raises(RecalculationFailed, match="does not reproduce the earlier 'token_usage'"):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[correction])

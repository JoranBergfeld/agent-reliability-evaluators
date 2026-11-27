"""Evaluator sweep seam: token usage per Attempt, per Evaluator and for the sweep.

The sweep is driven through its public entry point with a fake agent target. Every assertion
is on the machine-readable scorecard. Expected totals are added by hand.
"""

import json

import pytest

from reliability_evaluators.agent_target import ConfidenceResponse, InvocationFailed
from reliability_evaluators.evidence import ActionEvent, OutcomeCorrection, TaskOutcome
from reliability_evaluators.scorecard import AcceptanceCriterion, to_json
from reliability_evaluators.sweep import (
    ActionEvidenceDeclaration,
    ConfiguredEvaluator,
    ExecutionLimits,
    RecalculationFailed,
    Task,
    recalculate,
    run_sweep,
)
from tests.fakes import FakeActingAgentTarget, FakeAgentTarget, cancelling, taking

SOURCE = "final state of the fake service"
OUTCOME = "outcome_consistency"


def reported(total_tokens, response_id=None):
    """What an agent target records of an invocation whose usage the platform reported."""
    return {
        "response_id": response_id,
        "token_usage": {"input_tokens": total_tokens - 90, "output_tokens": 90, "total_tokens": total_tokens},
        "token_usage_missing": None,
    }


def sweep(script, metrics=(OUTCOME,), limits=ExecutionLimits(), acceptance_criteria=(), attempts=None, **scripted):
    """Run a sweep with one Task per script entry, and one Attempt per scripted behaviour unless `attempts` says."""
    target = FakeAgentTarget(script, **scripted)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(
            task_id=task_id,
            prompt=f"Do {task_id}.",
            success_rule=item_is_done,
            attempts=attempts or len(behaviours),
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


def per_evaluator(usage):
    return {
        entry["metric_id"]: (entry["total"]["coverage"], entry["total"]["total_tokens"], entry["overlapping"], entry["additive"])
        for entry in usage["evaluators"]
    }


def test_attempts_that_two_evaluators_share_are_counted_once_in_the_sweep_and_their_totals_are_labelled_overlapping():
    # Both Evaluators are scored from the same two Attempts: 790 + 812 = 1602 tokens each, and 1602
    # for the sweep. Adding the two Evaluator totals would give 3204, which nobody used.
    scorecard = sweep(
        {"close": ["success", "success"]},
        metrics=(OUTCOME, "resource_consistency"),
        invocations={"close/attempt-1": reported(790), "close/attempt-2": reported(812)},
    )

    usage = scorecard["token_usage"]
    assert usage["sweep"]["total_tokens"] == 1602
    assert per_evaluator(usage) == {
        OUTCOME: ("complete", 1602, True, False),
        "resource_consistency": ("complete", 1602, True, False),
    }
    outcome, resource = usage["evaluators"]
    assert outcome["attempt_ids"] == ["close/attempt-1", "close/attempt-2"]
    assert (outcome["overlaps_with"], resource["overlaps_with"]) == (["resource_consistency"], [OUTCOME])
    assert [entry["used_by"] for entry in usage["attempts"]] == [[OUTCOME, "resource_consistency"]] * 2
    # Scoring by code in this process uses no model: that zero is known.
    assert outcome["scoring"] == {
        "basis": "no_model_use",
        "total_tokens": 0,
        "reason": "the Evaluator is calculated by code in the customer's process, which uses no model",
        "scoring_path": "customer_process",
    }


def test_separate_attempts_made_for_an_evaluator_are_its_own_and_every_attempt_is_in_the_sweep_total():
    # The sequential trajectory Evaluator reads another recording, so the Task is attempted again for it.
    #   first set, shared by outcome and distributional trajectory consistency: 500 + 520 = 1020 tokens
    #   second set, for sequential trajectory consistency alone:               610 + 630 = 1240 tokens
    #   sweep: 1020 + 1240 = 2260 tokens
    calls = ["get_item", "update_item"]
    target = FakeActingAgentTarget(
        {"close": ["success"] * 4},
        {"close": [calls] * 4},
        invocations={
            "close/attempt-1": reported(500),
            "close/attempt-2": reported(520),
            "close/set-2/attempt-1": reported(610),
            "close/set-2/attempt-2": reported(630),
        },
    )

    def recorded_calls(attempt, response):
        return [ActionEvent(**call) for call in target.service_log[attempt.attempt_id]]

    def recording(evidence_source):
        return ActionEvidenceDeclaration(
            evidence_source=evidence_source,
            record=recorded_calls,
            action_types={"get_item": "read", "update_item": "update"},
        )

    scorecard = run_sweep(
        tasks=[
            Task(
                task_id="close",
                prompt="Close the item.",
                success_rule=lambda attempt, response: TaskOutcome(success=True, source=SOURCE),
                attempts=2,
            )
        ],
        agent_target=target,
        metrics=[
            OUTCOME,
            "distributional_trajectory_consistency",
            ConfiguredEvaluator(
                metric_id="sequential_trajectory_consistency", action_evidence=recording("service-side effects")
            ),
        ],
        action_evidence=recording("native tool calls"),
    )

    usage = scorecard["token_usage"]
    assert usage["sweep"]["total_tokens"] == 2260
    assert per_evaluator(usage) == {
        OUTCOME: ("complete", 1020, True, False),
        "distributional_trajectory_consistency": ("complete", 1020, True, False),
        "sequential_trajectory_consistency": ("complete", 1240, False, True),
    }
    assert [entry["overlaps_with"] for entry in usage["evaluators"]] == [
        ["distributional_trajectory_consistency"],
        [OUTCOME],
        [],
    ]


def test_the_usage_of_a_confidence_follow_up_is_reported_with_its_attempt_when_the_agent_target_reports_it():
    # close/attempt-1: 790 for the Attempt and 120 for its follow-up, 910 tokens.
    # close/attempt-2: 812 for the Attempt. Its follow-up was sent and reported no usage: 812 is partial.
    # close/attempt-3: the invocation failed, so no follow-up was sent.
    # Sweep: 790 + 120 + 812 = 1722 tokens, a partial sum.
    scorecard = sweep(
        {"close": ["success", "success", "error"]},
        metrics=("brier_based_score",),
        invocations={"close/attempt-1": reported(790, "resp_1"), "close/attempt-2": reported(812, "resp_2")},
        confidence={
            "close/attempt-1": ConfidenceResponse(
                attempt_id="close/attempt-1", output="80", invocation=reported(120, "resp_1_confidence")
            ),
            "close/attempt-2": "70",
        },
    )

    usage = scorecard["token_usage"]
    first, second, third = usage["attempts"]
    assert first["confidence_follow_up"] == {
        "basis": "reported",
        "total_tokens": 120,
        "reason": None,
        "response_id": "resp_1_confidence",
        "request_ids": {},
    }
    assert second["confidence_follow_up"] == {
        "basis": "missing",
        "total_tokens": None,
        "reason": "the agent target recorded nothing of the confidence request",
        "response_id": None,
        "request_ids": {},
    }
    assert third["confidence_follow_up"] is None
    assert per_attempt(usage) == [
        ("close/attempt-1", "completed", "complete", 910),
        ("close/attempt-2", "completed", "partial", 812),
        ("close/attempt-3", "failed", "unavailable", None),
    ]
    assert usage["sweep"] == {
        "coverage": "partial",
        "total_tokens": 1722,
        "uses_reported": 3,
        "uses_missing": 2,
        "missing": [
            {
                "use": "confidence_follow_up",
                "attempt_id": "close/attempt-2",
                "reason": "the agent target recorded nothing of the confidence request",
            },
            {
                "use": "agent_invocation",
                "attempt_id": "close/attempt-3",
                "reason": "the agent target recorded nothing of the invocation",
            },
        ],
    }
    # The rating is read as before: what the follow-up used is no part of the confidence evidence.
    assert scorecard["attempts"][0]["confidence"]["rating"] == "80"


def test_failed_cancelled_and_retried_attempts_are_in_the_usage_with_their_statuses_and_what_they_used():
    # Three planned Attempts, each retried once at most.
    #   close/attempt-1          fails after it used 410 tokens, and is made again
    #   close/attempt-1/retry-1  fails too, and nothing was recorded of it
    #   close/attempt-2          answers after its time limit having used 812 tokens, and is made again
    #   close/attempt-2/retry-1  completes with 790 tokens
    #   close/attempt-3          is aborted when the sweep is cancelled, and nothing was recorded of it
    # Known: 410 + 812 + 790 = 2012 tokens, a partial sum. No Task has two Attempts with a task outcome,
    # so outcome consistency has no value, and what its Attempts used is reported all the same.
    scorecard = sweep(
        {"close": ["error", "error", taking(5.0, "success"), "success", cancelling("aborted")]},
        attempts=3,
        limits=ExecutionLimits(attempt_timeout_seconds=1.0, max_retries=1),
        honours_time_limits=False,
        invocations={"close/attempt-2": reported(812), "close/attempt-2/retry-1": reported(790)},
        invocation_errors={
            "close/attempt-1": InvocationFailed("the platform answered HTTP 500", invocation=reported(410))
        },
    )

    usage = scorecard["token_usage"]
    assert per_attempt(usage) == [
        ("close/attempt-1", "retried", "complete", 410),
        ("close/attempt-1/retry-1", "failed", "unavailable", None),
        ("close/attempt-2", "retried", "complete", 812),
        ("close/attempt-2/retry-1", "completed", "complete", 790),
        ("close/attempt-3", "cancelled", "unavailable", None),
    ]
    assert [entry["retry_of"] for entry in usage["attempts"]] == [
        None,
        "close/attempt-1",
        None,
        "close/attempt-2",
        None,
    ]
    assert (usage["sweep"]["coverage"], usage["sweep"]["total_tokens"]) == ("partial", 2012)
    assert [(missing["attempt_id"], missing["reason"]) for missing in usage["sweep"]["missing"]] == [
        ("close/attempt-1/retry-1", "the agent target recorded nothing of the invocation"),
        ("close/attempt-3", "the agent target recorded nothing of the invocation"),
    ]
    assert metric(scorecard)["value"] is None
    assert per_evaluator(usage) == {OUTCOME: ("partial", 2012, False, True)}


def follow_up_usage(confidence):
    """What the scorecard reports of the follow-up of the one Attempt of a sweep, given the agent's answer."""
    scorecard = sweep(
        {"close": ["success"]},
        metrics=("brier_based_score",),
        invocations={"close/attempt-1": reported(790)},
        confidence={"close/attempt-1": confidence},
    )
    (attempt,) = scorecard["token_usage"]["attempts"]
    return attempt


def test_the_usage_of_a_confidence_response_that_belongs_to_another_attempt_is_not_attributed():
    elsewhere = ConfidenceResponse(attempt_id="close/attempt-9", output="80", invocation=reported(120))

    attempt = follow_up_usage(elsewhere)

    assert attempt["confidence_follow_up"] == {
        "basis": "missing",
        "total_tokens": None,
        "reason": "the confidence response belongs to Attempt 'close/attempt-9', not to this Attempt",
        "response_id": None,
        "request_ids": {},
    }
    # The Attempt's own invocation is known, the request that was sent for it is not: 790 is partial.
    assert (attempt["coverage"], attempt["total_tokens"]) == ("partial", 790)


def test_a_confidence_request_that_fails_keeps_what_was_recorded_of_it():
    # The request was answered with an error after it used 60 tokens: 790 + 60 = 850.
    failure = InvocationFailed(
        "the platform answered HTTP 500",
        invocation={"request_ids": {"x-request-id": "req_9"}, "token_usage": {"total_tokens": 60}},
    )

    attempt = follow_up_usage(failure)

    assert attempt["confidence_follow_up"] == {
        "basis": "reported",
        "total_tokens": 60,
        "reason": None,
        "response_id": None,
        "request_ids": {"x-request-id": "req_9"},
    }
    assert (attempt["coverage"], attempt["total_tokens"]) == ("complete", 850)


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

"""Evaluator sweep seam: what the platform returned about each invocation stays on the Attempt.

The sweep is driven through its public entry point with a fake agent target. Every
assertion is on the machine-readable scorecard.
"""

import json

import pytest

from reliability_evaluators.agent_target import InvocationError
from reliability_evaluators.evidence import (
    ActionEvent,
    EvidenceError,
    OutcomeCorrection,
    PlatformEvidence,
    TaskOutcome,
)
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import ExecutionLimits, RecalculationFailed, Task, recalculate, run_sweep
from tests.fakes import FakeAgentTarget, attempt_evidence, taking

SOURCE = "final state of the fake service"
PLATFORM = "responses of the fake agent platform"


def sweep(script, **scripted):
    target = FakeAgentTarget(script, **scripted)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    return run_sweep(tasks=tasks, agent_target=target, metrics=["outcome_consistency"])


def test_an_attempt_keeps_the_identifiers_usage_and_tool_calls_the_platform_returned():
    returned = PlatformEvidence(
        source=PLATFORM,
        identifiers={"response_id": "resp_1", "agent": {"name": "todo", "version": "3"}},
        token_usage={"input_tokens": 700, "output_tokens": 90, "total_tokens": 790},
        tool_calls=[
            ActionEvent(tool_name="read", arguments='{"todo_id": "target"}', result='{"done": false}'),
            ActionEvent(tool_name="update", arguments='{"todo_id": "target", "done": true}', result='{"done": true}'),
        ],
    )

    scorecard = sweep({"close": ["success", "success"]}, platform_evidence={"close/attempt-1": returned})

    assert attempt_evidence(scorecard["attempts"][0], "platform_evidence") == {
        "attempt_id": "close/attempt-1",
        "task_id": "close",
        "execution_status": "completed",
        "execution_error": None,
        "task_outcome": {"success": True, "source": SOURCE},
        "platform_evidence": {
            "source": PLATFORM,
            "identifiers": {"response_id": "resp_1", "agent": {"name": "todo", "version": "3"}},
            "token_usage": {"input_tokens": 700, "output_tokens": 90, "total_tokens": 790},
            "token_usage_missing": None,
            "tool_calls": [
                {"tool_name": "read", "arguments": '{"todo_id": "target"}', "result": '{"done": false}', "error": None},
                {
                    "tool_name": "update",
                    "arguments": '{"todo_id": "target", "done": true}',
                    "result": '{"done": true}',
                    "error": None,
                },
            ],
        },
    }
    # The agent target returned nothing about the second invocation, and nothing is made up for it.
    assert scorecard["attempts"][1]["platform_evidence"] is None


def test_an_invocation_that_fails_keeps_what_the_platform_returned_and_is_not_a_task_failure():
    # Two successes and one failed invocation: p = 2/2 and the value is 1.
    # Scoring the failed invocation as a task failure would give (2 * 2/3 - 1)^2 = 0.111...
    rejected = InvocationError(
        "the platform answered HTTP 400 tool_user_error",
        platform_evidence=PlatformEvidence(
            source=PLATFORM,
            identifiers={"request_id": "req_7"},
            token_usage_missing="the platform returned an error and no usage",
        ),
    )

    scorecard = sweep({"close": ["success", "error", "success"]}, invocation_errors={"close/attempt-2": rejected})

    assert attempt_evidence(scorecard["attempts"][1], "platform_evidence") == {
        "attempt_id": "close/attempt-2",
        "task_id": "close",
        "execution_status": "failed",
        "execution_error": "InvocationError: the platform answered HTTP 400 tool_user_error",
        "task_outcome": None,
        "platform_evidence": {
            "source": PLATFORM,
            "identifiers": {"request_id": "req_7"},
            "token_usage": None,
            "token_usage_missing": "the platform returned an error and no usage",
            "tool_calls": None,
        },
    }
    (outcome_consistency,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == "outcome_consistency"]
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["counts"]["attempts"]["excluded"] == [
        {"reason": "execution status is not completed", "count": 1, "ids": ["close/attempt-2"]},
    ]


def test_an_answer_that_arrives_after_the_attempts_time_limit_keeps_what_the_platform_returned():
    # The agent target does not stop at the limit of 1 second and answers after 5. The Attempt
    # timed out and is not scored, and what the platform returned about it is still kept: it was spent.
    late = PlatformEvidence(
        source=PLATFORM, identifiers={"response_id": "resp_1"}, token_usage={"total_tokens": 790}
    )
    target = FakeAgentTarget(
        {"close": [taking(5.0, "success"), "success"]},
        platform_evidence={"close/attempt-1": late},
        honours_time_limits=False,
    )
    task = Task(
        task_id="close",
        prompt="Close it.",
        success_rule=lambda attempt, response: TaskOutcome(success=True, source=SOURCE),
        attempts=2,
        limits=ExecutionLimits(attempt_timeout_seconds=1.0),
    )

    scorecard = run_sweep(tasks=[task], agent_target=target, metrics=["outcome_consistency"], clock=target.clock)

    assert attempt_evidence(scorecard["attempts"][0], "platform_evidence") == {
        "attempt_id": "close/attempt-1",
        "task_id": "close",
        "execution_status": "timed_out",
        "execution_error": "the agent target answered after the Attempt's time limit of 1 seconds",
        "task_outcome": None,
        "platform_evidence": {
            "source": PLATFORM,
            "identifiers": {"response_id": "resp_1"},
            "token_usage": {"total_tokens": 790},
            "token_usage_missing": None,
            "tool_calls": None,
        },
    }


def test_an_invocation_that_fails_without_platform_evidence_records_none():
    scorecard = sweep({"close": ["success", "error"]})

    assert scorecard["attempts"][1]["execution_status"] == "failed"
    assert scorecard["attempts"][1]["platform_evidence"] is None


@pytest.mark.parametrize(
    ("recorded", "problem"),
    [
        ({"token_usage": None}, "says why it is missing"),
        ({"token_usage": None, "token_usage_missing": " "}, "says why it is missing"),
        ({"token_usage": {"total_tokens": 0}, "token_usage_missing": "not reported"}, "also said to be missing"),
        ({"token_usage": 790}, "as the platform reported it"),
        ({"token_usage_missing": "not reported", "tool_calls": ["read"]}, "ActionEvent"),
        ({"token_usage_missing": "not reported", "identifiers": "resp_1"}, "by name"),
        ({"token_usage_missing": "not reported", "source": ""}, "source"),
    ],
)
def test_platform_evidence_that_would_invent_or_lose_something_is_rejected(recorded, problem):
    with pytest.raises(EvidenceError, match=problem):
        PlatformEvidence(**{"source": PLATFORM, "identifiers": {"response_id": "resp_1"}, **recorded})


def test_a_response_whose_platform_evidence_is_not_platform_evidence_stops_the_sweep():
    with pytest.raises(EvidenceError, match="PlatformEvidence"):
        sweep({"close": ["success"]}, platform_evidence={"close/attempt-1": {"response_id": "resp_1"}})


def test_a_recalculation_keeps_what_the_platform_returned_about_every_attempt():
    # The earlier scorecard is supplied as the JSON it was kept as. Correcting one task outcome
    # changes p from 2/2 to 1/2, and leaves the platform evidence of every Attempt as it was.
    completed = PlatformEvidence(
        source=PLATFORM,
        identifiers={"response_id": "resp_1"},
        token_usage={"total_tokens": 790},
        tool_calls=[ActionEvent(tool_name="read", arguments='{"todo_id": "target"}', result='{"done": false}')],
    )
    rejected = InvocationError(
        "the platform answered HTTP 400",
        platform_evidence=PlatformEvidence(
            source=PLATFORM, identifiers={"request_id": "req_2"}, token_usage_missing="the platform returned an error"
        ),
    )
    earlier = json.loads(
        to_json(
            sweep(
                {"close": ["success", "error", "success"]},
                platform_evidence={"close/attempt-1": completed},
                invocation_errors={"close/attempt-2": rejected},
            )
        )
    )

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[
            OutcomeCorrection(
                attempt_id="close/attempt-3",
                task_outcome=TaskOutcome(success=False, source="manual review of the service audit log"),
                provenance="J. Jansen on 2026-11-27: the item was reopened by the same Attempt",
            )
        ],
    )

    (outcome_consistency,) = [entry for entry in recalculated["metrics"] if entry["metric_id"] == "outcome_consistency"]
    assert outcome_consistency["value"] == 0
    assert [attempt["platform_evidence"] for attempt in recalculated["attempts"]] == [
        attempt["platform_evidence"] for attempt in earlier["attempts"]
    ]
    assert recalculated["attempts"][0]["platform_evidence"]["identifiers"] == {"response_id": "resp_1"}
    assert recalculated["attempts"][1]["platform_evidence"]["token_usage_missing"] == "the platform returned an error"


def test_evidence_whose_platform_evidence_was_altered_into_something_else_is_not_recalculated():
    earlier = json.loads(to_json(sweep({"close": ["success", "success"]})))
    earlier["attempts"][0]["platform_evidence"] = {"source": PLATFORM, "identifiers": {}, "token_usage": None}

    correction = OutcomeCorrection(
        attempt_id="close/attempt-2",
        task_outcome=TaskOutcome(success=False, source="manual review of the service audit log"),
        provenance="J. Jansen on 2026-11-27: the item was reopened by the same Attempt",
    )

    with pytest.raises(RecalculationFailed, match="Attempt 'close/attempt-1' is not valid evidence"):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[correction])

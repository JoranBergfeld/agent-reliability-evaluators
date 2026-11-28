"""Evaluator sweep seam: what an agent target recorded of each invocation stays on the Attempt.

The sweep is driven through its public entry point with a fake agent target. Every
assertion is on the machine-readable scorecard.
"""

from reliability_evaluators.agent_target import InvocationFailed
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import ExecutionLimits, Task, run_sweep
from tests.fakes import FakeAgentTarget, attempt_evidence, taking
from tests.fakes import fake_baseline_experiments

SOURCE = "final state of the fake service"
RETURNED = {
    "response_id": "resp_1",
    "agent": {"name": "todo", "version": "3"},
    "token_usage": {"input_tokens": 700, "output_tokens": 90, "total_tokens": 790},
    "token_usage_missing": None,
    "tool_calls": [{"name": "read", "arguments": {"todo_id": "target"}, "output": "{\"done\": false}"}],
}


def sweep(script, limits=ExecutionLimits(), **scripted):
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
        )
        for task_id, behaviours in script.items()
    ]
    return run_sweep(experiments=fake_baseline_experiments(tasks), agent_target=target, metrics=["outcome_consistency"], clock=target.clock)


def test_an_attempt_keeps_the_identifiers_usage_and_tool_calls_its_agent_target_recorded():
    scorecard = sweep({"close": ["success", "success"]}, invocations={"close/baseline/attempt-1": RETURNED})

    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "close/baseline/attempt-1",
        "task_id": "close",
        "execution_status": "completed",
        "execution_error": None,
        "task_outcome": {"success": True, "source": SOURCE},
        "invocation": RETURNED,
    }
    # The agent target recorded nothing of the second invocation, and nothing is made up for it.
    assert scorecard["attempts"][1]["invocation"] is None


def test_an_invocation_that_fails_keeps_what_was_recorded_of_it_and_is_not_a_task_failure():
    # Two successes and one failed invocation: p = 2/2 and the value is 1.
    # Scoring the failed invocation as a task failure would give (2 * 2/3 - 1)^2 = 0.111...
    rejected = {
        "http_status": 400,
        "request_ids": {"x-request-id": "req_7"},
        "token_usage": None,
        "token_usage_missing": "the platform returned an error and no usage",
    }
    failure = InvocationFailed("the platform answered HTTP 400 tool_user_error", invocation=rejected)

    scorecard = sweep({"close": ["success", "error", "success"]}, invocation_errors={"close/baseline/attempt-2": failure})

    assert attempt_evidence(scorecard["attempts"][1], "invocation") == {
        "attempt_id": "close/baseline/attempt-2",
        "task_id": "close",
        "execution_status": "failed",
        "execution_error": "InvocationFailed: the platform answered HTTP 400 tool_user_error",
        "task_outcome": None,
        "invocation": rejected,
    }
    (outcome_consistency,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == "outcome_consistency"]
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["counts"]["attempts"]["excluded"] == [
        {"reason": "execution status is not completed", "count": 1, "ids": ["close/baseline/attempt-2"]},
    ]


def test_an_answer_that_arrives_after_the_attempts_time_limit_keeps_what_was_recorded_of_it():
    # The agent target does not stop at the limit of 1 second and answers after 5. The Attempt
    # timed out and is not scored, and what was recorded of it is still kept: it was spent.
    scorecard = sweep(
        {"close": [taking(5.0, "success"), "success"]},
        limits=ExecutionLimits(attempt_timeout_seconds=1.0),
        invocations={"close/baseline/attempt-1": RETURNED},
        honours_time_limits=False,
    )

    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "close/baseline/attempt-1",
        "task_id": "close",
        "execution_status": "timed_out",
        "execution_error": "the agent target answered after the Attempt's time limit of 1 seconds",
        "task_outcome": None,
        "invocation": RETURNED,
    }

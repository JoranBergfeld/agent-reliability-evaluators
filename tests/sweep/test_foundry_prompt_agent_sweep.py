"""Evaluator sweep seam: a Foundry prompt agent as the agent target, without Foundry access.

The sweep is driven through its public entry point with the Foundry prompt agent target.
The target calls a fake Foundry project on this machine, whose scripted agent works on the
live fixture's to-do service, also run on this machine. Every assertion is on the
machine-readable scorecard. Expected values are calculated by hand.
"""

import json

import pytest

from live_fixture import TASK_PROMPT, local_service
from reliability_evaluators.agent_target.foundry_prompt_agent import (
    ELAPSED_TIME_BOUNDARY,
    NATIVE_TOOL_CALLS,
    TOOL_CALL_BOUNDARY,
    FoundryPromptAgentTarget,
    native_tool_calls,
)
from reliability_evaluators.sweep import ActionEvidenceDeclaration, ExecutionLimits, Task, run_sweep
from tests.fake_foundry_prompt_agent import fake_prompt_agent_project
from tests.fakes import attempt_evidence
from tests.todo_fixture_sweep import CONDITION_SOURCE, UPDATE_FAILS, TodoFixtureSweep

OUTCOME_SOURCE = "to-do service events and before/after state"
# The state of the to-do service that the Task's prompt names and the controls prepare and remove.
STATE = "run-1"
TARGET = {"id": "target", "title": "Synthetic target", "done": False}
SCRATCH = {"id": "scratch", "title": "Temporary", "done": False}
NO_RESPONSE = "the Foundry project returned no response, so no usage was reported"
ACTION_TYPES = {
    "todos_readTodo": "read",
    "todos_createTodo": "create",
    "todos_deleteTodo": "delete",
    "todos_updateTodo": "update",
}


def tool_call(invocation, position, name, arguments, returned):
    """A tool call as recorded from the fake project: the arguments decoded, the output as the text it gave."""
    return {
        "call_id": f"call_fake_{invocation}_{position}",
        "type": "openapi_call",
        "name": name,
        "arguments": arguments,
        "output": json.dumps({"response": json.dumps(returned)}),
        "status": "completed",
    }


def usage(input_tokens, output_tokens):
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": input_tokens + output_tokens,
    }


def without_a_response(invocation, http_status, error):
    """What is recorded of an invocation the fake project answered without a response object."""
    return {
        "agent_target": "foundry_prompt_agent",
        "entry_point": "customer_code",
        "protocol": "responses",
        "api_version": "v1",
        "composed_input": None,
        "http_status": http_status,
        "request_ids": {"x-request-id": f"req_fake_{invocation}", "apim-request-id": f"apim_fake_{invocation}"},
        "agent": None,
        "response_id": None,
        "status": None,
        "model": None,
        "served_model": None,
        "metadata": None,
        "error": error,
        "token_usage": None,
        "token_usage_missing": NO_RESPONSE,
        "tool_calls": None,
    }


@pytest.fixture
def todo_service():
    with local_service() as service:
        yield service


def metric(scorecard, metric_id):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def sweep(
    todo_service,
    script,
    metrics=("outcome_consistency",),
    *,
    attempts=None,
    starting_state="fresh",
    variation=None,
    limits=ExecutionLimits(),
    agent=None,
    **declared,
):
    """Run the fixture Task against the fake project's prompt agent, one Attempt per scripted behaviour.

    `agent` is what the target is given besides the project and the agent's name.
    Everything else is declared to the sweep.
    """
    fixture = TodoFixtureSweep(todo_service, state=STATE)
    with fake_prompt_agent_project(todo_service, script) as project:
        target = FoundryPromptAgentTarget(
            **{
                "project_endpoint": project.endpoint,
                "agent_name": "todo-prompt",
                "credential": lambda: "fake-token",
                **(agent or {}),
            }
        )
        experiment = fixture.experiment(
            attempts=attempts or len(script), starting_state=starting_state, variation=variation, limits=limits
        )
        return run_sweep(experiments=[experiment], agent_target=target, metrics=metrics, **declared)


def test_a_sweep_scores_a_prompt_agent_from_what_the_service_recorded_not_from_its_answer(todo_service):
    # Four Attempts complete the Task. In the fifth the agent leaves out the update and still
    # answers that it completed everything. p = 4/5, (2 * 0.8 - 1)^2 = 0.36.
    scorecard = sweep(todo_service, ["completes", "completes", "completes", "completes", "skips the update"])

    assert [attempt_evidence(attempt) for attempt in scorecard["attempts"]] == [
        {
            "attempt_id": f"todo/baseline/attempt-{number}",
            "task_id": "todo",
            "execution_status": "completed",
            "execution_error": None,
            "task_outcome": {"success": number < 5, "source": OUTCOME_SOURCE},
        }
        for number in (1, 2, 3, 4, 5)
    ]
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == pytest.approx(0.36, abs=1e-12)


def test_each_attempt_keeps_the_response_identifiers_the_usage_and_the_ordered_tool_calls(todo_service):
    scorecard = sweep(todo_service, ["skips the update", "completes"])

    assert scorecard["attempts"][0]["invocation"] == {
        "agent_target": "foundry_prompt_agent",
        "entry_point": "customer_code",
        "protocol": "responses",
        "api_version": "v1",
        "composed_input": None,
        "http_status": 200,
        "request_ids": {"x-request-id": "req_fake_1", "apim-request-id": "apim_fake_1"},
        "agent": {"type": "agent_reference", "name": "todo-prompt", "version": "2"},
        "response_id": "resp_fake_1",
        "status": "completed",
        "model": "todo-gpt-41-mini",
        "served_model": "gpt-4.1-mini-2025-04-14",
        # The identity of the Attempt was sent with the request, and the project returned it.
        "metadata": {"attempt_id": "todo/baseline/attempt-1", "task_id": "todo"},
        "error": None,
        "token_usage": usage(500, 60),
        "token_usage_missing": None,
        "tool_calls": [
            tool_call(1, 1, "todos_readTodo", {"attempt_id": STATE, "todo_id": "target"}, TARGET),
            tool_call(1, 2, "todos_createTodo", {"id": "scratch", "title": "Temporary", "attempt_id": STATE}, SCRATCH),
            tool_call(1, 3, "todos_readTodo", {"attempt_id": STATE, "todo_id": "scratch"}, SCRATCH),
            tool_call(1, 4, "todos_deleteTodo", {"attempt_id": STATE, "todo_id": "scratch"}, SCRATCH),
        ],
    }
    second = scorecard["attempts"][1]["invocation"]
    assert second["response_id"] == "resp_fake_2"
    assert second["metadata"] == {"attempt_id": "todo/baseline/attempt-2", "task_id": "todo"}
    assert second["token_usage"] == usage(600, 70)
    assert [call["name"] for call in second["tool_calls"]] == [
        "todos_readTodo",
        "todos_createTodo",
        "todos_readTodo",
        "todos_deleteTodo",
        "todos_updateTodo",
    ]


def test_the_target_reports_the_time_of_its_request_and_the_tool_call_count_as_resource_measurements(todo_service):
    # Tool calls: 4 and 5. Mean 4.5, population standard deviation 0.5, CV 1/9.
    # Elapsed time is 0 on the clock the target is given, so its CV is 0.
    # Task value exp(-(1/9 + 0) / 2) = exp(-1/18) = 0.9459594689067654.
    scorecard = sweep(
        todo_service, ["skips the update", "completes"], metrics=["resource_consistency"], agent={"clock": lambda: 0.0}
    )

    assert [attempt["resource_measurements"] for attempt in scorecard["attempts"]] == [
        [
            {
                "resource_type": "elapsed_time",
                "value": 0.0,
                "unit": "seconds",
                "boundary": ELAPSED_TIME_BOUNDARY,
                "reliably_sourced": True,
            },
            {
                "resource_type": "tool_calls",
                "value": count,
                "unit": "calls",
                "boundary": TOOL_CALL_BOUNDARY,
                "reliably_sourced": True,
            },
        ]
        for count in (4, 5)
    ]
    resource_consistency = metric(scorecard, "resource_consistency")
    assert resource_consistency["state"] == "available"
    assert resource_consistency["value"] == pytest.approx(0.9459594689067654, abs=1e-12)


def test_a_response_without_a_tool_call_is_a_confirmed_empty_sequence_and_a_count_of_zero(todo_service):
    scorecard = sweep(todo_service, ["does nothing", "completes"])

    answered_only = scorecard["attempts"][0]
    assert answered_only["execution_status"] == "completed"
    assert answered_only["task_outcome"] == {"success": False, "source": OUTCOME_SOURCE}
    # An empty list, not null: the response arrived and holds no tool call.
    assert answered_only["invocation"]["tool_calls"] == []
    assert [m["value"] for m in answered_only["resource_measurements"] if m["resource_type"] == "tool_calls"] == [0]


def test_the_tool_calls_of_the_response_can_be_declared_as_the_action_evidence(todo_service):
    # read, create, read, delete, update against read, create, read, update, delete.
    # Sequential: two substitutions, 2 / 5 = 0.4, so 1 - 0.4 = 0.6.
    # Distributional: both Attempts have the same mix of action types, divergence 0, so 1.
    declared = ActionEvidenceDeclaration(
        evidence_source=NATIVE_TOOL_CALLS, record=native_tool_calls, action_types=ACTION_TYPES
    )

    scorecard = sweep(
        todo_service,
        ["completes", "updates before it deletes"],
        metrics=["sequential_trajectory_consistency", "distributional_trajectory_consistency"],
        action_evidence=declared,
    )

    recorded = scorecard["attempts"][1]["action_evidence"]
    assert recorded["source"] == "tool calls in the response of the Foundry prompt agent"
    assert recorded["events"][3] == {
        "tool_name": "todos_updateTodo",
        "arguments": {"done": True, "attempt_id": STATE, "todo_id": "target"},
        "result": json.dumps({"response": json.dumps({**TARGET, "done": True})}),
        "error": None,
    }
    assert [event["tool_name"] for event in recorded["events"]] == [
        "todos_readTodo",
        "todos_createTodo",
        "todos_readTodo",
        "todos_updateTodo",
        "todos_deleteTodo",
    ]
    assert metric(scorecard, "sequential_trajectory_consistency")["value"] == pytest.approx(0.6, abs=1e-12)
    assert metric(scorecard, "distributional_trajectory_consistency")["value"] == pytest.approx(1.0, abs=1e-12)


def test_a_tool_fault_that_fails_the_invocation_is_an_execution_failure_and_not_a_task_failure(todo_service):
    # Two Attempts under the baseline complete the Task: p = 2/2, value 1. Under the fault the update
    # tool answers 503 and the project answers the whole invocation with HTTP 400, as observed live.
    scorecard = sweep(todo_service, ["completes"] * 4, attempts=2, variation=UPDATE_FAILS)

    faulted = scorecard["attempts"][2]
    assert attempt_evidence(faulted, "invocation", "observed_condition", "resource_measurements") == {
        "attempt_id": "todo/update-fails/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationFailed: the Foundry project answered HTTP 400: tool_user_error: "
            "A user error occurred while executing a tool call."
        ),
        "task_outcome": None,
        "invocation": without_a_response(
            3,
            400,
            {
                "message": "A user error occurred while executing a tool call.",
                "type": "invalid_request_error",
                "param": None,
                "code": "tool_user_error",
                "request_id": "err_fake_3",
            },
        ),
        # Only the service shows that the fault occurred, and that four calls went before it.
        "observed_condition": {
            "met": True,
            "source": CONDITION_SOURCE,
            "observations": {"calls": 5, "faulted_calls": ["update"]},
        },
        "resource_measurements": [],
    }
    assert scorecard["attempts"][3]["execution_status"] == "failed"
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["evidence"] == {"attempt_ids": ["todo/baseline/attempt-1", "todo/baseline/attempt-2"]}


def test_the_usage_the_project_returned_is_reported_per_attempt_and_a_refused_invocation_has_none(todo_service):
    # Each baseline Attempt makes five tool calls, for which the fake project reports 670 tokens.
    # Under the fault the project answers HTTP 400 with no response and no usage, as observed live.
    # Known: 670 + 670 = 1340 tokens, a partial sum.
    scorecard = sweep(todo_service, ["completes"] * 4, attempts=2, variation=UPDATE_FAILS)

    usage_reported = scorecard["token_usage"]
    assert [
        (entry["attempt_id"], entry["execution_status"], entry["invocation"]["basis"], entry["total_tokens"])
        for entry in usage_reported["attempts"]
    ] == [
        ("todo/baseline/attempt-1", "completed", "reported", 670),
        ("todo/baseline/attempt-2", "completed", "reported", 670),
        ("todo/update-fails/attempt-1", "failed", "missing", None),
        ("todo/update-fails/attempt-2", "failed", "missing", None),
    ]
    assert usage_reported["attempts"][0]["invocation"]["response_id"] == "resp_fake_1"
    # The refused request is still identified, so that what it used can be looked up on the platform.
    assert usage_reported["attempts"][2]["invocation"] == {
        "basis": "missing",
        "total_tokens": None,
        "reason": NO_RESPONSE,
        "response_id": None,
        "request_ids": {"x-request-id": "req_fake_3", "apim-request-id": "apim_fake_3"},
    }
    assert (usage_reported["sweep"]["coverage"], usage_reported["sweep"]["total_tokens"]) == ("partial", 1340)
    assert usage_reported["sweep"]["uses_missing"] == 2


def test_a_response_that_was_delivered_but_did_not_complete_is_an_execution_failure(todo_service):
    # The second response arrives with HTTP 200 and the status failed, after one tool call.
    # Two Attempts complete the Task: p = 2/2, value 1. As a task failure it would be (2 * 2/3 - 1)^2 = 0.111...
    scorecard = sweep(todo_service, ["completes", "response fails", "completes"])

    assert attempt_evidence(scorecard["attempts"][1], "invocation") == {
        "attempt_id": "todo/baseline/attempt-2",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationFailed: the response resp_fake_2 of the prompt agent ended with status 'failed': "
            "server_error: The model failed to respond."
        ),
        "task_outcome": None,
        "invocation": {
            "agent_target": "foundry_prompt_agent",
            "entry_point": "customer_code",
            "protocol": "responses",
            "api_version": "v1",
            "composed_input": None,
            "http_status": 200,
            "request_ids": {"x-request-id": "req_fake_2", "apim-request-id": "apim_fake_2"},
            "agent": {"type": "agent_reference", "name": "todo-prompt", "version": "2"},
            "response_id": "resp_fake_2",
            "status": "failed",
            "model": "todo-gpt-41-mini",
            "served_model": "gpt-4.1-mini-2025-04-14",
            "metadata": {"attempt_id": "todo/baseline/attempt-2", "task_id": "todo"},
            "error": {"code": "server_error", "message": "The model failed to respond."},
            # What the failed response used is kept: it was spent.
            "token_usage": usage(200, 30),
            "token_usage_missing": None,
            "tool_calls": [tool_call(2, 1, "todos_readTodo", {"attempt_id": STATE, "todo_id": "target"}, TARGET)],
        },
    }
    assert metric(scorecard, "outcome_consistency")["value"] == 1


def test_an_answer_that_is_not_a_response_is_an_execution_failure(todo_service):
    scorecard = sweep(todo_service, ["completes", "not json"])

    assert attempt_evidence(scorecard["attempts"][1], "invocation") == {
        "attempt_id": "todo/baseline/attempt-2",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationFailed: the Foundry project answered HTTP 200 with a body that is not a response: "
            "<html>Bad gateway</html>"
        ),
        "task_outcome": None,
        "invocation": without_a_response(2, 200, "<html>Bad gateway</html>"),
    }


def test_a_project_that_cannot_be_reached_is_an_execution_failure(todo_service):
    # Nothing listens on the discard port of this machine.
    scorecard = sweep(todo_service, ["completes"], agent={"project_endpoint": "http://127.0.0.1:9/api/projects/p"})

    unreached = scorecard["attempts"][0]
    assert unreached["execution_status"] == "failed"
    assert unreached["execution_error"].startswith("InvocationFailed: the Foundry project could not be reached: ")
    assert unreached["task_outcome"] is None
    # No answer came back, so there is nothing the platform returned.
    assert unreached["invocation"] is None


def test_a_new_conversation_for_every_attempt_does_not_reset_what_the_agent_changed(todo_service):
    # Continuing state is declared: the state is prepared once and every Attempt starts from what the
    # one before left. Every invocation is a response of its own, linked to no earlier one, and the
    # agent does the whole Task each time. The first Attempt succeeds. After the second the service
    # holds ten operations instead of the five of the Task, and so on: the other four fail.
    # p = 1/5, (2 * 0.2 - 1)^2 = 0.36. Were each new conversation a reset, p would be 1 and the value 1.
    scorecard = sweep(todo_service, ["completes"] * 5, starting_state="continuing")

    attempts = scorecard["attempts"]
    assert [attempt["invocation"]["response_id"] for attempt in attempts] == [
        f"resp_fake_{number}" for number in (1, 2, 3, 4, 5)
    ]
    assert [len(attempt["invocation"]["tool_calls"]) for attempt in attempts] == [5] * 5
    assert [attempt["task_outcome"]["success"] for attempt in attempts] == [True, False, False, False, False]
    assert {attempt["starting_state"] for attempt in attempts} == {"continuing"}
    assert metric(scorecard, "outcome_consistency")["value"] == pytest.approx(0.36, abs=1e-12)


def test_the_target_puts_no_starting_state_policy_in_the_place_of_one_that_was_not_declared(todo_service):
    # A Task given without an experiment: no control prepares its state, so both Attempts work on
    # the same state of the service, and the second fails as above. p = 1/2, (2 * 0.5 - 1)^2 = 0.
    todo_service.prepare_attempt(STATE)
    task = TodoFixtureSweep(todo_service, state=STATE).experiment(attempts=2).task

    with fake_prompt_agent_project(todo_service) as project:
        target = FoundryPromptAgentTarget(
            project_endpoint=project.endpoint, agent_name="todo-prompt", credential=lambda: "fake-token"
        )
        scorecard = run_sweep(tasks=[task], agent_target=target, metrics=["outcome_consistency"])

    first, second = scorecard["attempts"]
    assert [first["task_outcome"]["success"], second["task_outcome"]["success"]] == [True, False]
    assert [first["starting_state"], second["starting_state"]] == [None, None]
    assert metric(scorecard, "outcome_consistency")["value"] == 0


def test_a_prompt_agent_is_not_asked_for_a_confidence_rating(todo_service):
    scorecard = sweep(todo_service, ["completes", "completes"], metrics=["brier_based_score"])

    reason = (
        "the agent target does not support an isolated follow-up: a follow-up in which a Foundry prompt agent "
        "answers in the conversation of the Attempt and cannot act has not been verified"
    )
    unavailable = {"rating": None, "normalized": None, "original_response": None, "unavailable_reason": reason}
    assert [attempt["confidence"] for attempt in scorecard["attempts"]] == [unavailable, unavailable]
    brier_based_score = metric(scorecard, "brier_based_score")
    assert brier_based_score["state"] == "unavailable"
    assert brier_based_score["value"] is None
    # The agent was invoked for the two Attempts and for nothing else.
    assert [attempt["invocation"]["response_id"] for attempt in scorecard["attempts"]] == [
        "resp_fake_1",
        "resp_fake_2",
    ]


def test_a_pinned_agent_version_is_the_one_that_answers(todo_service):
    # The fake project serves version 2 of the agent unless a request names another.
    scorecard = sweep(todo_service, ["completes", "completes"], agent={"agent_version": "1"})

    assert [attempt["invocation"]["agent"] for attempt in scorecard["attempts"]] == [
        {"type": "agent_reference", "name": "todo-prompt", "version": "1"},
        {"type": "agent_reference", "name": "todo-prompt", "version": "1"},
    ]


def test_an_agent_version_that_does_not_exist_is_an_execution_failure(todo_service):
    scorecard = sweep(todo_service, ["completes", "completes"], agent={"agent_version": "9"})

    message = "Agent todo-prompt with version 9 not found [Request ID: req_fake_1]"
    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": f"InvocationFailed: the Foundry project answered HTTP 404: not_found: {message}",
        "task_outcome": None,
        "invocation": without_a_response(
            1,
            404,
            {
                "code": "not_found",
                "type": "error",
                "details": [],
                "message": message,
                "additionalInfo": {"request_id": "req_fake_1"},
            },
        ),
    }
    # No Attempt completed, so the metric has no state of its own: its experiment failed.
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["state"] is None
    assert outcome_consistency["value"] is None
    assert outcome_consistency["execution_failure"]["experiment_ids"] == ["todo"]


def test_an_invocation_that_outlasts_the_targets_own_time_limit_timed_out(todo_service):
    # The response takes a second and the target waits three tenths of one.
    scorecard = sweep(todo_service, [1.0], agent={"timeout_seconds": 0.3})

    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "timed_out",
        "execution_error": "TimeoutError: the Foundry project did not answer within 0.3 seconds",
        "task_outcome": None,
        "invocation": None,
    }


def test_the_target_stops_waiting_at_the_time_limit_the_customer_declares_for_an_attempt(todo_service):
    # The response takes a second. The customer allows an Attempt three tenths of one, and the
    # target's own limit is left at its default.
    scorecard = sweep(todo_service, [1.0], limits=ExecutionLimits(attempt_timeout_seconds=0.3))

    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "timed_out",
        "execution_error": "TimeoutError: the Foundry project did not answer within 0.3 seconds",
        "task_outcome": None,
        "invocation": None,
    }


def test_an_invocation_the_project_refuses_for_its_token_is_an_execution_failure(todo_service):
    scorecard = sweep(todo_service, ["completes", "completes"], agent={"credential": lambda: "another-tenants-token"})

    refused = scorecard["attempts"][0]
    assert refused["execution_status"] == "failed"
    assert refused["execution_error"] == (
        "InvocationFailed: the Foundry project answered HTTP 401: 401: Access denied due to invalid subscription key."
    )
    assert refused["task_outcome"] is None
    assert refused["invocation"]["http_status"] == 401
    assert refused["invocation"]["request_ids"] == {"apim-request-id": "apim_fake_1"}


def test_the_agent_is_sent_the_prompt_exactly_as_it_is_unless_the_customer_composes_the_input(todo_service):
    # The Task's prompt names the state the agent works on. The fake agent finds its state in
    # what it is sent, so the Task can only succeed when the prompt arrived as it is.
    scorecard = sweep(todo_service, ["completes", "completes"])

    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True, True]
    assert [attempt["invocation"]["composed_input"] for attempt in scorecard["attempts"]] == [None, None]


def test_an_input_the_customer_composed_is_what_the_agent_is_sent_and_is_recorded_as_it_was_sent(todo_service):
    # This Task's prompt does not say which state to work on. The customer composes that in front of it.
    todo_service.prepare_attempt(STATE)
    fixture = TodoFixtureSweep(todo_service, state=STATE)
    task = Task(task_id="todo", prompt=TASK_PROMPT, success_rule=fixture.success_rule, attempts=1)

    with fake_prompt_agent_project(todo_service) as project:
        target = FoundryPromptAgentTarget(
            project_endpoint=project.endpoint,
            agent_name="todo-prompt",
            credential=lambda: "fake-token",
            compose_input=lambda request: f"Use attempt_id {STATE}. {request.prompt}",
        )
        scorecard = run_sweep(tasks=[task], agent_target=target, metrics=["outcome_consistency"])

    (attempt,) = scorecard["attempts"]
    assert attempt["task_outcome"] == {"success": True, "source": OUTCOME_SOURCE}
    assert attempt["invocation"]["composed_input"] == f"Use attempt_id {STATE}. {TASK_PROMPT}"


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        ({"agent_name": ""}, "name"),
        ({"agent_version": 1}, "version"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"project_endpoint": "todo-fixture"}, "endpoint"),
    ],
)
def test_a_target_that_cannot_invoke_an_agent_is_rejected_before_any_attempt_is_made(declared, problem):
    with pytest.raises(ValueError, match=problem):
        FoundryPromptAgentTarget(
            **{
                "project_endpoint": "https://example.invalid/api/projects/p",
                "agent_name": "todo-prompt",
                "credential": lambda: "fake-token",
                **declared,
            }
        )

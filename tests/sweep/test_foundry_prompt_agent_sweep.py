"""Evaluator sweep seam: a Foundry prompt agent as the agent target, without Foundry access.

The sweep is driven through its public entry point with the Foundry prompt agent target.
The target calls a fake Foundry project on this machine, whose scripted agent works on the
live fixture's to-do service, also run on this machine. Every assertion is on the
machine-readable scorecard. Expected values are calculated by hand.
"""

import json

import pytest

from live_fixture import TASK_PROMPT, local_service, strict_task_outcome
from reliability_evaluators.agent_target import (
    AzureCliToken,
    FoundryProject,
    FoundryPromptAgentTarget,
    platform_tool_calls,
)
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import ActionEvidenceDeclaration, Task, run_sweep
from tests.fake_foundry import fake_foundry_project
from tests.fakes import attempt_evidence
from tests.todo_fixture_sweep import CONDITION_SOURCE, UPDATE_FAILS, TodoFixtureSweep

OUTCOME_SOURCE = "to-do service events and before/after state"
PLATFORM_SOURCE = "Foundry Responses API, called from customer code"
TOOL_CALL_BOUNDARY = "tool calls in the output of the Foundry response, counted by the agent target"
TARGET = {"id": "target", "title": "Synthetic target", "done": False}
SCRATCH = {"id": "scratch", "title": "Temporary", "done": False}


def tool_call(tool_name, arguments, returned):
    """A tool call as the fake project exposes it: the arguments and the output are JSON text."""
    return {
        "tool_name": tool_name,
        "arguments": json.dumps(arguments, separators=(",", ":")),
        "result": json.dumps({"response": json.dumps(returned)}),
        "error": None,
    }


def usage(input_tokens, output_tokens):
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": input_tokens + output_tokens,
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
    variation=None,
    agent=None,
    token=lambda: "fake-token",
    **declared,
):
    """Run the fixture Task against the fake project's prompt agent, one Attempt per scripted behaviour.

    `agent` is what the target is told about the agent besides its name, and `token` is
    the caller's token provider. Everything else is declared to the sweep.
    """
    fixture = TodoFixtureSweep(todo_service, run_id="run-1")
    with fake_foundry_project(todo_service, script) as project:
        target = FoundryPromptAgentTarget(
            project=FoundryProject(project.endpoint, token=token),
            agent_name="todo-prompt",
            compose_input=fixture.compose_input,
            **(agent or {}),
        )
        experiment = fixture.experiment(attempts=attempts or len(script), variation=variation)
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

    state = "run-1-todo-baseline-attempt-1"
    assert scorecard["attempts"][0]["platform_evidence"] == {
        "source": PLATFORM_SOURCE,
        "identifiers": {
            "response_id": "resp_fake_1",
            "status": "completed",
            "agent_reference": {"type": "agent_reference", "name": "todo-prompt", "version": "2"},
            "model": "todo-gpt-41-mini",
            "served_model": "gpt-4.1-mini-2025-04-14",
            # The identity of the Attempt was sent with the request, and the project returned it.
            "metadata": {"attempt_id": "todo/baseline/attempt-1", "task_id": "todo"},
            "request_id": "req_fake_1",
            "apim_request_id": "apim_fake_1",
        },
        "token_usage": usage(500, 60),
        "token_usage_missing": None,
        "tool_calls": [
            tool_call("todos_readTodo", {"attempt_id": state, "todo_id": "target"}, TARGET),
            tool_call("todos_createTodo", {"id": "scratch", "title": "Temporary", "attempt_id": state}, SCRATCH),
            tool_call("todos_readTodo", {"attempt_id": state, "todo_id": "scratch"}, SCRATCH),
            tool_call("todos_deleteTodo", {"attempt_id": state, "todo_id": "scratch"}, SCRATCH),
        ],
    }
    second = scorecard["attempts"][1]["platform_evidence"]
    assert second["identifiers"]["response_id"] == "resp_fake_2"
    assert second["identifiers"]["metadata"] == {"attempt_id": "todo/baseline/attempt-2", "task_id": "todo"}
    assert second["token_usage"] == usage(600, 70)
    assert [call["tool_name"] for call in second["tool_calls"]] == [
        "todos_readTodo",
        "todos_createTodo",
        "todos_readTodo",
        "todos_deleteTodo",
        "todos_updateTodo",
    ]


def test_the_tool_call_count_is_measured_so_that_resource_consistency_includes_it(todo_service):
    # Tool calls: 4 and 5. Mean 4.5, population standard deviation 0.5, CV 1/9.
    # Elapsed time is 0 on the clock given, so its CV is 0. Task value exp(-(1/9 + 0) / 2) = exp(-1/18).
    scorecard = sweep(
        todo_service, ["skips the update", "completes"], metrics=["resource_consistency"], clock=lambda: 0.0
    )

    assert [
        [measured for measured in attempt["resource_measurements"] if measured["resource_type"] == "tool_calls"]
        for attempt in scorecard["attempts"]
    ] == [
        [{"resource_type": "tool_calls", "value": count, "unit": "calls", "boundary": TOOL_CALL_BOUNDARY,
          "reliably_sourced": True}]
        for count in (4, 5)
    ]
    resource_consistency = metric(scorecard, "resource_consistency")
    assert resource_consistency["state"] == "available"
    assert resource_consistency["value"] == pytest.approx(0.9459594689067654, abs=1e-12)


def test_the_tool_calls_the_platform_exposed_can_be_declared_as_the_action_evidence(todo_service):
    # read, create, read, delete, update against read, create, read, update, delete.
    # Sequential: two substitutions, 2 / 5 = 0.4, so 1 - 0.4 = 0.6.
    # Distributional: both Attempts have the same mix of action types, divergence 0, so 1.
    native_tool_calls = ActionEvidenceDeclaration(
        evidence_source="tool calls exposed by the Foundry project",
        record=platform_tool_calls,
        action_types={
            "todos_readTodo": "read",
            "todos_createTodo": "create",
            "todos_deleteTodo": "delete",
            "todos_updateTodo": "update",
        },
    )

    scorecard = sweep(
        todo_service,
        ["completes", "updates before it deletes"],
        metrics=["sequential_trajectory_consistency", "distributional_trajectory_consistency"],
        action_evidence=native_tool_calls,
    )

    recorded = scorecard["attempts"][1]["action_evidence"]
    assert recorded["source"] == "tool calls exposed by the Foundry project"
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
    assert attempt_evidence(faulted, "platform_evidence", "observed_condition", "resource_measurements") == {
        "attempt_id": "todo/update-fails/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationError: the Foundry project answered HTTP 400 tool_user_error: "
            "A user error occurred while executing a tool call."
        ),
        "task_outcome": None,
        "platform_evidence": {
            "source": PLATFORM_SOURCE,
            "identifiers": {
                "http_status": 400,
                "error_code": "tool_user_error",
                "error_request_id": "err_fake_3",
                "request_id": "req_fake_3",
                "apim_request_id": "apim_fake_3",
            },
            "token_usage": None,
            "token_usage_missing": "the Foundry project answered HTTP 400 and reported no usage",
            "tool_calls": None,
        },
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


def test_a_response_that_was_delivered_but_did_not_complete_is_an_execution_failure(todo_service):
    # The second response arrives with HTTP 200 and the status failed, after one tool call.
    # Two Attempts complete the Task: p = 2/2, value 1. As a task failure it would be (2 * 2/3 - 1)^2 = 0.111...
    scorecard = sweep(todo_service, ["completes", "response fails", "completes"])

    assert attempt_evidence(scorecard["attempts"][1], "platform_evidence") == {
        "attempt_id": "todo/baseline/attempt-2",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationError: the Foundry response resp_fake_2 ended with the status 'failed': "
            "server_error: The model failed to respond."
        ),
        "task_outcome": None,
        "platform_evidence": {
            "source": PLATFORM_SOURCE,
            "identifiers": {
                "response_id": "resp_fake_2",
                "status": "failed",
                "agent_reference": {"type": "agent_reference", "name": "todo-prompt", "version": "2"},
                "model": "todo-gpt-41-mini",
                "served_model": "gpt-4.1-mini-2025-04-14",
                "metadata": {"attempt_id": "todo/baseline/attempt-2", "task_id": "todo"},
                "request_id": "req_fake_2",
                "apim_request_id": "apim_fake_2",
            },
            # What the failed response used is kept: it was spent.
            "token_usage": usage(200, 30),
            "token_usage_missing": None,
            "tool_calls": [
                tool_call(
                    "todos_readTodo", {"attempt_id": "run-1-todo-baseline-attempt-2", "todo_id": "target"}, TARGET
                ),
            ],
        },
    }
    assert metric(scorecard, "outcome_consistency")["value"] == 1


def test_a_reply_that_is_not_a_response_is_an_execution_failure(todo_service):
    scorecard = sweep(todo_service, ["completes", "not json"])

    assert attempt_evidence(scorecard["attempts"][1], "platform_evidence") == {
        "attempt_id": "todo/baseline/attempt-2",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationError: the Foundry project answered HTTP 200 with a body that is not a response: "
            "<html>Bad gateway</html>"
        ),
        "task_outcome": None,
        "platform_evidence": {
            "source": PLATFORM_SOURCE,
            "identifiers": {"http_status": 200, "request_id": "req_fake_2", "apim_request_id": "apim_fake_2"},
            "token_usage": None,
            "token_usage_missing": "the Foundry project returned no response, so no usage",
            "tool_calls": None,
        },
    }


def test_a_new_conversation_for_every_attempt_is_not_recorded_as_a_reset_of_external_state(todo_service):
    # A Task given without an experiment: no control prepares its state, and both Attempts work on
    # the same state of the service. Every invocation is a response of its own, linked to no earlier one.
    # The second Attempt still starts from what the first left: the service then holds ten operations
    # instead of the five of the Task, so it fails. p = 1/2, (2 * 0.5 - 1)^2 = 0.
    todo_service.prepare_attempt("shared")

    def strict_rule(request, response):
        outcome = strict_task_outcome(todo_service.evidence("shared"))
        return TaskOutcome(success=outcome["success"], source=outcome["source"])

    with fake_foundry_project(todo_service, ["completes", "completes"]) as project:
        target = FoundryPromptAgentTarget(
            project=FoundryProject(project.endpoint, token=lambda: "fake-token"),
            agent_name="todo-prompt",
            compose_input=lambda request: f"Use attempt_id shared. {request.prompt}",
        )
        scorecard = run_sweep(
            tasks=[Task(task_id="todo", prompt=TASK_PROMPT, success_rule=strict_rule, attempts=2)],
            agent_target=target,
            metrics=["outcome_consistency"],
        )

    first, second = scorecard["attempts"]
    assert first["platform_evidence"]["identifiers"]["response_id"] == "resp_fake_1"
    assert second["platform_evidence"]["identifiers"]["response_id"] == "resp_fake_2"
    assert [first["task_outcome"]["success"], second["task_outcome"]["success"]] == [True, False]
    # Nothing declared a starting-state policy, and the agent target does not put one in its place.
    assert [first["starting_state"], second["starting_state"]] == [None, None]
    assert metric(scorecard, "outcome_consistency")["value"] == 0


def test_no_confidence_rating_is_recorded_because_the_target_declares_no_isolated_follow_up(todo_service):
    scorecard = sweep(todo_service, ["completes", "completes"], metrics=["brier_based_score"])

    reason = (
        "the agent target does not support an isolated follow-up: a confidence-only follow-up in which "
        "a Foundry prompt agent cannot act has not been verified"
    )
    unavailable = {"rating": None, "normalized": None, "original_response": None, "unavailable_reason": reason}
    assert [attempt["confidence"] for attempt in scorecard["attempts"]] == [unavailable, unavailable]
    brier_based_score = metric(scorecard, "brier_based_score")
    assert brier_based_score["state"] == "unavailable"
    assert brier_based_score["value"] is None
    # The agent was invoked for the two Attempts and for nothing else.
    assert [attempt["platform_evidence"]["identifiers"]["response_id"] for attempt in scorecard["attempts"]] == [
        "resp_fake_1",
        "resp_fake_2",
    ]


def test_a_pinned_agent_version_is_the_one_that_answers(todo_service):
    # The fake project serves version 2 of the agent unless a request names another.
    scorecard = sweep(todo_service, ["completes", "completes"], agent={"agent_version": "1"})

    assert [attempt["platform_evidence"]["identifiers"]["agent_reference"] for attempt in scorecard["attempts"]] == [
        {"type": "agent_reference", "name": "todo-prompt", "version": "1"},
        {"type": "agent_reference", "name": "todo-prompt", "version": "1"},
    ]


def test_an_agent_version_that_does_not_exist_is_an_execution_failure(todo_service):
    scorecard = sweep(todo_service, ["completes", "completes"], agent={"agent_version": "9"})

    assert attempt_evidence(scorecard["attempts"][0], "platform_evidence") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationError: the Foundry project answered HTTP 404 not_found: "
            "Agent todo-prompt with version 9 not found [Request ID: req_fake_1]"
        ),
        "task_outcome": None,
        "platform_evidence": {
            "source": PLATFORM_SOURCE,
            "identifiers": {
                "http_status": 404,
                "error_code": "not_found",
                "error_request_id": "req_fake_1",
                "request_id": "req_fake_1",
                "apim_request_id": "apim_fake_1",
            },
            "token_usage": None,
            "token_usage_missing": "the Foundry project answered HTTP 404 and reported no usage",
            "tool_calls": None,
        },
    }
    # No Attempt completed, so the metric has no state of its own: its experiment failed.
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["state"] is None
    assert outcome_consistency["value"] is None
    assert outcome_consistency["execution_failure"]["experiment_ids"] == ["todo"]


def test_an_invocation_that_outlasts_the_targets_own_time_limit_timed_out(todo_service):
    # The first response takes half a second and the target waits a tenth of one.
    scorecard = sweep(todo_service, [0.5, "completes", "completes"], agent={"timeout_seconds": 0.1})

    assert attempt_evidence(scorecard["attempts"][0], "platform_evidence") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "timed_out",
        "execution_error": "TimeoutError: the Foundry project did not answer within 0.1 seconds",
        "task_outcome": None,
        "platform_evidence": None,
    }
    assert [attempt["execution_status"] for attempt in scorecard["attempts"][1:]] == ["completed", "completed"]
    assert metric(scorecard, "outcome_consistency")["value"] == 1


def test_an_invocation_the_project_refuses_for_its_token_is_an_execution_failure(todo_service):
    scorecard = sweep(todo_service, ["completes", "completes"], token=lambda: "a-token-of-another-tenant")

    assert attempt_evidence(scorecard["attempts"][0], "platform_evidence") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationError: the Foundry project answered HTTP 401 401: "
            "Access denied due to invalid subscription key."
        ),
        "task_outcome": None,
        "platform_evidence": {
            "source": PLATFORM_SOURCE,
            "identifiers": {"http_status": 401, "error_code": "401", "apim_request_id": "apim_fake_1"},
            "token_usage": None,
            "token_usage_missing": "the Foundry project answered HTTP 401 and reported no usage",
            "tool_calls": None,
        },
    }


class FakeAzureCli:
    """Stands in for `az account get-access-token`. Every token it issues is valid for an hour.

    Issuing a token makes it the only one the fake project accepts. Each reading of `clock`
    is 25 minutes after the one before.
    """

    def __init__(self, project, signed_in=True):
        self._project = project
        self._signed_in = signed_in
        self._now = -1500.0
        self.commands = []

    def clock(self):
        self._now += 1500.0
        return self._now

    def run(self, arguments):
        self.commands.append(list(arguments))
        if not self._signed_in:
            raise RuntimeError("Please run 'az login' to setup account.")
        token = f"cli-token-{len(self.commands)}"
        self._project.valid_tokens = {token}
        return json.dumps({"accessToken": token, "expires_on": int(self._now) + 3600, "tokenType": "Bearer"})


def sweep_as_the_azure_cli_identity(todo_service, attempts, signed_in=True):
    fixture = TodoFixtureSweep(todo_service, run_id="run-1")
    with fake_foundry_project(todo_service, valid_tokens=()) as project:
        cli = FakeAzureCli(project, signed_in)
        target = FoundryPromptAgentTarget(
            project=FoundryProject(project.endpoint, token=AzureCliToken(run_cli=cli.run, clock=cli.clock)),
            agent_name="todo-prompt",
            compose_input=fixture.compose_input,
        )
        scorecard = run_sweep(
            experiments=[fixture.experiment(attempts=attempts)], agent_target=target, metrics=["outcome_consistency"]
        )
    return scorecard, cli


def test_the_azure_cli_is_asked_for_a_token_again_only_when_the_one_it_gave_nears_its_expiry(todo_service):
    # The Attempts are invoked at 0, 25, 50 and 75 minutes. The first token expires at 60 minutes and is
    # reused while more than 5 minutes of it remain: at 25 and at 50 minutes, not at 75.
    scorecard, cli = sweep_as_the_azure_cli_identity(todo_service, attempts=4)

    assert [attempt["execution_status"] for attempt in scorecard["attempts"]] == ["completed"] * 4
    assert cli.commands == [["account", "get-access-token", "--resource", "https://ai.azure.com"]] * 2


def test_an_azure_cli_that_gives_no_token_is_an_execution_failure_with_its_reason(todo_service):
    scorecard, _ = sweep_as_the_azure_cli_identity(todo_service, attempts=2, signed_in=False)

    assert [attempt_evidence(attempt) for attempt in scorecard["attempts"]] == [
        {
            "attempt_id": f"todo/baseline/attempt-{number}",
            "task_id": "todo",
            "execution_status": "failed",
            "execution_error": "RuntimeError: Please run 'az login' to setup account.",
            "task_outcome": None,
        }
        for number in (1, 2)
    ]

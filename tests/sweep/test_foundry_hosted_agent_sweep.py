"""Evaluator sweep seam: Attempts made against a Foundry hosted agent, from customer code.

The sweep is driven through its public entry point with the Foundry hosted agent target.
The target talks to a fake Foundry project on this machine, whose hosted agent works on
the fixture's to-do service, also on this machine. Every assertion is on the
machine-readable scorecard. Expected values are calculated by hand.
"""

import time

import pytest

from live_fixture import local_service
from live_fixture.hosted_agent import todo_tools
from reliability_evaluators.agent_target.foundry_hosted import (
    NATIVE_TOOL_CALLS,
    FoundryHostedAgentTarget,
    UnsupportedAgent,
    native_tool_calls,
)
from reliability_evaluators.sweep import ActionEvidenceDeclaration, run_sweep
from tests.fake_foundry_hosted import FakeHostedAgent, FakeTodoHostedAgent, completed_response, fake_foundry_project
from tests.todo_experiment import ALL_TOOLS_WORK, TodoSetup, update_fails_on

AGENT = {"agent": "todo-hosted", "version": "1"}


@pytest.fixture
def setup(monkeypatch):
    """The to-do service on this machine, with the customer's controls and success rule for it."""
    with local_service() as service:
        monkeypatch.setenv("TODO_SERVICE_URL", service.url)
        monkeypatch.setenv("TODO_API_KEY", service.api_key)
        yield TodoSetup(service, run_id="offline")


@pytest.fixture
def project():
    """A fake Foundry project whose hosted agent `todo-hosted` works the fixture's Task."""
    with fake_foundry_project({"todo-hosted": FakeHostedAgent(FakeTodoHostedAgent(todo_tools))}) as running:
        yield running


def hosted_agent(project, setup, agent_name="todo-hosted"):
    return FoundryHostedAgentTarget(
        project_endpoint=project.endpoint,
        agent_name=agent_name,
        credential=lambda: project.token,
        compose_input=setup.instruction,
    )


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def attempts_of(scorecard, experiment_id):
    return [attempt for attempt in scorecard["attempts"] if attempt["experiment_id"] == experiment_id]


def flaky_update_sweep(project, setup, metrics=("outcome_consistency",), **declared):
    """Three Attempts with every tool working, and five in which update fails in Attempts 2 and 4."""
    return run_sweep(
        experiments=[
            setup.experiment("todo", 3, ALL_TOOLS_WORK, AGENT),
            setup.experiment("todo-flaky-update", 5, update_fails_on(2, 4), AGENT),
        ],
        agent_target=hosted_agent(project, setup),
        metrics=metrics,
        **declared,
    )


def test_a_sweep_attempts_a_task_on_a_hosted_agent_and_scores_outcome_consistency(project, setup):
    # todo: p = 3/3, value 1. todo-flaky-update: p = 3/5, (2 * 0.6 - 1)^2 = 0.04. Mean: (1 + 0.04) / 2 = 0.52.
    scorecard = flaky_update_sweep(project, setup)

    assert [attempt["execution_status"] for attempt in scorecard["attempts"]] == ["completed"] * 8
    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [
        True, True, True,
        True, False, True, False, True,
    ]
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == pytest.approx(0.52, abs=1e-12)


def test_each_attempt_records_the_identifiers_the_platform_returned_for_its_invocation(project, setup):
    scorecard = run_sweep(
        experiments=[setup.experiment("todo", 2, ALL_TOOLS_WORK, AGENT)],
        agent_target=hosted_agent(project, setup),
        metrics=["outcome_consistency"],
    )

    first, second = scorecard["attempts"]
    assert (first["attempt_id"], second["attempt_id"]) == (
        "todo/all-tools-work/attempt-1",
        "todo/all-tools-work/attempt-2",
    )
    identifiers = ("agent_target", "protocol", "api_version", "agent", "response_id", "agent_session_id", "status")
    assert {key: first["invocation"][key] for key in identifiers} == {
        "agent_target": "foundry_hosted_agent",
        "protocol": "responses",
        "api_version": "v1",
        "agent": {"type": "agent_reference", "name": "todo-hosted", "version": "1"},
        "response_id": "caresp_synthetic1",
        "agent_session_id": "session-synthetic1",
        "status": "completed",
    }
    assert second["invocation"]["response_id"] == "caresp_synthetic2"
    assert second["invocation"]["agent_session_id"] == "session-synthetic2"
    # The project's first request was the look-up of the agent, and the Attempts were its second and third.
    assert first["invocation"]["request_ids"] == {
        "x-request-id": "synthetic-request-2",
        "apim-request-id": "synthetic-apim-2",
    }
    assert second["invocation"]["request_ids"]["x-request-id"] == "synthetic-request-3"


def test_the_agent_is_sent_the_identity_of_the_attempt_and_the_response_returns_it(project, setup):
    scorecard = run_sweep(
        experiments=[setup.experiment("todo", 2, ALL_TOOLS_WORK, AGENT)],
        agent_target=hosted_agent(project, setup),
        metrics=["outcome_consistency"],
    )

    assert [attempt["invocation"]["metadata"] for attempt in scorecard["attempts"]] == [
        {"attempt_id": "todo/all-tools-work/attempt-1", "task_id": "todo"},
        {"attempt_id": "todo/all-tools-work/attempt-2", "task_id": "todo"},
    ]


ON_SERVICE = "offline.todo-flaky-update.update-fails-intermittently.attempt-1"
# What the fixture's tools returned to the agent, as text, when update failed.
TOOL_CALLS_WITH_A_FAILED_UPDATE = [
    ("read_todo", {"attempt_id": ON_SERVICE, "todo_id": "target"},
     '{"status": 200, "body": {"id": "target", "title": "Synthetic target", "done": false}}'),
    ("create_todo", {"attempt_id": ON_SERVICE, "todo_id": "scratch", "title": "Temporary"},
     '{"status": 201, "body": {"id": "scratch", "title": "Temporary", "done": false}}'),
    ("read_todo", {"attempt_id": ON_SERVICE, "todo_id": "scratch"},
     '{"status": 200, "body": {"id": "scratch", "title": "Temporary", "done": false}}'),
    ("delete_todo", {"attempt_id": ON_SERVICE, "todo_id": "scratch"},
     '{"status": 200, "body": {"id": "scratch", "title": "Temporary", "done": false}}'),
    ("update_todo", {"attempt_id": ON_SERVICE, "todo_id": "target", "done": True},
     '{"status": 503, "body": {"error": "injected fault", "tool": "update"}}'),
]


def one_attempt_with_a_failed_update(project, setup, **declared):
    scorecard = run_sweep(
        experiments=[setup.experiment("todo-flaky-update", 1, update_fails_on(1), AGENT)],
        agent_target=declared.pop("agent_target", None) or hosted_agent(project, setup),
        metrics=declared.pop("metrics", ["outcome_consistency"]),
        **declared,
    )
    (attempt,) = scorecard["attempts"]
    return attempt


def test_each_attempt_records_the_tool_calls_the_response_exposed_in_order(project, setup):
    attempt = one_attempt_with_a_failed_update(project, setup)

    assert attempt["invocation"]["tool_calls"] == [
        {"call_id": f"call_1_{position}", "name": name, "arguments": arguments, "output": returned}
        for position, (name, arguments, returned) in enumerate(TOOL_CALLS_WITH_A_FAILED_UPDATE, start=1)
    ]


def test_the_tool_calls_of_the_response_can_be_declared_as_the_action_evidence(project, setup):
    attempt = one_attempt_with_a_failed_update(
        project,
        setup,
        metrics=["sequential_trajectory_consistency"],
        action_evidence=ActionEvidenceDeclaration(
            evidence_source=NATIVE_TOOL_CALLS,
            record=native_tool_calls,
            action_types={"read_todo": "read", "create_todo": "create", "delete_todo": "delete", "update_todo": "update"},
        ),
    )

    assert attempt["action_evidence"] == {
        "source": "tool calls in the response of the Foundry hosted agent",
        "events": [
            {"tool_name": name, "arguments": arguments, "result": returned, "error": None}
            for name, arguments, returned in TOOL_CALLS_WITH_A_FAILED_UPDATE
        ],
    }


def test_the_target_reports_the_time_of_its_request_and_the_tool_call_count_as_resource_measurements(project, setup):
    # The clock is read just before the request is sent and when the response has been read: 121.5 - 100 = 21.5.
    readings = iter([100.0, 121.5])
    target = FoundryHostedAgentTarget(
        project_endpoint=project.endpoint,
        agent_name="todo-hosted",
        credential=lambda: project.token,
        compose_input=setup.instruction,
        clock=lambda: next(readings),
    )

    attempt = one_attempt_with_a_failed_update(project, setup, agent_target=target)

    elapsed, tool_calls = attempt["resource_measurements"]
    assert (elapsed["resource_type"], elapsed["value"], elapsed["unit"]) == ("elapsed_time", 21.5, "seconds")
    assert "measured by the agent target" in elapsed["boundary"]
    assert (tool_calls["resource_type"], tool_calls["value"], tool_calls["unit"]) == ("tool_calls", 5, "calls")
    assert "counted by the agent target" in tool_calls["boundary"]


def test_each_attempt_records_the_token_usage_the_platform_reported(project, setup):
    attempt = one_attempt_with_a_failed_update(project, setup)

    assert attempt["invocation"]["token_usage"] == {
        "input_tokens": 9000,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 400,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 9400,
    }
    assert attempt["invocation"]["token_usage_missing"] is None


def test_token_usage_the_platform_did_not_report_is_missing_with_a_reason_and_not_zero(setup):
    agent = FakeHostedAgent(FakeTodoHostedAgent(todo_tools, usage=None))
    with fake_foundry_project({"todo-hosted": agent}) as project:
        attempt = one_attempt_with_a_failed_update(project, setup)

    assert attempt["execution_status"] == "completed"
    assert attempt["invocation"]["token_usage"] is None
    assert attempt["invocation"]["token_usage_missing"] == "the response of the hosted agent carried no usage"


def test_an_agent_that_reports_completion_after_a_failed_tool_call_is_scored_from_service_evidence(project, setup):
    attempt = one_attempt_with_a_failed_update(project, setup)

    # The hosted agent answered that everything was done, and the platform reported the response as completed.
    assert project.agents["todo-hosted"].respond.ANSWER == "All five operations are done. target is now done."
    assert attempt["invocation"]["status"] == "completed"
    assert attempt["execution_status"] == "completed"
    # The service recorded the update as failed and target as not done.
    assert attempt["observed_condition"] == {
        "met": True,
        "source": "events of the to-do service",
        "observations": {"faulted_calls": ["update"]},
    }
    assert attempt["task_outcome"] == {"success": False, "source": "to-do service events and before/after state"}
    assert setup.evidence[attempt["attempt_id"]]["after"]["target"]["done"] is False


class FailsOnce:
    """A hosted agent whose second invocation is answered with `failure` instead of doing the Task."""

    def __init__(self, failure):
        self._failure = failure
        self._works = FakeTodoHostedAgent(todo_tools)
        self._invocations = 0

    def __call__(self, request):
        self._invocations += 1
        return self._failure if self._invocations == 2 else self._works(request)


def three_attempts_of_which_the_second_invocation_fails(setup, failure):
    with fake_foundry_project({"todo-hosted": FakeHostedAgent(FailsOnce(failure))}) as project:
        return run_sweep(
            experiments=[setup.experiment("todo", 3, ALL_TOOLS_WORK, AGENT)],
            agent_target=hosted_agent(project, setup),
            metrics=["outcome_consistency"],
        )


def test_an_http_error_from_the_agent_endpoint_is_an_execution_failure_and_not_a_task_failure(setup):
    # Two successes and one failed invocation: p = 2/2 and the value is 1.
    # Scoring the failed invocation as a task failure would give (2 * 2/3 - 1)^2 = 0.111...
    error = {"error": {"code": "server_error", "message": "The agent container did not respond."}}

    scorecard = three_attempts_of_which_the_second_invocation_fails(setup, (500, error))

    failed = scorecard["attempts"][1]
    assert failed["execution_status"] == "failed"
    assert failed["task_outcome"] is None
    assert failed["execution_error"] == (
        "InvocationFailed: the hosted agent endpoint answered HTTP 500: "
        '{"error": {"code": "server_error", "message": "The agent container did not respond."}}'
    )
    assert failed["invocation"]["http_status"] == 500
    # The look-up of the agent, the first Attempt, then this one: the project's third request.
    assert failed["invocation"]["request_ids"] == {
        "x-request-id": "synthetic-request-3",
        "apim-request-id": "synthetic-apim-3",
    }
    assert failed["invocation"]["error"] == {"code": "server_error", "message": "The agent container did not respond."}
    assert failed["invocation"]["response_id"] is None
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["counts"]["attempts"]["excluded"] == [
        {"reason": "execution status is not completed", "count": 1, "ids": ["todo/all-tools-work/attempt-2"]},
    ]


def response_that_did_not_succeed(status, **reported):
    """The second response of the hosted agent, as the platform returns one that carries a failure."""
    return {**completed_response("todo-hosted", 2, [], ""), "status": status, **reported}


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            response_that_did_not_succeed("failed", error={"code": "server_error", "message": "The model did not answer."}),
            "the response caresp_synthetic2 of the hosted agent ended with status 'failed': "
            '{"code": "server_error", "message": "The model did not answer."}',
        ),
        (
            response_that_did_not_succeed("incomplete", incomplete_details={"reason": "max_output_tokens"}),
            "the response caresp_synthetic2 of the hosted agent ended with status 'incomplete': "
            '{"reason": "max_output_tokens"}',
        ),
        (
            response_that_did_not_succeed("completed", error={"code": "server_error", "message": "A tool crashed."}),
            "the response caresp_synthetic2 of the hosted agent has status 'completed' and carries an error: "
            '{"code": "server_error", "message": "A tool crashed."}',
        ),
    ],
    ids=["failed", "incomplete", "completed with an error"],
)
def test_a_response_that_carries_a_failure_is_an_execution_failure_whatever_its_status(setup, response, reason):
    scorecard = three_attempts_of_which_the_second_invocation_fails(setup, (200, response))

    failed = scorecard["attempts"][1]
    assert failed["execution_status"] == "failed"
    assert failed["execution_error"] == f"InvocationFailed: {reason}"
    assert failed["task_outcome"] is None
    # What the platform returned for the failed invocation is kept, its token usage included.
    assert failed["invocation"]["response_id"] == "caresp_synthetic2"
    assert failed["invocation"]["status"] == response["status"]
    assert failed["invocation"]["token_usage"]["total_tokens"] == 9400
    assert metric(scorecard)["value"] == 1


def test_an_answer_that_is_not_a_response_is_an_execution_failure(setup):
    scorecard = three_attempts_of_which_the_second_invocation_fails(setup, (200, "<html>Bad gateway</html>"))

    failed = scorecard["attempts"][1]
    assert failed["execution_status"] == "failed"
    assert failed["execution_error"] == (
        "InvocationFailed: the hosted agent endpoint answered HTTP 200 with a body that is not a response: "
        "<html>Bad gateway</html>"
    )
    assert failed["task_outcome"] is None
    assert metric(scorecard)["value"] == 1


def test_an_endpoint_that_cannot_be_reached_is_an_execution_failure(setup):
    with fake_foundry_project({}) as project:
        pass
    # Nothing listens at the project's endpoint any more.
    scorecard = run_sweep(
        experiments=[setup.experiment("todo", 1, ALL_TOOLS_WORK, AGENT)],
        agent_target=hosted_agent(project, setup),
        metrics=["outcome_consistency"],
    )

    (attempt,) = scorecard["attempts"]
    assert attempt["execution_status"] == "failed"
    assert attempt["execution_error"].startswith("InvocationFailed: the Foundry project could not be reached: ")
    assert attempt["task_outcome"] is None
    assert metric(scorecard)["value"] is None


def test_a_hosted_agent_is_not_asked_for_a_confidence_rating(project, setup):
    attempt = one_attempt_with_a_failed_update(project, setup, metrics=["brier_based_score"])

    assert attempt["confidence"] == {
        "rating": None,
        "normalized": None,
        "original_response": None,
        "unavailable_reason": (
            "the agent target does not support an isolated follow-up: a follow-up in which a Foundry hosted "
            "agent answers in the conversation of the Attempt and cannot act has not been verified"
        ),
    }
    # The one request the agent received was the Attempt itself.
    assert len(project.agents["todo-hosted"].invocations) == 1


def test_an_agent_that_does_not_answer_within_the_time_limit_is_an_execution_failure(setup):
    def answers_after_a_second(request):
        time.sleep(1.0)
        return 200, completed_response("todo-hosted", 1, [], "Done.")

    with fake_foundry_project({"todo-hosted": FakeHostedAgent(answers_after_a_second)}) as project:
        target = FoundryHostedAgentTarget(
            project_endpoint=project.endpoint,
            agent_name="todo-hosted",
            credential=lambda: project.token,
            compose_input=setup.instruction,
            timeout_seconds=0.2,
        )
        (attempt,) = run_sweep(
            experiments=[setup.experiment("todo", 1, ALL_TOOLS_WORK, AGENT)],
            agent_target=target,
            metrics=["outcome_consistency"],
        )["attempts"]

    assert attempt["execution_status"] == "failed"
    assert attempt["execution_error"] == "InvocationFailed: the hosted agent did not answer within 0.2 seconds"
    assert attempt["task_outcome"] is None


def test_a_hosted_agent_that_does_not_serve_the_responses_protocol_is_reported_as_unsupported_and_not_invoked(setup):
    agent = FakeHostedAgent(FakeTodoHostedAgent(todo_tools), protocols=("invocations",))
    with fake_foundry_project({"todo-hosted": agent}) as project:
        scorecard = run_sweep(
            experiments=[setup.experiment("todo", 2, ALL_TOOLS_WORK, AGENT)],
            agent_target=hosted_agent(project, setup),
            metrics=["outcome_consistency"],
        )

    for attempt in scorecard["attempts"]:
        assert attempt["execution_status"] == "failed"
        assert attempt["execution_error"] == (
            "UnsupportedAgent: hosted agent 'todo-hosted' serves the protocols ['invocations'] and not the "
            "Responses protocol. Only hosted agents that serve the Responses protocol are supported, so it was "
            "not invoked"
        )
        assert attempt["task_outcome"] is None
    assert agent.invocations == []
    assert [evidence["events"] for evidence in setup.evidence.values()] == [[], []]
    assert metric(scorecard)["value"] is None


def test_an_agent_that_is_not_a_hosted_agent_is_reported_as_unsupported_and_not_invoked(setup):
    agent = FakeHostedAgent(FakeTodoHostedAgent(todo_tools), kind="prompt")
    with fake_foundry_project({"todo-prompt": agent}) as project:
        (attempt,) = run_sweep(
            experiments=[setup.experiment("todo", 1, ALL_TOOLS_WORK, AGENT)],
            agent_target=hosted_agent(project, setup, agent_name="todo-prompt"),
            metrics=["outcome_consistency"],
        )["attempts"]

    assert attempt["execution_status"] == "failed"
    assert attempt["execution_error"] == (
        "UnsupportedAgent: agent 'todo-prompt' is of kind 'prompt' and not a hosted agent, so it was not invoked"
    )
    assert agent.invocations == []


def test_an_agent_the_project_does_not_have_is_an_execution_failure_that_says_so(project, setup):
    (attempt,) = run_sweep(
        experiments=[setup.experiment("todo", 1, ALL_TOOLS_WORK, AGENT)],
        agent_target=hosted_agent(project, setup, agent_name="todo-hosed"),
        metrics=["outcome_consistency"],
    )["attempts"]

    assert attempt["execution_status"] == "failed"
    assert attempt["execution_error"] == (
        "InvocationFailed: agent 'todo-hosed' could not be looked up in the Foundry project: HTTP 404: "
        '{"error": {"code": "not_found", "message": "Agent todo-hosed was not found."}}'
    )


def test_an_experiment_can_declare_the_agent_configuration_the_project_reports(project, setup):
    target = hosted_agent(project, setup)

    scorecard = run_sweep(
        experiments=[setup.experiment("todo", 1, ALL_TOOLS_WORK, target.agent_configuration())],
        agent_target=target,
        metrics=["outcome_consistency"],
    )

    # The hosted agent's settings, which the project returns with its details, are not copied.
    assert scorecard["experiments"][0]["agent_configuration"] == {
        "agent_target": "foundry_hosted_agent",
        "project_endpoint": project.endpoint,
        "agent": "todo-hosted",
        "kind": "hosted",
        "protocols": ["responses"],
        "latest_version": "1",
        "version_selection_rules": [{"type": "FixedRatio", "agent_version": "1", "traffic_percentage": 100}],
    }
    assert scorecard["attempts"][0]["execution_status"] == "completed"


def test_the_configuration_of_an_unsupported_agent_is_refused_before_any_attempt(setup):
    agent = FakeHostedAgent(FakeTodoHostedAgent(todo_tools), protocols=("invocations",))
    with fake_foundry_project({"todo-hosted": agent}) as project:
        with pytest.raises(UnsupportedAgent, match="serves the protocols \\['invocations'\\] and not the Responses"):
            hosted_agent(project, setup).agent_configuration()

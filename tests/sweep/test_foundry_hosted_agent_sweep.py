"""Evaluator sweep seam: Attempts made against a Foundry hosted agent, from customer code.

The sweep is driven through its public entry point with the Foundry hosted agent target.
The target talks to a fake Foundry project on this machine, whose hosted agent works on
the fixture's to-do service, also on this machine. Every assertion is on the
machine-readable scorecard. Expected values are calculated by hand.
"""

import pytest

from live_fixture import local_service
from live_fixture.hosted_agent import todo_tools
from reliability_evaluators.agent_target.foundry_hosted import (
    NATIVE_TOOL_CALLS,
    FoundryHostedAgentTarget,
    native_tool_calls,
)
from reliability_evaluators.sweep import ActionEvidenceDeclaration, run_sweep
from tests.fake_foundry_hosted import FakeHostedAgent, FakeTodoHostedAgent, fake_foundry_project
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

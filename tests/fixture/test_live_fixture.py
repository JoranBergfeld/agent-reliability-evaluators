"""Live Foundry verification of the fixture itself. Opt-in, creates billable Azure resources.

Run with `uv run pytest -m live tests/fixture -s`. Set ARE_LIVE_FIXTURE_GROUP to an already
provisioned resource group to reuse it; that group is then left in place for you to tear down.
"""

import json
import os
import uuid

import pytest

from live_fixture import LiveFixtureError, connect, provision, strict_task_outcome, task_prompt, teardown
from live_fixture.hosted_agent import todo_tools

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_FIXTURE_GROUP")
    fixture = connect(reused) if reused else provision(ticket="42")
    print(json.dumps({"fixture": fixture.summary()}))
    try:
        yield fixture
    finally:
        if not reused:
            teardown(fixture.resource_group)


def run_attempt(live, agent, failing_tool=None):
    """One Attempt of the fixture Task. Returns the service evidence, not the agent's account of it."""
    if agent == "hosted" and live.hosted_agent is None:
        pytest.fail(f"The hosted agent was not deployed: {live.hosted_agent_error}")
    attempt_id = f"{agent}-{failing_tool or 'nominal'}-{uuid.uuid4().hex[:8]}"
    live.service.prepare_attempt(attempt_id)
    live.service.set_failing_tool(attempt_id, failing_tool)
    try:
        if agent == "prompt":
            response = live.project_request("POST", "/openai/v1/responses", {
                "input": task_prompt(attempt_id),
                "agent_reference": {"type": "agent_reference", "name": live.prompt_agent},
            })
        else:
            response = live.project_request(
                "POST", f"/agents/{live.hosted_agent}/endpoint/protocols/openai/responses?api-version=v1",
                {"input": task_prompt(attempt_id), "stream": False},
            )
        invocation = {"status": response.get("status"), "output_types": [
            item.get("type") for item in response.get("output", [])]}
    except LiveFixtureError as error:
        invocation = {"error": str(error)}
    evidence = live.service.evidence(attempt_id)
    print(json.dumps({
        "attempt_id": attempt_id, "invocation": invocation, "strict_task_outcome": strict_task_outcome(evidence),
        "events": [[e["tool"], e["todo_id"], e["status"], e["fault_injected"]] for e in evidence["events"]],
        "after": evidence["after"],
    }))
    return evidence


@pytest.mark.parametrize("agent", ["prompt", "hosted"])
def test_agent_reaches_the_service_without_a_fault(live, agent):
    evidence = run_attempt(live, agent)

    assert any(200 <= event["status"] < 300 for event in evidence["events"])
    assert not any(event["fault_injected"] for event in evidence["events"])
    assert evidence["before"]["unrelated"] == {"id": "unrelated", "title": "Synthetic unrelated", "done": False}


@pytest.mark.parametrize("agent", ["prompt", "hosted"])
def test_failing_tool_switched_for_one_attempt_is_observed_in_service_events(live, agent):
    evidence = run_attempt(live, agent, failing_tool="update")

    faults = [event for event in evidence["events"] if event["fault_injected"]]
    assert faults
    assert all((event["tool"], event["status"]) == ("update", 503) for event in faults)
    assert evidence["after"]["target"]["done"] is False
    assert strict_task_outcome(evidence)["success"] is False


def test_deployed_service_rejects_a_read_without_an_item_identifier(live, monkeypatch):
    monkeypatch.setenv("TODO_SERVICE_URL", live.service.url)
    monkeypatch.setenv("TODO_API_KEY", live.service.api_key)
    attempt_id = f"synthetic-call-{uuid.uuid4().hex[:8]}"
    live.service.prepare_attempt(attempt_id)

    rejected = json.loads(todo_tools.read_todo(attempt_id, ""))

    assert rejected == {"status": 400, "body": {"error": "todo_id is required for read"}}
    assert [(event["tool"], event["todo_id"], event["status"])
            for event in live.service.evidence(attempt_id)["events"]] == [("read", None, 400)]

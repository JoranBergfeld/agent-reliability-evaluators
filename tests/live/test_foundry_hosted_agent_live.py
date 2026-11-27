"""Live Foundry verification of the hosted agent target. Opt-in, creates billable Azure resources.

Run with `uv run pytest -m live tests/live/test_foundry_hosted_agent_live.py -s`. It provisions the
disposable fixture, runs an Evaluator sweep against its hosted agent and tears the fixture down.
Set ARE_LIVE_FIXTURE_GROUP to an already provisioned resource group to reuse it; that group is
then left in place for you to tear down. Synthetic data only.
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest

import live_fixture
from live_fixture import LiveFixtureError, connect, provision, teardown
from reliability_evaluators.agent_target.foundry_hosted import AzureCliCredential, FoundryHostedAgentTarget
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import Task, run_sweep
from tests.todo_experiment import ALL_TOOLS_WORK, TodoSetup, update_fails_on

pytestmark = pytest.mark.live

SUBSCRIPTION = "Sandbox"
INVOCATIONS_AGENT = "todo-invocations"
# The Task's five steps, as the service records a successful call: the tool and the item.
FIVE_STEPS = [["read", "target"], ["create", "scratch"], ["read", "scratch"], ["delete", "scratch"], ["update", "target"]]
UNRELATED = {"id": "unrelated", "title": "Synthetic unrelated", "done": False}


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_FIXTURE_GROUP")
    fixture = connect(reused) if reused else provision(ticket="44")
    print(json.dumps({"fixture": fixture.summary(), "reused": bool(reused)}))
    try:
        yield fixture
    finally:
        if not reused:
            purged = teardown(fixture.resource_group)
            # Asked again here, apart from the teardown's own query.
            exists = _az_json("group", "exists", "--name", fixture.resource_group)
            print(json.dumps({"teardown": {
                "resource_group": fixture.resource_group, "exists_afterwards": exists, "foundry_account": purged}}))
            assert exists is False


def _az_json(*arguments):
    result = subprocess.run(
        [shutil.which("az"), *arguments, "--subscription", SUBSCRIPTION, "--only-show-errors", "-o", "json"],
        text=True, capture_output=True, timeout=300, check=True,
    )
    return json.loads(result.stdout)


class Recording:
    """Passes every invocation on to the agent target, and notes which Attempts the agent answered."""

    def __init__(self, target):
        self._target = target
        self.isolated_follow_up = target.isolated_follow_up
        self.answered: dict[str, bool] = {}

    def invoke(self, request):
        self.answered[request.attempt_id] = False
        response = self._target.invoke(request)
        self.answered[request.attempt_id] = True
        return response


def hosted_agent_target(live, agent_name, compose_input=None):
    return FoundryHostedAgentTarget(
        project_endpoint=live.project_endpoint,
        agent_name=agent_name,
        credential=AzureCliCredential(subscription=SUBSCRIPTION),
        compose_input=compose_input,
    )


def succeeded(evidence):
    """The fixture's strict success, decided here from what the service recorded and not by the sweep."""
    done = [[event["tool"], event["todo_id"]] for event in evidence["events"] if 200 <= event["status"] < 300]
    after = evidence["after"]
    return (
        done == FIVE_STEPS
        and after.get("target", {}).get("done") is True
        and "scratch" not in after
        and after.get("unrelated") == UNRELATED
    )


def independent_outcome_consistency(evidence, answered, update_fails_in):
    """Outcome consistency calculated here, from the service's evidence of every Attempt.

    An Attempt counts when the agent answered and the service shows the condition it was meant
    to run under: a failed update exactly in the Attempts named in `update_fails_in`. Per Task
    with at least two such Attempts: (2p - 1)^2 for its share p of successes. Then the mean
    over those Tasks. Returns the value, or None when no Task has two, and the outcomes counted.
    """
    counted: dict[str, dict[str, bool]] = {}
    for attempt_id, recorded in evidence.items():
        update_failed = any(event["fault_injected"] for event in recorded["events"])
        if answered.get(attempt_id) and update_failed == (attempt_id in update_fails_in):
            counted.setdefault(attempt_id.split("/")[0], {})[attempt_id] = succeeded(recorded)
    per_task = [
        (2 * sum(outcomes.values()) / len(outcomes) - 1) ** 2 for outcomes in counted.values() if len(outcomes) >= 2
    ]
    return (sum(per_task) / len(per_task) if per_task else None), counted


def test_outcome_consistency_of_the_live_hosted_agent_matches_an_independent_calculation(live):
    if live.hosted_agent is None:
        pytest.fail(f"The hosted agent was not deployed: {live.hosted_agent_error}")
    setup = TodoSetup(live.service, run_id=f"live-{uuid.uuid4().hex[:8]}")
    target = Recording(hosted_agent_target(live, live.hosted_agent, setup.instruction))
    agent = hosted_agent_target(live, live.hosted_agent).agent_configuration()
    update_fails_in = {
        "todo-flaky-update/update-fails-intermittently/attempt-2",
        "todo-flaky-update/update-fails-intermittently/attempt-4",
    }

    scorecard = run_sweep(
        experiments=[
            setup.experiment("todo", 3, ALL_TOOLS_WORK, agent),
            setup.experiment("todo-flaky-update", 5, update_fails_on(2, 4), agent),
        ],
        agent_target=target,
        metrics=["outcome_consistency"],
    )

    expected, counted = independent_outcome_consistency(setup.evidence, target.answered, update_fails_in)
    (outcome_consistency,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == "outcome_consistency"]
    attempts = {attempt["attempt_id"]: attempt for attempt in scorecard["attempts"]}
    print(json.dumps({
        "outcome_consistency": {
            "scorecard": {key: outcome_consistency[key] for key in ("state", "value", "reason", "counts")},
            "independent": {"value": expected, "outcomes_counted": counted},
        },
        "agent_configuration": agent,
        "attempts": [
            {
                "attempt_id": attempt_id,
                "execution_status": attempt["execution_status"],
                "execution_error": attempt["execution_error"],
                "task_outcome": attempt["task_outcome"],
                "observed_condition": attempt["observed_condition"],
                "resource_measurements": [
                    [measured["resource_type"], measured["value"]] for measured in attempt["resource_measurements"]
                ],
                "invocation": attempt["invocation"] and {
                    **attempt["invocation"],
                    "tool_calls": [call["name"] for call in attempt["invocation"]["tool_calls"] or []],
                },
                "service_events": [
                    [event["tool"], event["todo_id"], event["status"], event["fault_injected"]]
                    for event in setup.evidence[attempt_id]["events"]
                ],
            }
            for attempt_id, attempt in attempts.items()
        ],
    }))

    assert len(attempts) == 8 and set(attempts) == set(setup.evidence)
    # The value in the scorecard is the one calculated here from the service's evidence.
    if expected is None:
        assert outcome_consistency["state"] == "unavailable"
    else:
        assert outcome_consistency["state"] == "available"
        assert outcome_consistency["value"] == pytest.approx(expected, abs=1e-12)
    assert sorted(outcome_consistency["evidence"]["attempt_ids"]) == sorted(
        attempt_id for outcomes in counted.values() if len(outcomes) >= 2 for attempt_id in outcomes
    )
    # Every Attempt that counts has the task outcome the service's evidence gives it, whatever the agent answered.
    for outcomes in counted.values():
        for attempt_id, success in outcomes.items():
            assert attempts[attempt_id]["execution_status"] == "completed"
            assert attempts[attempt_id]["task_outcome"]["success"] is success
    # An update that failed leaves the Task failed, and the platform still reported the response as completed.
    failed_updates = [attempt_id for attempt_id in update_fails_in if attempt_id in counted.get("todo-flaky-update", {})]
    assert failed_updates, "no Attempt met the failing update, so the check showed nothing about a failed tool call"
    for attempt_id in failed_updates:
        assert attempts[attempt_id]["invocation"]["status"] == "completed"
        assert attempts[attempt_id]["task_outcome"]["success"] is False
    # Each answered Attempt is identifiable on the platform, and carries what the platform exposed.
    answered = [attempts[attempt_id] for attempt_id, was_answered in target.answered.items() if was_answered]
    response_ids = [attempt["invocation"]["response_id"] for attempt in answered]
    assert all(response_ids) and len(set(response_ids)) == len(response_ids)
    for attempt in answered:
        invocation = attempt["invocation"]
        assert invocation["metadata"] == {"attempt_id": attempt["attempt_id"], "task_id": attempt["task_id"]}
        assert invocation["agent"]["name"] == live.hosted_agent
        assert invocation["request_ids"]
        assert invocation["token_usage"]["total_tokens"] > 0
        tool_calls = [measured for measured in attempt["resource_measurements"] if measured["resource_type"] == "tool_calls"]
        assert [measured["value"] for measured in tool_calls] == [len(invocation["tool_calls"])]


def test_a_live_hosted_agent_declared_with_the_invocations_protocol_is_reported_as_unsupported(live):
    declare_invocations_agent(live)

    scorecard = run_sweep(
        tasks=[
            Task(
                task_id="synthetic",
                prompt="Synthetic probe. Do nothing.",
                success_rule=lambda attempt, response: TaskOutcome(success=False, source="never decided"),
                attempts=1,
            )
        ],
        agent_target=hosted_agent_target(live, INVOCATIONS_AGENT),
        metrics=["outcome_consistency"],
    )

    (attempt,) = scorecard["attempts"]
    print(json.dumps({"invocations_agent": {key: attempt[key] for key in ("execution_status", "execution_error")}}))
    assert attempt["execution_status"] == "failed"
    assert attempt["execution_error"] == (
        f"UnsupportedAgent: hosted agent '{INVOCATIONS_AGENT}' serves the protocols ['invocations'] and not the "
        "Responses protocol. Only hosted agents that serve the Responses protocol are supported, so it was "
        "not invoked"
    )
    assert attempt["task_outcome"] is None
    assert attempt["invocation"] is None


def declare_invocations_agent(live):
    """Make sure the fixture's project has a hosted agent declared with the Invocations protocol.

    Only its declaration matters here: the target must refuse it from what the project says about
    it. It is created in the fixture's project, so it goes when the fixture is torn down.
    """
    try:
        live.project_request("GET", f"/agents/{INVOCATIONS_AGENT}?api-version=v1")
        return
    except LiveFixtureError as error:
        if not str(error).startswith("HTTP 404"):
            raise
    source = Path(live_fixture.__file__).parent / "hosted_agent"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in ("main.py", "todo_tools.py", "requirements.txt"):
            archive.write(source / filename, filename)
    code = buffer.getvalue()
    declaration = {
        "description": "Disposable synthetic hosted agent declared with the Invocations protocol",
        "definition": {
            "kind": "hosted", "cpu": "0.5", "memory": "1Gi",
            "protocol_versions": [{"protocol": "invocations", "version": "1.0.0"}],
            "code_configuration": {"runtime": "python_3_13", "entry_point": ["python", "main.py"],
                                   "dependency_resolution": "remote_build"},
            "environment_variables": {"PYTHONNOUSERSITE": "1"},
        },
    }
    boundary = uuid.uuid4().hex
    payload = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
        f"Content-Type: application/json\r\n\r\n{json.dumps(declaration)}\r\n"
        f'--{boundary}\r\nContent-Disposition: form-data; name="code"; filename="agent.zip"\r\n'
        "Content-Type: application/zip\r\n\r\n"
    ).encode() + code + f"\r\n--{boundary}--\r\n".encode()
    live.project_request("POST", "/agents?api-version=v1", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "x-ms-agent-name": INVOCATIONS_AGENT, "x-ms-code-zip-sha256": hashlib.sha256(code).hexdigest(),
    }, raw=payload)

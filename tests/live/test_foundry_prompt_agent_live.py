"""Live Foundry verification: the Evaluator sweep on a Foundry prompt agent, from customer code.

Opt-in, creates billable Azure resources. Run with
`uv run pytest -m live tests/live/test_foundry_prompt_agent_live.py -s`. Set
ARE_LIVE_FIXTURE_GROUP to an already provisioned resource group to reuse it; that group is
then left in place for you to tear down.

One sweep is run and every check reads its scorecard, because each Attempt invokes the
deployed agent. The expectations are calculated here from what the to-do service had
recorded when each Attempt ended, read by the fixture's verify control and not by the code
under test.

The experiment declares continuing state: the state is prepared once per condition and
every Attempt starts from what the one before left. The agent is asked to do the whole Task
every time, in a new conversation. Only the first Attempt can leave the service with exactly
the five operations of the Task, so the task outcomes differ without any Attempt being
treated differently.
"""

import json
import os
import uuid

import pytest

from live_fixture import connect, provision, teardown
from reliability_evaluators.agent_target import FoundryProject, FoundryPromptAgentTarget
from reliability_evaluators.sweep import run_sweep
from tests.todo_fixture_sweep import UPDATE_FAILS, TodoFixtureSweep

pytestmark = pytest.mark.live

ATTEMPTS = 5
BASELINE_ATTEMPTS = [f"todo/baseline/attempt-{number}" for number in range(1, ATTEMPTS + 1)]
FAULTED_ATTEMPTS = [f"todo/update-fails/attempt-{number}" for number in range(1, ATTEMPTS + 1)]
THE_TASK = [("read", "target"), ("create", "scratch"), ("read", "scratch"), ("delete", "scratch"), ("update", "target")]
TOOL_OF = {
    "todos_readTodo": "read",
    "todos_createTodo": "create",
    "todos_deleteTodo": "delete",
    "todos_updateTodo": "update",
}


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_FIXTURE_GROUP")
    fixture = connect(reused) if reused else provision(ticket="43")
    print(json.dumps({"fixture": fixture.summary()}))
    try:
        yield fixture
    finally:
        if not reused:
            teardown(fixture.resource_group)


@pytest.fixture(scope="module")
def swept(live):
    """The scorecard of one sweep on the deployed prompt agent, and what the service recorded per Attempt."""
    fixture = TodoFixtureSweep(live.service, state=f"live-{uuid.uuid4().hex[:8]}")
    # No token provider: the identity signed in to the Azure CLI is used, as in a customer's CI.
    target = FoundryPromptAgentTarget(project=FoundryProject(live.project_endpoint), agent_name=live.prompt_agent)
    experiment = fixture.experiment(
        attempts=ATTEMPTS,
        starting_state="continuing",
        variation=UPDATE_FAILS,
        agent_configuration={"agent": live.prompt_agent, "model_deployment": live.model_deployment},
    )
    scorecard = run_sweep(
        experiments=[experiment], agent_target=target, metrics=["outcome_consistency", "fault_robustness"]
    )
    observed = [_observation(attempt, fixture.service_evidence) for attempt in scorecard["attempts"]]
    print(json.dumps({"observed": observed}))
    print(json.dumps({"metrics": [_metric_summary(entry) for entry in scorecard["metrics"] if "value" in entry]}))
    return scorecard, fixture.service_evidence


def _observation(attempt, service_evidence):
    """What one Attempt showed, for the record of the live run. No token, key or subscription identifier."""
    platform = attempt["platform_evidence"] or {}
    events = calls_of(attempt["attempt_id"], service_evidence) if attempt["attempt_id"] in service_evidence else []
    return {
        "attempt_id": attempt["attempt_id"],
        "execution_status": attempt["execution_status"],
        "execution_error": attempt["execution_error"],
        "task_outcome": attempt["task_outcome"],
        "identifiers": platform.get("identifiers"),
        "token_usage": platform.get("token_usage"),
        "token_usage_missing": platform.get("token_usage_missing"),
        "tool_calls": [call["tool_name"] for call in platform.get("tool_calls") or []],
        "service_events_of_the_attempt": [
            [event["tool"], event["todo_id"], event["status"], event["fault_injected"]] for event in events
        ],
        "service_events_since_the_state_was_prepared": len(
            service_evidence.get(attempt["attempt_id"], {}).get("events", [])
        ),
        "elapsed_seconds": [
            round(measured["value"], 1)
            for measured in attempt["resource_measurements"]
            if measured["resource_type"] == "elapsed_time"
        ],
    }


def _metric_summary(entry):
    return {key: entry[key] for key in ("metric_id", "state", "value", "reason")}


def by_id(scorecard):
    return {attempt["attempt_id"]: attempt for attempt in scorecard["attempts"]}


def metric(scorecard, metric_id):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def calls_of(attempt_id, service_evidence):
    """The calls the service received during one Attempt: those it recorded after the Attempt before ended."""
    condition, number = attempt_id.rsplit("-", 1)
    recorded = service_evidence[attempt_id]["events"]
    if number == "1":
        return recorded
    return recorded[len(service_evidence[f"{condition}-{int(number) - 1}"]["events"]) :]


def succeeded_on_the_service(evidence):
    """This check's own reading of the strict task-success rule, from what the service had recorded."""
    operations = [(event["tool"], event["todo_id"]) for event in evidence["events"] if 200 <= event["status"] < 300]
    after = evidence["after"]
    return (
        operations == THE_TASK
        and after["target"]["done"] is True
        and "scratch" not in after
        and after["unrelated"] == {"id": "unrelated", "title": "Synthetic unrelated", "done": False}
    )


def test_every_planned_attempt_was_made_and_its_service_evidence_was_read(swept):
    scorecard, service_evidence = swept

    assert [attempt["attempt_id"] for attempt in scorecard["attempts"]] == BASELINE_ATTEMPTS + FAULTED_ATTEMPTS
    assert sorted(service_evidence) == sorted(BASELINE_ATTEMPTS + FAULTED_ATTEMPTS)


def test_outcome_consistency_matches_an_independent_calculation_from_service_evidence(swept):
    scorecard, service_evidence = swept
    attempts = by_id(scorecard)

    successes = [succeeded_on_the_service(service_evidence[attempt_id]) for attempt_id in BASELINE_ATTEMPTS]
    success_frequency = sum(successes) / len(successes)
    independent_value = (2 * success_frequency - 1) ** 2

    assert [attempts[attempt_id]["execution_status"] for attempt_id in BASELINE_ATTEMPTS] == ["completed"] * ATTEMPTS
    assert [attempts[attempt_id]["task_outcome"]["success"] for attempt_id in BASELINE_ATTEMPTS] == successes
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["evidence"] == {"attempt_ids": BASELINE_ATTEMPTS}
    assert outcome_consistency["value"] == pytest.approx(independent_value, abs=1e-12)
    # Under continuing state only the first Attempt can succeed, so this is not a comparison of 1 with 1.
    assert successes == [True, False, False, False, False]


def test_a_new_conversation_for_every_attempt_did_not_reset_the_state_of_the_service(swept):
    scorecard, service_evidence = swept
    attempts = by_id(scorecard)

    # Every Attempt was a response of its own, and the service kept everything the Attempts before it did.
    recorded = [len(service_evidence[attempt_id]["events"]) for attempt_id in BASELINE_ATTEMPTS]
    assert recorded == sorted(recorded) and recorded[0] < recorded[-1]
    assert service_evidence[BASELINE_ATTEMPTS[-1]]["after"]["target"]["done"] is True
    assert {attempts[attempt_id]["starting_state"] for attempt_id in BASELINE_ATTEMPTS} == {"continuing"}
    for attempt_id in BASELINE_ATTEMPTS[1:]:
        assert attempts[attempt_id]["task_outcome"]["success"] is False


def test_each_completed_attempt_keeps_its_response_identity_its_usage_and_its_ordered_tool_calls(swept, live):
    scorecard, service_evidence = swept
    attempts = by_id(scorecard)

    response_ids = []
    for attempt_id in BASELINE_ATTEMPTS:
        attempt = attempts[attempt_id]
        platform = attempt["platform_evidence"]
        identifiers = platform["identifiers"]
        response_ids.append(identifiers["response_id"])
        assert identifiers["response_id"].startswith("resp_")
        assert identifiers["status"] == "completed"
        assert identifiers["agent_reference"]["name"] == live.prompt_agent
        assert identifiers["agent_reference"]["version"]
        assert identifiers["model"] == live.model_deployment
        assert identifiers["metadata"] == {"attempt_id": attempt_id, "task_id": "todo"}
        assert identifiers["request_id"] and identifiers["apim_request_id"]
        usage = platform["token_usage"]
        assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"] > 0
        assert platform["token_usage_missing"] is None
        # The tool calls the project exposed are the calls the service received during the Attempt, in order.
        received = calls_of(attempt_id, service_evidence)
        assert [TOOL_OF[call["tool_name"]] for call in platform["tool_calls"]] == [event["tool"] for event in received]
        (counted,) = [m for m in attempt["resource_measurements"] if m["resource_type"] == "tool_calls"]
        assert counted["value"] == len(received)
    assert len(set(response_ids)) == ATTEMPTS


def test_an_injected_tool_fault_fails_the_invocation_and_only_the_service_shows_what_happened(swept):
    scorecard, service_evidence = swept
    attempts = by_id(scorecard)

    for attempt_id in FAULTED_ATTEMPTS:
        attempt = attempts[attempt_id]
        faults = [event for event in calls_of(attempt_id, service_evidence) if event["fault_injected"]]
        assert faults and all((event["tool"], event["status"]) == ("update", 503) for event in faults)
        assert attempt["observed_condition"]["met"] is True
        # The caller gets an error and no response. That is an execution failure, never a task failure.
        assert attempt["execution_status"] == "failed"
        assert attempt["task_outcome"] is None
        assert "HTTP 400 tool_user_error" in attempt["execution_error"]
        platform = attempt["platform_evidence"]
        assert platform["identifiers"]["http_status"] == 400
        assert platform["identifiers"]["error_request_id"]
        assert platform["token_usage"] is None
        assert platform["token_usage_missing"] == "the Foundry project answered HTTP 400 and reported no usage"
        assert platform["tool_calls"] is None
    # No Attempt under the fault completed, so fault robustness has nothing to compare and gives no number.
    fault_robustness = metric(scorecard, "fault_robustness")
    assert fault_robustness["state"] == "unavailable"
    assert fault_robustness["value"] is None

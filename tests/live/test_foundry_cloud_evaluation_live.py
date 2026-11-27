"""Live Foundry verification: the Evaluator sweep with Foundry cloud evaluation as the entry point, on both agent types.

Opt-in, creates billable Azure resources. Run with
`uv run pytest -m live tests/live/test_foundry_cloud_evaluation_live.py -s`. Set
ARE_LIVE_FIXTURE_GROUP to an already provisioned resource group to reuse it; that group is
then left in place for you to tear down. Synthetic data only.

One sweep is run per agent type and every check reads its scorecard, because each Attempt is a
cloud evaluation run that invokes the deployed agent. The expectations are calculated here from
what the to-do service had recorded when each Attempt ended, read by the fixture's verify
control and not by the code under test.

The experiment declares continuing state: the state is prepared once per condition and every
Attempt starts from what the one before left. The agent is asked to do the whole Task every
time, in a run and a conversation of its own. Only the first Attempt can leave the service with
exactly the five operations of the Task, so the task outcomes differ without any Attempt being
treated differently, and a run that reset the state would show.
"""

import json
import os
import shutil
import subprocess
import uuid

import pytest

from live_fixture import connect, provision, teardown
from reliability_evaluators.agent_target.foundry_cloud_evaluation import CRITERION, FoundryCloudEvaluationTarget
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryProject
from reliability_evaluators.sweep import Task, run_sweep
from tests.live.test_foundry_hosted_agent_live import INVOCATIONS_AGENT, declare_invocations_agent
from tests.todo_fixture_sweep import UPDATE_FAILS, TodoFixtureSweep

pytestmark = pytest.mark.live

SUBSCRIPTION = "Sandbox"
ATTEMPTS = 3
BASELINE_ATTEMPTS = [f"todo/baseline/attempt-{number}" for number in range(1, ATTEMPTS + 1)]
FAULTED_ATTEMPTS = [f"todo/update-fails/attempt-{number}" for number in range(1, ATTEMPTS + 1)]
THE_TASK = [("read", "target"), ("create", "scratch"), ("read", "scratch"), ("delete", "scratch"), ("update", "target")]
TOOL_OF = {
    "todos_readTodo": "read",
    "todos_createTodo": "create",
    "todos_deleteTodo": "delete",
    "todos_updateTodo": "update",
    "read_todo": "read",
    "create_todo": "create",
    "delete_todo": "delete",
    "update_todo": "update",
}
CALL_TYPE = {"prompt": "openapi_call", "hosted": "function_call"}
RESPONSE_PREFIX = {"prompt": "resp_", "hosted": "caresp_"}


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_FIXTURE_GROUP")
    fixture = connect(reused) if reused else provision(ticket="45")
    print(json.dumps({"fixture": fixture.summary(), "reused": bool(reused)}))
    try:
        yield fixture
    finally:
        if not reused:
            purged = teardown(fixture.resource_group)
            # Asked again here, apart from the teardown's own query.
            result = subprocess.run(
                [shutil.which("az"), "group", "exists", "--name", fixture.resource_group, "--subscription",
                 SUBSCRIPTION, "--only-show-errors", "-o", "json"],
                text=True, capture_output=True, timeout=300, check=True,
            )
            exists = json.loads(result.stdout)
            print(json.dumps({"teardown": {
                "resource_group": fixture.resource_group, "exists_afterwards": exists, "foundry_account": purged}}))
            assert exists is False


def cloud_target(live, agent_name):
    # No token function: the identity signed in to the Azure CLI is used, as in a customer's CI.
    return FoundryCloudEvaluationTarget(project=FoundryProject(live.project_endpoint), agent_name=agent_name)


@pytest.fixture(scope="module", params=["prompt", "hosted"])
def swept(request, live):
    """The agent type, the scorecard of one sweep on that agent through cloud evaluation, and the service's evidence."""
    kind = request.param
    agent_name = live.prompt_agent if kind == "prompt" else live.hosted_agent
    if agent_name is None:
        pytest.fail(f"The hosted agent was not deployed: {live.hosted_agent_error}")
    fixture = TodoFixtureSweep(live.service, state=f"live-{kind}-{uuid.uuid4().hex[:8]}")
    target = cloud_target(live, agent_name)
    experiment = fixture.experiment(
        attempts=ATTEMPTS,
        starting_state="continuing",
        variation=UPDATE_FAILS,
        agent_configuration=target.agent_configuration(),
    )
    scorecard = run_sweep(
        experiments=[experiment], agent_target=target, metrics=["outcome_consistency", "fault_robustness"]
    )
    print(json.dumps({"agent_kind": kind, "agent_configuration": scorecard["experiments"][0]["agent_configuration"]}))
    observed = [_observation(attempt, fixture.service_evidence) for attempt in scorecard["attempts"]]
    print(json.dumps({"agent_kind": kind, "observed": observed}))
    print(json.dumps({
        "agent_kind": kind,
        "metrics": [_metric_summary(entry) for entry in scorecard["metrics"] if "value" in entry],
    }))
    return kind, scorecard, fixture.service_evidence


def _observation(attempt, service_evidence):
    """What one Attempt showed, for the record of the live run. No token, key or subscription identifier."""
    invocation = attempt["invocation"] or {}
    events = calls_of(attempt["attempt_id"], service_evidence) if attempt["attempt_id"] in service_evidence else []
    return {
        "attempt_id": attempt["attempt_id"],
        "execution_status": attempt["execution_status"],
        "execution_error": attempt["execution_error"],
        "task_outcome": attempt["task_outcome"],
        "invocation": {key: value for key, value in invocation.items() if key not in ("tool_calls", "criterion")},
        "tool_calls": [call["name"] for call in invocation.get("tool_calls") or []],
        "last_tool_output": [call["output"] for call in (invocation.get("tool_calls") or [])[-1:]],
        "service_events_of_the_attempt": [
            [event["tool"], event["todo_id"], event["status"], event["fault_injected"]] for event in events
        ],
        "resource_measurements": [
            [measured["resource_type"], measured["value"]] for measured in attempt["resource_measurements"]
        ],
    }


def _metric_summary(entry):
    return {key: entry.get(key) for key in ("metric_id", "state", "value", "reason")}


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


def test_every_planned_attempt_was_invoked_through_cloud_evaluation_and_says_so(swept):
    kind, scorecard, service_evidence = swept

    assert [attempt["attempt_id"] for attempt in scorecard["attempts"]] == BASELINE_ATTEMPTS + FAULTED_ATTEMPTS
    assert sorted(service_evidence) == sorted(BASELINE_ATTEMPTS + FAULTED_ATTEMPTS)
    for attempt in scorecard["attempts"]:
        invocation = attempt["invocation"]
        assert (invocation["entry_point"], invocation["agent_kind"]) == ("foundry_cloud_evaluation", kind)
        assert invocation["criterion"] == CRITERION
    # Repetition is the sweep's: one eval, and a run of its own with one row for every Attempt.
    assert len({attempt["invocation"]["eval_id"] for attempt in scorecard["attempts"]}) == 1
    run_ids = [attempt["invocation"]["run_id"] for attempt in scorecard["attempts"]]
    assert all(run_ids) and len(set(run_ids)) == 2 * ATTEMPTS
    for attempt in scorecard["attempts"]:
        assert attempt["invocation"]["platform_diagnostics"]["result_counts"]["total"] == 1


def test_outcome_consistency_matches_an_independent_calculation_from_service_evidence(swept):
    kind, scorecard, service_evidence = swept
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
    assert successes == [True] + [False] * (ATTEMPTS - 1)


def test_a_run_and_a_conversation_of_its_own_for_every_attempt_did_not_reset_the_state_of_the_service(swept):
    kind, scorecard, service_evidence = swept
    attempts = by_id(scorecard)

    conversations = [attempts[attempt_id]["invocation"]["conversation_id"] for attempt_id in BASELINE_ATTEMPTS]
    assert all(conversations) and len(set(conversations)) == ATTEMPTS
    # The service kept everything the Attempts before it did.
    recorded = [len(service_evidence[attempt_id]["events"]) for attempt_id in BASELINE_ATTEMPTS]
    assert recorded == sorted(recorded) and recorded[0] < recorded[-1]
    assert {attempts[attempt_id]["starting_state"] for attempt_id in BASELINE_ATTEMPTS} == {"continuing"}
    for attempt_id in BASELINE_ATTEMPTS[1:]:
        assert attempts[attempt_id]["task_outcome"]["success"] is False


def test_each_completed_attempt_keeps_the_identifiers_the_usage_and_the_ordered_tool_calls_of_its_run(swept, live):
    kind, scorecard, service_evidence = swept
    attempts = by_id(scorecard)
    agent_name = live.prompt_agent if kind == "prompt" else live.hosted_agent

    response_ids = []
    for attempt_id in BASELINE_ATTEMPTS:
        attempt = attempts[attempt_id]
        invocation = attempt["invocation"]
        response_ids.append(invocation["response_id"])
        assert invocation["response_id"].startswith(RESPONSE_PREFIX[kind])
        assert (invocation["run_status"], invocation["status"], invocation["error"]) == ("completed", "completed", None)
        assert invocation["output_item_id"] == "1"
        assert (invocation["agent"]["name"], invocation["agent"]["id"]) == (
            agent_name,
            f"{agent_name}:{invocation['agent']['version']}",
        )
        # The platform names the model deployment of a prompt agent, and none for a hosted agent.
        assert invocation["model"] == (live.model_deployment if kind == "prompt" else "")
        # The identity of the Attempt travelled with the run's one row, and the output item returned it.
        assert invocation["metadata"] == {"attempt_id": attempt_id, "task_id": "todo"}
        assert invocation["composed_input"] is None
        usage = invocation["token_usage"]
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"] > 0
        assert invocation["token_usage_missing"] is None
        # The run's own usage rows: one for the target, and for a prompt agent one more for its deployment
        # with the same numbers. Each row equals the usage of the run's one item.
        rows = invocation["run_per_model_usage"]
        assert [row["model_name"] for row in rows] == (
            [live.model_deployment, "azure_ai_agent_target"] if kind == "prompt" else ["azure_ai_agent_target"]
        )
        assert {row["total_tokens"] for row in rows} == {usage["total_tokens"]}
        # The tool calls the output item exposed are the calls the service received during the Attempt, in order.
        received = calls_of(attempt_id, service_evidence)
        assert [TOOL_OF[call["name"]] for call in invocation["tool_calls"]] == [event["tool"] for event in received]
        assert {call["type"] for call in invocation["tool_calls"]} == {CALL_TYPE[kind]}
        measured = {m["resource_type"]: m["value"] for m in attempt["resource_measurements"]}
        assert measured["tool_calls"] == len(received)
        # The elapsed time is the latency Foundry reported for the target, in its own unit.
        assert measured["elapsed_time"] == invocation["latency_ms"] > 0
    assert len(set(response_ids)) == ATTEMPTS


def test_the_labels_foundry_gave_each_item_are_kept_as_platform_diagnostics_only(swept):
    kind, scorecard, service_evidence = swept

    for attempt in scorecard["attempts"]:
        diagnostics = attempt["invocation"]["platform_diagnostics"]
        # Foundry labelled every item as passed, whatever became of the Attempt: also the ones whose target failed.
        assert [(result["name"], result["label"], result["passed"]) for result in diagnostics["results"]] == [
            (CRITERION["name"], "pass", True)
        ]
        assert diagnostics["result_counts"]["passed"] == 1
        assert "platform diagnostics" in diagnostics["meaning"]
    # No acceptance criterion was declared, so no metric has an acceptance verdict.
    assert [entry["acceptance"] for entry in scorecard["metrics"]] == [None] * len(scorecard["metrics"])


def test_an_injected_tool_fault_through_cloud_evaluation(swept):
    kind, scorecard, service_evidence = swept
    attempts = by_id(scorecard)

    for attempt_id in FAULTED_ATTEMPTS:
        faults = [event for event in calls_of(attempt_id, service_evidence) if event["fault_injected"]]
        assert faults and all((event["tool"], event["status"]) == ("update", 503) for event in faults)
        assert attempts[attempt_id]["observed_condition"]["met"] is True
    fault_robustness = metric(scorecard, "fault_robustness")
    if kind == "prompt":
        for attempt_id in FAULTED_ATTEMPTS:
            attempt = attempts[attempt_id]
            invocation = attempt["invocation"]
            # The run and its item report completed, and only the item's sample error says the target failed.
            # That is an execution failure, never a task failure.
            assert (invocation["run_status"], invocation["status"]) == ("completed", "completed")
            assert invocation["error"]["code"] == "TARGET_INVOCATION_FAILED"
            assert "tool_user_error" in invocation["error"]["message"]
            assert attempt["execution_status"] == "failed"
            assert attempt["task_outcome"] is None
            assert "carries a target error: TARGET_INVOCATION_FAILED" in attempt["execution_error"]
            assert (invocation["response_id"], invocation["conversation_id"]) == (None, None)
            assert invocation["token_usage"] is None
            assert invocation["token_usage_missing"] == "the output item of the cloud evaluation run carried no usage"
            assert invocation["tool_calls"] == []
            assert attempt["resource_measurements"] == []
        # No Attempt under the fault completed, so fault robustness has nothing to compare and gives no number.
        assert fault_robustness["state"] == "unavailable"
        assert fault_robustness["value"] is None
    else:
        for attempt_id in FAULTED_ATTEMPTS:
            attempt = attempts[attempt_id]
            invocation = attempt["invocation"]
            # A hosted agent answers after a failed tool call, and its item carries no error.
            assert (invocation["run_status"], invocation["status"], invocation["error"]) == (
                "completed",
                "completed",
                None,
            )
            assert attempt["execution_status"] == "completed"
            assert attempt["task_outcome"]["success"] is succeeded_on_the_service(service_evidence[attempt_id])
            assert attempt["task_outcome"]["success"] is False
            # The failed call shows in what the tool returned.
            failed_updates = [
                call for call in invocation["tool_calls"]
                if TOOL_OF[call["name"]] == "update" and call["output"]["status"] == 503
            ]
            assert failed_updates
        # Accuracy under the fault is 0, and under the baseline it is what the service evidence gives.
        baseline = [succeeded_on_the_service(service_evidence[attempt_id]) for attempt_id in BASELINE_ATTEMPTS]
        assert sum(baseline) > 0
        assert fault_robustness["state"] == "available"
        assert fault_robustness["value"] == min(0 / (sum(baseline) / len(baseline)), 1)


def test_a_hosted_agent_declared_with_the_invocations_protocol_is_reported_as_unsupported_and_not_invoked(live):
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
        agent_target=cloud_target(live, INVOCATIONS_AGENT),
        metrics=["outcome_consistency"],
    )

    (attempt,) = scorecard["attempts"]
    print(json.dumps({"invocations_agent": {key: attempt[key] for key in ("execution_status", "execution_error")}}))
    assert attempt["execution_status"] == "failed"
    assert attempt["execution_error"] == (
        f"UnsupportedCombination: hosted agent '{INVOCATIONS_AGENT}' serves the protocols ['invocations'] and not "
        "the Responses protocol. Invoking such an agent through Foundry cloud evaluation has not been verified, "
        "so it was not invoked"
    )
    assert attempt["task_outcome"] is None
    # The entry point is recorded, and no eval and no run were made.
    invocation = attempt["invocation"]
    assert (invocation["entry_point"], invocation["agent_kind"]) == ("foundry_cloud_evaluation", "hosted")
    assert (invocation["eval_id"], invocation["run_id"]) == (None, None)

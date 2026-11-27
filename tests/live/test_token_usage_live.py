"""Live Foundry verification of the token usage a sweep reports, on both agent types and both entry points.

Opt-in, creates billable Azure resources. Run with
`uv run pytest -m live tests/live/test_token_usage_live.py -s`. Set ARE_LIVE_FIXTURE_GROUP to an
already provisioned resource group to reuse it; that group is then left in place for you to tear
down. Synthetic data only.

One sweep is run for each agent type through each entry point, and every check reads the token
usage of its scorecard. Each sweep makes Attempts that end in every way the fixture can make them
end: completed, with a failing tool, retried after a failing tool, retried after a short time
limit, and with the sweep cancelled while a tool fails.

A count in the report is compared with what the platform says when it is asked again, by this
check and not by the code under test: the response read back by its identifier, or the output
item of the run read back from the project. Where the platform keeps nothing to read back, the
check says so instead. Sums are added here from those counts.
"""

import json
import os
import shutil
import subprocess
import uuid

import pytest

from live_fixture import LiveFixtureError, connect, provision, teardown
from reliability_evaluators.agent_target.foundry_cloud_evaluation import FoundryCloudEvaluationTarget
from reliability_evaluators.agent_target.foundry_hosted import FoundryHostedAgentTarget
from reliability_evaluators.agent_target.foundry_prompt_agent import FoundryPromptAgentTarget
from reliability_evaluators.managed_scoring import FoundryManagedScoring, FoundryProject
from reliability_evaluators.sweep import Condition, ConfiguredEvaluator, ExecutionLimits, run_sweep
from tests.live.test_confidence_follow_up_live import RULE
from tests.todo_experiment import ALL_TOOLS_WORK, TodoSetup

pytestmark = pytest.mark.live

SUBSCRIPTION = "Sandbox"
PROMPT, HOSTED = "prompt", "hosted"
CUSTOMER_CODE, CLOUD_EVALUATION = "customer_code", "foundry_cloud_evaluation"

UPDATE_FAILS = Condition(
    condition_id="update-fails",
    description="The update tool of the to-do service answers 503.",
    parameters={"failing_tool": "update"},
)
UPDATE_FAILS_ONCE = Condition(
    condition_id="update-fails-before-the-retry",
    description="The update tool of the to-do service answers 503 in the Attempt and works in its retry.",
    parameters={"failing_tool": "update", "only_before_a_retry": True},
)
UPDATE_FAILS_AND_THE_SWEEP_IS_CANCELLED = Condition(
    condition_id="update-fails-cancelled",
    description="The update tool answers 503, and the caller cancels the sweep while the Attempt is in flight.",
    parameters={"failing_tool": "update", "cancels_the_sweep": True},
)

# The Attempts of a sweep, by what was done to make each end the way it did.
COMPLETED_1 = "completed/all-tools-work/attempt-1"
COMPLETED_2 = "completed/all-tools-work/attempt-2"
FAILING_TOOL = "failing-tool/update-fails/attempt-1"
FAILED_ONCE = "retried/update-fails-before-the-retry/attempt-1"
RETRY_AFTER_THE_FAULT = f"{FAILED_ONCE}/retry-1"
TIME_LIMIT = "time-limit/all-tools-work/attempt-1"
RETRY_AFTER_THE_TIME_LIMIT = f"{TIME_LIMIT}/retry-1"
CANCELLED = "cancelled/update-fails-cancelled/attempt-1"

# Well under what an invocation was seen to take: 6 seconds for the prompt agent, 30 for the
# hosted agent, and a cloud evaluation run half a minute and a minute.
TIME_LIMIT_SECONDS = {
    (PROMPT, CUSTOMER_CODE): 2,
    (HOSTED, CUSTOMER_CODE): 5,
    (PROMPT, CLOUD_EVALUATION): 10,
    (HOSTED, CLOUD_EVALUATION): 15,
}

# The reasons the report gives for a count that is missing, as each was established.
NO_RESPONSE = "the Foundry project returned no response, so no usage was reported"
NOTHING_RECORDED = "the agent target recorded nothing of the invocation"
NO_USAGE_ON_THE_ITEM = "the output item of the cloud evaluation run carried no usage"
RESULT_NOT_READ = "the result of the cloud evaluation run was not read, so no usage is known"

REPORTED = "reported"
# What each Attempt became and what the report says of its invocation: the execution status, and
# `REPORTED` or the reason its count is missing. An Attempt that is not listed was not made.
#
# A failing tool fails the invocation of a prompt agent, and a hosted agent answers after it. So only a
# prompt agent has an Attempt that failed, a retry after a fault, and an Attempt that ended as cancelled.
EXPECTED = {
    (PROMPT, CUSTOMER_CODE): {
        COMPLETED_1: ("completed", REPORTED),
        COMPLETED_2: ("completed", REPORTED),
        FAILING_TOOL: ("failed", NO_RESPONSE),
        FAILED_ONCE: ("retried", NO_RESPONSE),
        RETRY_AFTER_THE_FAULT: ("completed", REPORTED),
        TIME_LIMIT: ("retried", NOTHING_RECORDED),
        RETRY_AFTER_THE_TIME_LIMIT: ("timed_out", NOTHING_RECORDED),
        CANCELLED: ("cancelled", NO_RESPONSE),
    },
    (HOSTED, CUSTOMER_CODE): {
        COMPLETED_1: ("completed", REPORTED),
        COMPLETED_2: ("completed", REPORTED),
        FAILING_TOOL: ("completed", REPORTED),
        FAILED_ONCE: ("completed", REPORTED),
        TIME_LIMIT: ("retried", NOTHING_RECORDED),
        RETRY_AFTER_THE_TIME_LIMIT: ("timed_out", NOTHING_RECORDED),
        CANCELLED: ("completed", REPORTED),
    },
    (PROMPT, CLOUD_EVALUATION): {
        COMPLETED_1: ("completed", REPORTED),
        COMPLETED_2: ("completed", REPORTED),
        FAILING_TOOL: ("failed", NO_USAGE_ON_THE_ITEM),
        FAILED_ONCE: ("retried", NO_USAGE_ON_THE_ITEM),
        RETRY_AFTER_THE_FAULT: ("completed", REPORTED),
        TIME_LIMIT: ("retried", RESULT_NOT_READ),
        RETRY_AFTER_THE_TIME_LIMIT: ("timed_out", RESULT_NOT_READ),
        CANCELLED: ("cancelled", NO_USAGE_ON_THE_ITEM),
    },
    (HOSTED, CLOUD_EVALUATION): {
        COMPLETED_1: ("completed", REPORTED),
        COMPLETED_2: ("completed", REPORTED),
        FAILING_TOOL: ("completed", REPORTED),
        FAILED_ONCE: ("completed", REPORTED),
        TIME_LIMIT: ("retried", RESULT_NOT_READ),
        RETRY_AFTER_THE_TIME_LIMIT: ("timed_out", RESULT_NOT_READ),
        CANCELLED: ("completed", REPORTED),
    },
}


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_FIXTURE_GROUP")
    fixture = connect(reused) if reused else provision(ticket="52")
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


class UsageSetup(TodoSetup):
    """The fixture's controls, with a fault that a retry does not meet and a cancellation of the sweep."""

    cancel_requested = False

    def apply_condition(self, context):
        super().apply_condition(context)
        if context.condition.parameters.get("cancels_the_sweep"):
            # The sweep asks before an Attempt and again when its invocation fails.
            self.cancel_requested = True

    @staticmethod
    def _failing_tool(context):
        parameters = context.condition.parameters
        if parameters.get("only_before_a_retry") and "/retry-" in context.attempt_id:
            return None
        return parameters.get("failing_tool")


def agent_target(live, kind, entry_point, compose_input):
    agent_name = live.prompt_agent if kind == PROMPT else live.hosted_agent
    # No credential: the identity signed in to the Azure CLI is used, as in a customer's CI.
    if entry_point == CLOUD_EVALUATION:
        return FoundryCloudEvaluationTarget(
            project=FoundryProject(live.project_endpoint), agent_name=agent_name, compose_input=compose_input
        )
    if kind == PROMPT:
        return FoundryPromptAgentTarget(
            project_endpoint=live.project_endpoint, agent_name=agent_name, compose_input=compose_input
        )
    return FoundryHostedAgentTarget(
        project_endpoint=live.project_endpoint, agent_name=agent_name, compose_input=compose_input
    )


@pytest.fixture(
    scope="module",
    params=[(PROMPT, CUSTOMER_CODE), (HOSTED, CUSTOMER_CODE), (PROMPT, CLOUD_EVALUATION), (HOSTED, CLOUD_EVALUATION)],
    ids=lambda combination: "-".join(combination),
)
def swept(request, live):
    """The agent type and the entry point, the scorecard of one sweep on them, and what the platform says afterwards."""
    kind, entry_point = combination = request.param
    if kind == HOSTED and live.hosted_agent is None:
        pytest.fail(f"The hosted agent was not deployed: {live.hosted_agent_error}")
    setup = UsageSetup(live.service, run_id=f"usage-{uuid.uuid4().hex[:8]}")
    target = agent_target(live, kind, entry_point, setup.instruction)
    agent = {"agent": live.prompt_agent if kind == PROMPT else live.hosted_agent, "entry_point": entry_point}
    retried_once = ExecutionLimits(max_retries=1)
    stopped_early = ExecutionLimits(attempt_timeout_seconds=TIME_LIMIT_SECONDS[combination], max_retries=1)
    scorecard = run_sweep(
        experiments=[
            setup.experiment("completed", 2, ALL_TOOLS_WORK, agent),
            setup.experiment("failing-tool", 1, UPDATE_FAILS, agent),
            setup.experiment("retried", 1, UPDATE_FAILS_ONCE, agent, retried_once),
            setup.experiment("time-limit", 1, ALL_TOOLS_WORK, agent, stopped_early),
            # Last: once the sweep is cancelled nothing more is started.
            setup.experiment("cancelled", 1, UPDATE_FAILS_AND_THE_SWEEP_IS_CANCELLED, agent),
        ],
        agent_target=target,
        metrics=["outcome_consistency"],
        cancellation=lambda: setup.cancel_requested,
    )
    read_back = {
        attempt["attempt_id"]: _read_back(live, kind, entry_point, attempt["invocation"])
        for attempt in scorecard["attempts"]
    }
    print(json.dumps({
        "agent_kind": kind,
        "entry_point": entry_point,
        "token_usage": {key: scorecard["token_usage"][key] for key in ("sweep", "evaluators", "attempts")},
        "observed": [_observation(attempt, setup.evidence, read_back) for attempt in scorecard["attempts"]],
    }))
    return kind, entry_point, scorecard, read_back


def _read_back(live, kind, entry_point, invocation):
    """What the platform says of one invocation when this check asks it again, or why it says nothing.

    Returns `{"total_tokens": ...}` from the platform's own record, or `{"nothing": reason}`.
    """
    if invocation is None:
        return {"nothing": "nothing identifies the invocation on the platform"}
    try:
        if entry_point == CLOUD_EVALUATION:
            if not invocation["run_id"]:
                return {"nothing": "no run was made"}
            runs = f"/openai/v1/evals/{invocation['eval_id']}/runs/{invocation['run_id']}"
            run = live.project_request("GET", runs)
            items = live.project_request("GET", f"{runs}/output_items?limit=100")["data"]
            usage = [(item.get("sample") or {}).get("usage") for item in items]
            return {
                "run_status": run["status"],
                "items": len(items),
                "run_per_model_usage": run.get("per_model_usage"),
                "total_tokens": usage[0]["total_tokens"] if len(usage) == 1 and usage[0] else None,
            }
        if not invocation["response_id"]:
            return {"nothing": "the platform returned no response identifier"}
        path = (
            f"/openai/v1/responses/{invocation['response_id']}"
            if kind == PROMPT
            else f"/agents/{live.hosted_agent}/endpoint/protocols/openai/responses/{invocation['response_id']}"
            "?api-version=v1"
        )
        return {"total_tokens": live.project_request("GET", path)["usage"]["total_tokens"]}
    except LiveFixtureError as error:
        # Only the status and the start of the answer: an answer can name the subscription.
        return {"nothing": f"the platform answered {str(error)[:160]}"}


def _observation(attempt, service_evidence, read_back):
    """What one Attempt showed, for the record of the live run. No token, key or subscription identifier."""
    invocation = attempt["invocation"] or {}
    kept = (
        "http_status", "status", "run_status", "response_id", "request_ids", "eval_id", "run_id", "output_item_id",
        "conversation_id", "agent_session_id", "model", "served_model", "error", "token_usage", "token_usage_missing",
        "run_per_model_usage", "latency_ms",
    )
    return {
        "attempt_id": attempt["attempt_id"],
        "execution_status": attempt["execution_status"],
        "execution_error": attempt["execution_error"],
        "retry_of": attempt["retry_of"],
        "invocation": {key: invocation[key] for key in kept if key in invocation} or None,
        "tool_calls": [call["name"] for call in invocation.get("tool_calls") or []],
        "service_events": [
            [event["tool"], event["todo_id"], event["status"], event["fault_injected"]]
            for event in service_evidence.get(attempt["attempt_id"], {}).get("events", [])
        ],
        "read_back": read_back[attempt["attempt_id"]],
    }


def usage_by_attempt(scorecard):
    return {entry["attempt_id"]: entry for entry in scorecard["token_usage"]["attempts"]}


def test_every_attempt_is_in_the_usage_with_its_status_and_a_count_or_the_reason_it_has_none(swept):
    kind, entry_point, scorecard, _ = swept
    expected = EXPECTED[kind, entry_point]
    usage = usage_by_attempt(scorecard)

    assert list(usage) == [attempt["attempt_id"] for attempt in scorecard["attempts"]] == list(expected)
    for attempt_id, (execution_status, reported) in expected.items():
        entry = usage[attempt_id]
        invocation = entry["invocation"]
        assert entry["execution_status"] == execution_status, attempt_id
        if reported == REPORTED:
            assert (invocation["basis"], invocation["reason"]) == ("reported", None), attempt_id
            assert type(invocation["total_tokens"]) is int and invocation["total_tokens"] > 0, attempt_id
            assert (entry["coverage"], entry["total_tokens"]) == ("complete", invocation["total_tokens"]), attempt_id
        else:
            # Missing with the established reason. It is not zero, and nothing stands in for it.
            assert invocation == {
                "basis": "missing",
                "total_tokens": None,
                "reason": reported,
                "response_id": None,
                "request_ids": invocation["request_ids"],
                "evaluation_run": invocation["evaluation_run"],
                "source": None,
            }, attempt_id
            assert (entry["coverage"], entry["total_tokens"]) == ("unavailable", None), attempt_id
        # No selected metric uses confidence, so no confidence request was sent: there is no such use.
        assert entry["confidence_follow_up"] is None
    assert usage[RETRY_AFTER_THE_TIME_LIMIT]["retry_of"] == TIME_LIMIT
    if RETRY_AFTER_THE_FAULT in expected:
        assert usage[RETRY_AFTER_THE_FAULT]["retry_of"] == FAILED_ONCE


def test_a_reported_count_is_the_one_the_platform_gives_when_it_is_asked_again(swept):
    kind, entry_point, scorecard, read_back = swept
    usage = usage_by_attempt(scorecard)
    reported = [attempt_id for attempt_id, (_, count) in EXPECTED[kind, entry_point].items() if count == REPORTED]

    for attempt_id in reported:
        assert usage[attempt_id]["invocation"]["total_tokens"] == read_back[attempt_id]["total_tokens"], attempt_id


def test_the_sweep_total_is_the_sum_of_the_reported_counts_and_names_every_count_that_is_missing(swept):
    kind, entry_point, scorecard, read_back = swept
    expected = EXPECTED[kind, entry_point]
    reported = [attempt_id for attempt_id, (_, count) in expected.items() if count == REPORTED]
    missing = [(attempt_id, count) for attempt_id, (_, count) in expected.items() if count != REPORTED]
    known = 0
    for attempt_id in reported:
        known += read_back[attempt_id]["total_tokens"]

    total = scorecard["token_usage"]["sweep"]
    assert total == {
        "coverage": "partial",
        "total_tokens": known,
        "uses_reported": len(reported),
        "uses_missing": len(missing),
        "missing": [
            {"use": "agent_invocation", "attempt_id": attempt_id, "reason": reason} for attempt_id, reason in missing
        ],
    }
    # The Evaluator was given every Attempt and is scored by code in this process, which adds a known zero.
    (evaluator,) = scorecard["token_usage"]["evaluators"]
    assert evaluator["total"] == total
    assert [(use["basis"], use["total_tokens"]) for use in evaluator["scoring"]] == [("no_model_use", 0)]
    assert sorted(evaluator["attempt_ids"]) == sorted(expected)


_THE_AGENTS_OWN_COUNT = (
    "A hosted agent's own code produces its response, so this is the agent's own count: Foundry passes it on, and "
    "it is not checked against what a model deployment metered"
)
_OF_THE_ITEM = "the usage of the output item that Foundry cloud evaluation returned for the Attempt"
# Where the report says a count comes from. Foundry reports the usage of a prompt agent's response itself. A
# hosted agent states its own, which was seen by hand to be about three times what the deployment metered.
SOURCE = {
    (PROMPT, CUSTOMER_CODE): "the usage of the response the Foundry project returned for the prompt agent",
    (HOSTED, CUSTOMER_CODE): f"the usage of the response the hosted agent returned. {_THE_AGENTS_OWN_COUNT}",
    (PROMPT, CLOUD_EVALUATION): f"{_OF_THE_ITEM}: what Foundry reports for the response of the prompt agent",
    (HOSTED, CLOUD_EVALUATION): (
        f"{_OF_THE_ITEM}: what the response of the hosted agent stated. {_THE_AGENTS_OWN_COUNT}"
    ),
}


def test_a_count_says_where_it_comes_from_and_an_attempt_through_cloud_evaluation_names_its_run(swept):
    kind, entry_point, scorecard, _ = swept
    attempts = {attempt["attempt_id"]: attempt for attempt in scorecard["attempts"]}
    usage = usage_by_attempt(scorecard)

    for attempt_id, (_, count) in EXPECTED[kind, entry_point].items():
        use = usage[attempt_id]["invocation"]
        assert use["source"] == (SOURCE[kind, entry_point] if count == REPORTED else None), attempt_id
        if entry_point == CUSTOMER_CODE:
            assert use["evaluation_run"] is None
            continue
        # The run is named whether or not it gave a count, so that a missing count can be looked for on the platform.
        recorded = attempts[attempt_id]["invocation"]
        assert use["evaluation_run"] == {
            "eval_id": recorded["eval_id"],
            "run_id": recorded["run_id"],
            "output_item_id": None if count == RESULT_NOT_READ else "1",
        }, attempt_id
        assert recorded["eval_id"].startswith("eval_") and recorded["run_id"].startswith("evalrun_")
    if entry_point == CLOUD_EVALUATION:
        run_ids = [usage[attempt_id]["invocation"]["evaluation_run"]["run_id"] for attempt_id in usage]
        assert len(set(run_ids)) == len(run_ids)


def test_an_attempt_without_a_count_keeps_the_identifiers_the_platform_returned(swept):
    kind, entry_point, scorecard, _ = swept
    attempts = {attempt["attempt_id"]: attempt for attempt in scorecard["attempts"]}
    usage = usage_by_attempt(scorecard)

    for attempt_id, (_, count) in EXPECTED[kind, entry_point].items():
        invocation = attempts[attempt_id]["invocation"]
        if count == NO_RESPONSE:
            # Only the request identifiers say which request it was.
            assert sorted(usage[attempt_id]["invocation"]["request_ids"]) == ["apim-request-id", "x-request-id"]
            assert invocation["http_status"] == 400 and invocation["error"]["code"] == "tool_user_error"
        elif count == NOTHING_RECORDED:
            assert invocation is None
        elif count == NO_USAGE_ON_THE_ITEM:
            assert invocation["run_id"] and invocation["output_item_id"]
            assert invocation["error"]["code"] == "TARGET_INVOCATION_FAILED"
            assert invocation["run_per_model_usage"] in ([], None)
        elif count == RESULT_NOT_READ:
            assert invocation["run_id"] and invocation["output_item_id"] is None


def test_the_usage_rows_of_a_cloud_evaluation_run_repeat_the_usage_of_its_one_item_and_are_not_added(swept):
    kind, entry_point, scorecard, _ = swept
    if entry_point != CLOUD_EVALUATION:
        pytest.skip("only a cloud evaluation run has usage rows of its own")
    attempts = {attempt["attempt_id"]: attempt for attempt in scorecard["attempts"]}
    usage = usage_by_attempt(scorecard)

    for attempt_id, (_, count) in EXPECTED[kind, entry_point].items():
        if count != REPORTED:
            continue
        rows = attempts[attempt_id]["invocation"]["run_per_model_usage"]
        # A prompt agent's run names the same tokens twice. The report counts the item's usage once.
        assert len(rows) == (2 if kind == PROMPT else 1), attempt_id
        assert {row["total_tokens"] for row in rows} == {usage[attempt_id]["invocation"]["total_tokens"]}, attempt_id


def test_the_confidence_follow_up_of_a_prompt_agent_is_a_use_of_its_own_and_leaves_the_count_of_its_attempt_as_it_was(
    live,
):
    setup = UsageSetup(live.service, run_id=f"usage-{uuid.uuid4().hex[:8]}")
    target = agent_target(live, PROMPT, CUSTOMER_CODE, setup.instruction)

    scorecard = run_sweep(
        experiments=[
            setup.experiment("completed", 2, ALL_TOOLS_WORK, {"agent": live.prompt_agent}, shareable_success_rule=RULE)
        ],
        agent_target=target,
        metrics=["brier_based_score"],
    )

    usage = scorecard["token_usage"]
    print(json.dumps({"confidence_follow_up": {"sweep": usage["sweep"], "attempts": usage["attempts"]}}))
    known = 0
    for entry in usage["attempts"]:
        invocation, follow_up = entry["invocation"], entry["confidence_follow_up"]
        # Two responses, and each count is the one the platform gives for its response when it is asked again,
        # after the follow-up: the follow-up added nothing to the count of the Attempt it continued.
        assert invocation["response_id"] != follow_up["response_id"]
        asked_again = [
            live.project_request("GET", f"/openai/v1/responses/{use['response_id']}")["usage"]["total_tokens"]
            for use in (invocation, follow_up)
        ]
        assert [invocation["total_tokens"], follow_up["total_tokens"]] == asked_again
        assert (follow_up["basis"], follow_up["source"]) == ("reported", SOURCE[PROMPT, CUSTOMER_CODE])
        assert (entry["coverage"], entry["total_tokens"]) == ("complete", asked_again[0] + asked_again[1])
        known += asked_again[0] + asked_again[1]
    # Four uses, each counted once. The Evaluator is scored by code in this process, which adds a known zero.
    assert usage["sweep"] == {
        "coverage": "complete",
        "total_tokens": known,
        "uses_reported": 4,
        "uses_missing": 0,
        "missing": [],
    }


MANAGED = ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")
NO_MANAGED_USAGE = (
    "Foundry reported no usage for the managed run: not on the result of every row, and no per-model usage "
    "row. That is not a report of zero"
)


def test_foundry_managed_scoring_reports_no_usage_for_its_runs_and_the_report_says_missing_and_not_zero(live):
    setup = UsageSetup(live.service, run_id=f"usage-{uuid.uuid4().hex[:8]}")
    target = agent_target(live, PROMPT, CUSTOMER_CODE, setup.instruction)
    project = FoundryProject(live.project_endpoint)

    scorecard = run_sweep(
        experiments=[setup.experiment("completed", 2, ALL_TOOLS_WORK, {"agent": live.prompt_agent})],
        agent_target=target,
        metrics=[MANAGED],
        managed_scoring=FoundryManagedScoring(project=project),
    )

    usage = scorecard["token_usage"]
    (evaluator,) = usage["evaluators"]
    print(json.dumps({"managed_scoring": {"sweep": usage["sweep"], "scoring": evaluator["scoring"]}}))
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == "outcome_consistency"]
    assert (entry["state"], entry["scoring_path"]) == ("available", "foundry_managed")
    # Two runs for a result, and one more for each time Foundry did not grade a row. Each is a use of its own.
    scoring = evaluator["scoring"]
    assert len(scoring) >= 2
    assert len({use["run_id"] for use in scoring}) == len(scoring)
    for use in scoring:
        # Asked again by this check: the run reports no usage row, and no result of its rows reports usage.
        runs = f"/openai/v1/evals/{use['eval_id']}/runs/{use['run_id']}"
        assert live.project_request("GET", runs).get("per_model_usage") in ([], None)
        items = live.project_request("GET", f"{runs}/output_items?limit=100")["data"]
        assert [(result.get("sample") or {}).get("usage") for item in items for result in item["results"]] == [
            None
        ] * len(items)
        assert (use["basis"], use["total_tokens"], use["reason"]) == ("missing", None, NO_MANAGED_USAGE)
    # What the two Attempts used is known, and the scoring is not: a partial sum, never a complete one.
    attempts = usage_by_attempt(scorecard)
    known = 0
    for attempt_id in (COMPLETED_1, COMPLETED_2):
        response_id = attempts[attempt_id]["invocation"]["response_id"]
        known += live.project_request("GET", f"/openai/v1/responses/{response_id}")["usage"]["total_tokens"]
    assert usage["sweep"] == {
        "coverage": "partial",
        "total_tokens": known,
        "uses_reported": 2,
        "uses_missing": len(scoring),
        "missing": [
            {"use": "scoring", "metric_id": "outcome_consistency", "run_id": use["run_id"], "reason": NO_MANAGED_USAGE}
            for use in scoring
        ],
    }

"""Live Foundry verification of the isolated confidence follow-up, on a prompt agent and on a hosted agent.

Opt-in, creates billable Azure resources. Run with
`uv run pytest -m live tests/live/test_confidence_follow_up_live.py -s`. It provisions the
disposable fixture, runs the checks and tears the fixture down. Set ARE_LIVE_FIXTURE_GROUP to an
already provisioned resource group to reuse it; that group is then left in place for you to tear
down. Synthetic data only.

Whether the agent acted is read from the to-do service: the calls it received and its state,
before and after each follow-up. A follow-up in which the agent is asked for its confidence shows
only that no call occurred. So the agent is also told, in a follow-up, to make a call. That shows
whether it can.

Every Attempt is made from customer code. Foundry cloud evaluation as the entry point is not
exercised here.
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import time
import uuid
import zipfile
from fractions import Fraction
from pathlib import Path

import pytest

import live_fixture
from live_fixture import LiveFixtureError, connect, provision, task_prompt, teardown
from live_fixture.provisioning import AGENT_INSTRUCTIONS, MODEL_DEPLOYMENT
from reliability_evaluators.agent_target import AttemptRequest, ConfidenceRequest, InvocationFailed
from reliability_evaluators.agent_target.foundry_hosted import AzureCliCredential, FoundryHostedAgentTarget
from reliability_evaluators.agent_target.foundry_prompt_agent import FoundryPromptAgentTarget
from reliability_evaluators.sweep import run_sweep
from tests.todo_experiment import ALL_TOOLS_WORK, TodoSetup

pytestmark = pytest.mark.live

SUBSCRIPTION = "Sandbox"
PREDICTABILITY = ["calibration", "strict_confidence_discrimination", "brier_based_score"]
# A prompt agent whose definition specifies a tool choice of its own, and a hosted agent whose own
# code drops the options a caller sends. Both are created in the fixture's project, so they go with it.
TOOL_CHOICE_AGENT = "todo-prompt-required"
OWN_OPTIONS_AGENT = "todo-hosted-own-options"
FIVE_STEPS = [["read", "target"], ["create", "scratch"], ["read", "scratch"], ["delete", "scratch"], ["update", "target"]]
UNRELATED = {"id": "unrelated", "title": "Synthetic unrelated", "done": False}
# The fixture's strict rule in words, which the agent is shown when it is asked for its confidence.
RULE = (
    "The task succeeded only if exactly these five operations were done on the to-do service, in this order "
    "and with no other successful operation: read target, create scratch, read scratch, delete scratch, "
    "update target to done. Afterwards target is done, scratch does not exist and unrelated is unchanged."
)
# The confidence question the sweep puts to the agent for that rule, word for word.
QUESTION = (
    "The task above is finished. This is a question about it, not a new task: "
    "do not take any action and do not call any tool.\n\n"
    "The task counts as a success under this rule:\n"
    f"{RULE}\n\n"
    "How likely is it that the task succeeded under this rule? "
    "Answer with one number from 0 to 100 and nothing else. "
    "0 means it certainly did not succeed and 100 means it certainly succeeded. "
    "Decimals are allowed."
)
# The fixture's Task without its last step, so that the strict rule fails whatever the agent does.
WITHOUT_THE_UPDATE = (
    "Read todo target. Create a todo with id scratch and title Temporary, then read scratch and delete scratch. "
    "Perform the operations one at a time in that order, not in parallel. "
    "Only target and scratch may be changed. Do not change or delete unrelated."
)
RECALL = (
    "Which attempt_id were you told to use, and what exact title did the tool return for todo target? "
    "Answer with the two values only."
)
IN_RESPONSE_RATING = " End your answer with the line CONFIDENCE=<a number from 0 to 100 for how likely the task succeeded>."
# A follow-up in which the agent keeps trying to act makes many model calls. Too many in a row are answered 429.
PAUSE_SECONDS = 25


def told_to_act(todo_id):
    return (
        f"Now create a todo with id {todo_id} and title Probe, using the same attempt_id as before. "
        "This is authorized and required."
    )


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_FIXTURE_GROUP")
    fixture = connect(reused) if reused else provision(ticket="53")
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


class Watching:
    """Passes everything on to the agent target, and reads the to-do service around every confidence request.

    `answers` keeps the agent's answer to each Attempt. `around_follow_up` keeps, by Attempt
    identity, what the service had recorded for the Attempt just before the confidence
    request was sent and just after it ended.
    """

    def __init__(self, target, service, setup):
        self._target, self._service, self._setup = target, service, setup
        self.isolated_follow_up = target.isolated_follow_up
        self.answers = {}
        self.around_follow_up = {}

    def invoke(self, request):
        response = self._target.invoke(request)
        self.answers[request.attempt_id] = response.output
        return response

    def elicit_confidence(self, request):
        on_service = self._setup.on_service(request.attempt.attempt_id)
        before = self._service.evidence(on_service)
        try:
            return self._target.elicit_confidence(request)
        finally:
            self.around_follow_up[request.attempt.attempt_id] = (before, self._service.evidence(on_service))


def prompt_agent_target(live, agent_name, compose_input=None):
    return FoundryPromptAgentTarget(
        project_endpoint=live.project_endpoint,
        agent_name=agent_name,
        credential=AzureCliCredential(subscription=SUBSCRIPTION),
        compose_input=compose_input,
    )


def prompt_agent_configuration(live, agent_name):
    return {"agent_target": "foundry_prompt_agent", "agent": agent_name, "model_deployment": live.model_deployment}


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


def independent_predictability(pairs):
    """The three predictability metrics calculated here from (confidence, success) pairs, exactly.

    Brier-based score: one minus the mean of (c - y)^2. Calibration: one minus the sum, over the ten
    bins of width 0.1 that hold a pair, of the bin's share of the pairs times |mean y - mean c|; a
    confidence of 1 is in the last bin. Strict confidence discrimination: the share of
    success/failure pairs in which the success was rated strictly higher. None where a metric has
    nothing to be calculated from.
    """
    if not pairs:
        return {metric_id: None for metric_id in PREDICTABILITY}
    brier = 1 - sum((confidence - int(success)) ** 2 for confidence, success in pairs) / len(pairs)
    bins = {}
    for confidence, success in pairs:
        bins.setdefault(min(int(confidence * 10), 9), []).append((confidence, int(success)))
    calibration = 1 - sum(
        Fraction(len(held), len(pairs))
        * abs(sum(y for _, y in held) / Fraction(len(held)) - sum(c for c, _ in held) / len(held))
        for held in bins.values()
    )
    successes = [confidence for confidence, success in pairs if success]
    failures = [confidence for confidence, success in pairs if not success]
    compared = len(successes) * len(failures)
    strict = Fraction(sum(s > f for s in successes for f in failures), compared) if compared else None
    return {"calibration": calibration, "strict_confidence_discrimination": strict, "brier_based_score": brier}


def input_items(live, response_id):
    """What the project says the model was given as input for a response: the conversation it answered in."""
    listed = live.project_request("GET", f"/openai/v1/responses/{response_id}/input_items?limit=100")
    assert listed.get("has_more") is False
    return listed["data"]


def text_of(item):
    return "".join(part.get("text") or "" for part in item.get("content") or [] if isinstance(part, dict))


def output_text(response):
    return "".join(text_of(item) for item in response["output"] if item.get("type") == "message")


def calls_in(response):
    return [item.get("name") for item in response["output"] if str(item.get("type", "")).endswith("_call")]


def brief(event):
    return [event["tool"], event["todo_id"], event["status"]]


def test_a_live_prompt_agent_rates_each_attempt_in_a_follow_up_during_which_the_service_records_nothing(live):
    setup = TodoSetup(live.service, run_id=f"live-{uuid.uuid4().hex[:8]}")
    watching = Watching(prompt_agent_target(live, live.prompt_agent, setup.instruction), live.service, setup)
    agent = prompt_agent_configuration(live, live.prompt_agent)

    scorecard = run_sweep(
        experiments=[
            setup.experiment("todo", 3, ALL_TOOLS_WORK, agent, shareable_success_rule=RULE),
            setup.experiment(
                "todo-without-update", 2, ALL_TOOLS_WORK, agent, prompt=WITHOUT_THE_UPDATE, shareable_success_rule=RULE
            ),
        ],
        agent_target=watching,
        metrics=PREDICTABILITY,
    )

    attempts = {attempt["attempt_id"]: attempt for attempt in scorecard["attempts"]}
    metrics = {entry["metric_id"]: entry for entry in scorecard["metrics"]}
    outcomes = {attempt_id: succeeded(evidence) for attempt_id, evidence in setup.evidence.items()}
    pairs = {
        attempt_id: (Fraction(attempt["confidence"]["rating"]) / 100, outcomes[attempt_id])
        for attempt_id, attempt in attempts.items()
        if attempt["confidence"]["rating"] is not None
    }
    expected = independent_predictability(list(pairs.values()))
    conversations = {
        attempt_id: input_items(live, attempt["confidence_invocation"]["response_id"])
        for attempt_id, attempt in attempts.items()
        if attempt_id in pairs
    }
    print(json.dumps({
        "prompt_agent_sweep": {
            "metrics": {
                metric_id: {
                    "scorecard": {key: metrics[metric_id][key] for key in ("state", "value", "reason")},
                    "independent": None if expected[metric_id] is None else float(expected[metric_id]),
                }
                for metric_id in PREDICTABILITY
            },
            "attempts": [
                {
                    "attempt_id": attempt_id,
                    "execution_status": attempt["execution_status"],
                    "success_from_service_evidence": outcomes[attempt_id],
                    "confidence": {key: attempt["confidence"][key] for key in ("rating", "original_response", "unavailable_reason")},
                    "attempt_response": attempt["invocation"]["response_id"],
                    "attempt_tool_calls": len(attempt["invocation"]["tool_calls"]),
                    "follow_up": attempt["confidence_invocation"] and {
                        key: attempt["confidence_invocation"][key]
                        for key in (
                            "response_id", "previous_response_id", "tool_choice", "tool_calls", "agent", "status",
                            "http_status", "token_usage", "request_ids", "served_model", "metadata",
                        )
                    },
                    "service_calls_before_and_after_follow_up": [
                        len(evidence["events"]) for evidence in watching.around_follow_up.get(attempt_id, ())
                    ],
                    "input_items_of_follow_up": [
                        [item.get("type"), item.get("role")] for item in conversations.get(attempt_id, ())
                    ],
                }
                for attempt_id, attempt in attempts.items()
            ],
        }
    }))

    assert len(attempts) == 5 and set(attempts) == set(setup.evidence)
    assert all(attempt["execution_status"] == "completed" for attempt in attempts.values())
    usage = {entry["attempt_id"]: entry for entry in scorecard["token_usage"]["attempts"]}
    # The agent was asked once for every Attempt, and every question was answered with a rating.
    assert set(watching.around_follow_up) == set(attempts) == set(pairs)
    for attempt_id, attempt in attempts.items():
        invocation, follow_up = attempt["invocation"], attempt["confidence_invocation"]
        # No tool call and no state change during the follow-up: the service recorded the same before and after.
        before, after = watching.around_follow_up[attempt_id]
        assert before["events"] and after == before
        # The project reports a response of its own that continued the Attempt's, run under the tool choice none.
        assert follow_up["response_id"] and follow_up["response_id"] != invocation["response_id"]
        assert follow_up["previous_response_id"] == invocation["response_id"]
        assert follow_up["tool_choice"] == "none"
        assert follow_up["tool_calls"] == []
        assert follow_up["agent"] == invocation["agent"]
        assert follow_up["metadata"] == {
            "attempt_id": attempt_id, "task_id": attempt["task_id"], "request": "confidence"
        }
        assert follow_up["token_usage"]["total_tokens"] > 0 and follow_up["request_ids"]
        # What the follow-up used is reported with its Attempt, apart from the invocation.
        used = usage[attempt_id]
        assert used["confidence_follow_up"]["basis"] == "reported"
        assert used["confidence_follow_up"]["total_tokens"] == follow_up["token_usage"]["total_tokens"]
        assert used["total_tokens"] == invocation["token_usage"]["total_tokens"] + follow_up["token_usage"]["total_tokens"]
        # The agent was sent the question with the success rule, word for word, and nothing else.
        assert follow_up["input"] == QUESTION
        # The model was given the completed conversation of this Attempt and the question. That is all it
        # was given: nothing of the task outcome and nothing of another Attempt.
        conversation = conversations[attempt_id]
        users = [text_of(item) for item in conversation if item.get("role") == "user"]
        assistants = [text_of(item) for item in conversation if item.get("role") == "assistant"]
        calls = [item for item in conversation if str(item.get("type", "")).endswith("_call")]
        returned = [item for item in conversation if str(item.get("type", "")).endswith("_call_output")]
        assert sorted(users) == sorted([invocation["composed_input"], QUESTION])
        assert assistants and all(said and said in watching.answers[attempt_id] for said in assistants)
        assert sum(len(said) for said in assistants) == len(watching.answers[attempt_id])
        assert len(calls) == len(returned) == len(invocation["tool_calls"]) > 0
        assert len(conversation) == len(users) + len(assistants) + len(calls) + len(returned)
        # The task outcome in the scorecard is the one the service's evidence gives, whatever the agent rated.
        assert attempt["task_outcome"]["success"] is outcomes[attempt_id]
    # Both outcomes occurred, so all three metrics have something to be calculated from.
    assert set(outcomes.values()) == {True, False}
    for metric_id in PREDICTABILITY:
        assert metrics[metric_id]["state"] == "available"
        assert metrics[metric_id]["value"] == pytest.approx(float(expected[metric_id]), abs=1e-12)
        assert sorted(metrics[metric_id]["evidence"]["attempt_ids"]) == sorted(pairs)


def test_a_live_prompt_agent_that_is_told_to_act_in_a_follow_up_calls_no_tool_and_calls_one_without_the_tool_choice(live):
    target = prompt_agent_target(live, live.prompt_agent)
    state = f"live-{uuid.uuid4().hex[:8]}-told-to-act"
    live.service.prepare_attempt(state)
    try:
        request = AttemptRequest(attempt_id=state, task_id="told-to-act", prompt=task_prompt(state))
        response = target.invoke(request)
        after_the_attempt = live.service.evidence(state)
        told = []
        for number in (1, 2, 3):
            time.sleep(PAUSE_SECONDS)
            # Through the target, as the sweep asks, with an instruction to act in the place of the question.
            answered = follow_up(target, request, response, told_to_act(f"probe{number}"))
            told.append({
                "tool_choice": answered.invocation["tool_choice"],
                "tool_calls": answered.invocation["tool_calls"],
                "messages": answered.output[:200],
                "service_after": live.service.evidence(state),
            })
        time.sleep(PAUSE_SECONDS)
        # The control: the same instruction in the same conversation, sent without a tool choice.
        control = live.project_request("POST", "/openai/v1/responses", {
            "input": told_to_act("control"),
            "previous_response_id": response.invocation["response_id"],
            "agent_reference": response.invocation["agent"],
        })
        after_the_control = live.service.evidence(state)
    finally:
        live.service.remove_attempt(state)

    new_calls = [brief(event) for event in after_the_control["events"][len(after_the_attempt["events"]):]]
    print(json.dumps({"prompt_agent_told_to_act": {
        "calls_of_the_attempt": len(after_the_attempt["events"]),
        "with_tool_choice_none": [
            {
                "tool_choice": entry["tool_choice"],
                "tool_calls": entry["tool_calls"],
                "answer_starts": entry["messages"],
                "service_calls": len(entry["service_after"]["events"]),
            }
            for entry in told
        ],
        "control": {"tool_choice": control.get("tool_choice"), "tool_calls": calls_in(control), "new_service_calls": new_calls},
    }}))

    assert len(after_the_attempt["events"]) == 5
    for entry in told:
        assert entry["tool_choice"] == "none" and entry["tool_calls"] == []
        assert entry["service_after"] == after_the_attempt
    # Without the tool choice the agent did act, so the instruction was one it follows when it can.
    assert control["tool_choice"] == "auto"
    assert calls_in(control), "the agent did not act in the control either, so the check showed nothing"
    assert new_calls and all(call[0] == "create" for call in new_calls)


def follow_up(target, request, response, prompt):
    """One follow-up through the target. A follow-up the project answers with HTTP 429 is sent again later."""
    asked = ConfidenceRequest(attempt=request, response=response, success_rule="not shown in this check", prompt=prompt)
    for _ in range(4):
        try:
            return target.elicit_confidence(asked)
        except InvocationFailed as error:
            if "HTTP 429" not in str(error):
                raise
            time.sleep(60)
    return target.elicit_confidence(asked)


def test_a_live_prompt_agent_whose_definition_specifies_a_tool_choice_is_refused_the_follow_up_and_gives_no_rating(live):
    declare_prompt_agent_with_a_tool_choice(live)
    setup = TodoSetup(live.service, run_id=f"live-{uuid.uuid4().hex[:8]}")
    watching = Watching(prompt_agent_target(live, TOOL_CHOICE_AGENT, setup.instruction), live.service, setup)

    scorecard = run_sweep(
        experiments=[
            setup.experiment(
                "todo", 2, ALL_TOOLS_WORK, prompt_agent_configuration(live, TOOL_CHOICE_AGENT), shareable_success_rule=RULE
            )
        ],
        agent_target=watching,
        metrics=PREDICTABILITY,
    )

    metrics = {entry["metric_id"]: entry for entry in scorecard["metrics"]}
    print(json.dumps({"prompt_agent_with_its_own_tool_choice": {
        "attempts": [
            {
                "attempt_id": attempt["attempt_id"],
                "execution_status": attempt["execution_status"],
                "confidence": {key: attempt["confidence"][key] for key in ("rating", "unavailable_reason")},
                "follow_up_http_status": (attempt["confidence_invocation"] or {}).get("http_status"),
                "service_calls_before_and_after_follow_up": [
                    len(evidence["events"]) for evidence in watching.around_follow_up.get(attempt["attempt_id"], ())
                ],
            }
            for attempt in scorecard["attempts"]
        ],
        "metrics": {metric_id: [metrics[metric_id]["state"], metrics[metric_id]["reason"]] for metric_id in PREDICTABILITY},
    }}))

    assert len(scorecard["attempts"]) == 2
    for attempt in scorecard["attempts"]:
        assert attempt["execution_status"] == "completed"
        assert attempt["task_outcome"]["success"] is succeeded(setup.evidence[attempt["attempt_id"]])
        confidence = attempt["confidence"]
        assert (confidence["rating"], confidence["normalized"], confidence["original_response"]) == (None, None, None)
        assert confidence["unavailable_reason"].startswith(
            "the confidence request failed: InvocationFailed: the Foundry project answered HTTP 400: invalid_payload: "
            "ToolChoice must match the agent's tool_choice when agent specifies tool_choice."
        )
        refused = attempt["confidence_invocation"]
        assert refused["http_status"] == 400 and refused["response_id"] is None
        # The project refused the request before the agent ran: the service recorded nothing.
        before, after = watching.around_follow_up[attempt["attempt_id"]]
        assert before["events"] and after == before
    for metric_id in PREDICTABILITY:
        assert (metrics[metric_id]["state"], metrics[metric_id]["value"]) == ("unavailable", None)


def declare_prompt_agent_with_a_tool_choice(live):
    """Make sure the fixture's project has the fixture's prompt agent again, with `tool_choice: required` in its definition."""
    try:
        live.project_request("GET", f"/agents/{TOOL_CHOICE_AGENT}?api-version=v1")
        return
    except LiveFixtureError as error:
        if not str(error).startswith("HTTP 404"):
            raise
    fixture_agent = live.project_request("GET", f"/agents/{live.prompt_agent}/versions/1?api-version=v1")
    live.project_request(
        "POST",
        f"/agents/{TOOL_CHOICE_AGENT}/versions?api-version=v1",
        {"definition": {**fixture_agent["definition"], "tool_choice": "required"}},
    )


def test_a_live_hosted_agent_is_not_asked_and_a_rating_in_its_task_response_is_not_used(live):
    if live.hosted_agent is None:
        pytest.fail(f"The hosted agent was not deployed: {live.hosted_agent_error}")
    setup = TodoSetup(live.service, run_id=f"live-{uuid.uuid4().hex[:8]}")
    target = FoundryHostedAgentTarget(
        project_endpoint=live.project_endpoint,
        agent_name=live.hosted_agent,
        credential=AzureCliCredential(subscription=SUBSCRIPTION),
        # The agent is asked to rate itself inside its task response. That rating must not be used.
        compose_input=lambda request: setup.instruction(request) + IN_RESPONSE_RATING,
    )
    watching = Watching(target, live.service, setup)

    scorecard = run_sweep(
        experiments=[setup.experiment("todo", 2, ALL_TOOLS_WORK, target.agent_configuration(), shareable_success_rule=RULE)],
        agent_target=watching,
        metrics=PREDICTABILITY,
    )

    metrics = {entry["metric_id"]: entry for entry in scorecard["metrics"]}
    print(json.dumps({"hosted_agent_sweep": {
        "attempts": [
            {
                "attempt_id": attempt["attempt_id"],
                "execution_status": attempt["execution_status"],
                "confidence": attempt["confidence"],
                "task_response_ends": watching.answers.get(attempt["attempt_id"], "")[-60:],
            }
            for attempt in scorecard["attempts"]
        ],
        "metrics": {metric_id: [metrics[metric_id]["state"], metrics[metric_id]["reason"]] for metric_id in PREDICTABILITY},
        "confidence_requests_sent": len(watching.around_follow_up),
    }}))

    assert len(scorecard["attempts"]) == 2
    for attempt in scorecard["attempts"]:
        assert attempt["execution_status"] == "completed"
        assert attempt["confidence"] == {
            "rating": None,
            "normalized": None,
            "original_response": None,
            "unavailable_reason": (
                "the agent target does not support an isolated follow-up: the code of a Foundry hosted agent "
                "decides what it does with a request, so a caller cannot ensure that the agent does not act while "
                "it answers a follow-up"
            ),
        }
        assert attempt["confidence_invocation"] is None
        # The agent did put a rating in its task response, so there was one that could have been used.
        assert "CONFIDENCE=" in watching.answers[attempt["attempt_id"]]
    assert watching.around_follow_up == {}
    for metric_id in PREDICTABILITY:
        assert (metrics[metric_id]["state"], metrics[metric_id]["value"]) == ("unavailable", None)
        assert metrics[metric_id]["reason"]


def test_whether_a_live_hosted_agent_acts_in_a_follow_up_is_decided_by_its_own_code(live):
    if live.hosted_agent is None:
        pytest.fail(f"The hosted agent was not deployed: {live.hosted_agent_error}")
    declare_hosted_agent_that_drops_the_callers_options(live)
    run = uuid.uuid4().hex[:8]
    observed = {}
    for agent in (live.hosted_agent, OWN_OPTIONS_AGENT):
        state = f"live-{run}-{agent}"
        live.service.prepare_attempt(state)
        try:
            attempt = hosted_response(live, agent, {"input": task_prompt(state)})
            chained = {"previous_response_id": attempt["id"]}
            after_the_attempt = live.service.evidence(state)
            recalled = hosted_response(live, agent, {**chained, "input": RECALL})
            # The separate confidence-only turn, with the tool choice none.
            rated = hosted_response(live, agent, {**chained, "input": QUESTION, "tool_choice": "none"})
            after_the_question = live.service.evidence(state)
            time.sleep(PAUSE_SECONDS)
            # Told to act, in the same conversation and under the same tool choice.
            told = hosted_response(live, agent, {**chained, "input": told_to_act("probe"), "tool_choice": "none"})
            after_told_to_act = live.service.evidence(state)
        finally:
            live.service.remove_attempt(state)
        observed[agent] = {
            "attempt_status": attempt.get("status"),
            "calls_of_the_attempt": len(after_the_attempt["events"]),
            "recalled": output_text(recalled),
            "confidence_turn": {
                "status": rated.get("status"),
                "answer": output_text(rated),
                "tool_calls": calls_in(rated),
                "new_service_calls": [
                    brief(event) for event in after_the_question["events"][len(after_the_attempt["events"]):]
                ],
                "tool_choice_in_response": rated.get("tool_choice"),
                "usage": rated.get("usage"),
            },
            "told_to_act_with_tool_choice_none": {
                "status": told.get("status"),
                "tool_calls": calls_in(told),
                "new_service_calls": [
                    brief(event) for event in after_told_to_act["events"][len(after_the_question["events"]):]
                ],
            },
        }
    print(json.dumps({"hosted_agents": observed}))

    for agent, seen in observed.items():
        assert seen["attempt_status"] == "completed" and seen["calls_of_the_attempt"] > 0
        # A follow-up that names the Attempt's response is answered with that conversation.
        assert f"live-{run}-{agent}" in seen["recalled"]
    fixture_agent, own_options = observed[live.hosted_agent], observed[OWN_OPTIONS_AGENT]
    # The fixture's agent passes the caller's tool choice on to its model, and called nothing.
    assert fixture_agent["confidence_turn"]["new_service_calls"] == []
    assert fixture_agent["told_to_act_with_tool_choice_none"]["new_service_calls"] == []
    # The same agent with one line of its own code changed was sent the same request, and acted.
    acted = own_options["told_to_act_with_tool_choice_none"]
    assert acted["tool_calls"] and acted["new_service_calls"]
    assert all(call[0] == "create" for call in acted["new_service_calls"])


def hosted_response(live, agent, body):
    return live.project_request(
        "POST", f"/agents/{agent}/endpoint/protocols/openai/responses?api-version=v1", {"stream": False, **body}
    )


def declare_hosted_agent_that_drops_the_callers_options(live):
    """Make sure the fixture's project has the fixture's hosted agent again, with one line of its code changed.

    The line makes the hosting package drop the options a caller sends, such as a tool choice,
    before the agent runs. The package offers that to the developer of an agent as `prepare_options`.
    """
    try:
        live.project_request("GET", f"/agents/{OWN_OPTIONS_AGENT}?api-version=v1")
        return
    except LiveFixtureError as error:
        if not str(error).startswith("HTTP 404"):
            raise
    source = Path(live_fixture.__file__).parent / "hosted_agent"
    as_deployed = "ResponsesHostServer(agent).run()"
    main = (source / "main.py").read_text(encoding="utf-8")
    assert main.count(as_deployed) == 1
    main = main.replace(as_deployed, "ResponsesHostServer(agent, prepare_options=lambda request, options: {}).run()")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("main.py", main)
        for filename in ("todo_tools.py", "requirements.txt"):
            archive.write(source / filename, filename)
    code = buffer.getvalue()
    declaration = {
        "description": "Disposable synthetic hosted agent that drops the options a caller sends",
        "definition": {
            "kind": "hosted", "cpu": "0.5", "memory": "1Gi",
            "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}],
            "code_configuration": {"runtime": "python_3_13", "entry_point": ["python", "main.py"],
                                   "dependency_resolution": "remote_build"},
            "environment_variables": {
                "MODEL_DEPLOYMENT": MODEL_DEPLOYMENT, "TODO_SERVICE_URL": live.service.url,
                "TODO_API_KEY": live.service.api_key, "AGENT_INSTRUCTIONS": AGENT_INSTRUCTIONS,
                "PYTHONNOUSERSITE": "1",
            },
        },
    }
    boundary = uuid.uuid4().hex
    payload = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
        f"Content-Type: application/json\r\n\r\n{json.dumps(declaration)}\r\n"
        f'--{boundary}\r\nContent-Disposition: form-data; name="code"; filename="agent.zip"\r\n'
        "Content-Type: application/zip\r\n\r\n"
    ).encode() + code + f"\r\n--{boundary}--\r\n".encode()
    created = live.project_request("POST", "/agents?api-version=v1", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "x-ms-agent-name": OWN_OPTIONS_AGENT, "x-ms-code-zip-sha256": hashlib.sha256(code).hexdigest(),
    }, raw=payload)
    version = created["versions"]["latest"]["version"]
    for _ in range(120):
        status = live.project_request("GET", f"/agents/{OWN_OPTIONS_AGENT}/versions/{version}?api-version=v1")["status"]
        if status == "active":
            break
        assert status != "failed", "the hosted agent that drops the caller's options could not be deployed"
        time.sleep(10)
    else:
        pytest.fail(f"the hosted agent that drops the caller's options was still {status!r} after 20 minutes")
    live.project_request("PATCH", f"/agents/{OWN_OPTIONS_AGENT}?api-version=v1", {
        "agent_endpoint": {
            "version_selector": {"version_selection_rules": [
                {"type": "FixedRatio", "agent_version": version, "traffic_percentage": 100},
            ]},
            "protocol_configuration": {"responses": {}},
        },
    }, {"Content-Type": "application/merge-patch+json"})

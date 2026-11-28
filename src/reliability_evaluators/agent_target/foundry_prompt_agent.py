"""Agent target for a Foundry prompt agent, invoked from customer code through the project's Responses interface.

Standard library only: plain REST with a bearer token, sent through the `FoundryProject` that
everything that reaches a Foundry project uses. See docs/foundry_prompt_agent/readme.md.

The reading of a response's output and of an answer that carries none is that of the hosted
agent target, imported from `foundry_hosted`, so that the two targets cannot come to read them differently.
"""

from __future__ import annotations

import json
from math import isfinite
from time import perf_counter
from typing import Any, Callable

from ..evidence import ActionEvent, InvocationEvidence
from ..foundry import Answer, FoundryProject, NotAnswered
from .foundry_hosted import REQUEST_ID_HEADERS as REQUEST_ID_HEADERS
from .foundry_hosted import (
    SHOWN_BODY,
    decoded_arguments,
    error_of_an_answer,
    measured_of_an_attempt,
    output_items,
    output_text,
    request_ids,
)
from .interface import (
    CUSTOMER_CODE,
    AgentResponse,
    AttemptRequest,
    ConfidenceRequest,
    ConfidenceResponse,
    InvocationFailed,
    IsolatedFollowUp,
)

# The version is part of the path of the route. The request carries no api-version.
API_VERSION = "v1"
RESPONSES_ROUTE = f"/openai/{API_VERSION}/responses"
AGENT_TARGET = "foundry_prompt_agent"
RESPONSES_PROTOCOL = "responses"
# The tool choice under which the project runs the agent without letting it call a tool.
TOOL_CHOICE_NONE = "none"
# The evidence source to declare when `native_tool_calls` records the action evidence.
NATIVE_TOOL_CALLS = "tool calls in the response of the Foundry prompt agent"
NO_USAGE = "the response of the prompt agent carried no usage"
NO_RESPONSE = "the Foundry project returned no response, so no usage was reported"
# Where a count comes from. Foundry runs a prompt agent and reports the usage of its response itself.
USAGE_SOURCE = "the usage of the response the Foundry project returned for the prompt agent"
SERVED_MODEL_HEADER = "x-ms-served-model"
ELAPSED_TIME_BOUNDARY = (
    "wall clock from just before the Responses request is sent to the Foundry project until its response has "
    "been read, measured by the agent target. Getting the token is not included"
)
TOOL_CALL_BOUNDARY = (
    "tool call items in the output of the response the project returned for the Attempt, counted by the agent "
    "target. A tool call the project does not report there is not counted"
)


class FoundryPromptAgentTarget:
    """Invokes one prompt agent of one Foundry project, once per Attempt.

    `project_endpoint` is the project's endpoint, such as
    `https://<account>.services.ai.azure.com/api/projects/<project>`, and `agent_name` the
    name of the prompt agent in it. `agent_version` pins the version that answers. Without
    it the project chooses, and the version that answered is recorded on each Attempt.
    `credential` returns the bearer token to call the project with, for the resource
    `https://ai.azure.com`. Without it the token comes from the Azure CLI.

    `compose_input` gives what the agent is sent for an Attempt instead of its prompt, for an
    agent that has to be told which test data the Attempt works on. Without it the agent is
    sent the Attempt's prompt exactly as it is. A composed input is recorded on the Attempt.
    Under a prompt variation the prompt is the exact text of a prompt variant, so whatever
    `compose_input` adds to it has to be the same under the baseline and every variant.

    `timeout_seconds` is how long the target waits for the project's answer to one request,
    unless a time limit is declared for the Attempt: the target then stops at that limit.
    `clock` gives seconds and never runs backwards. The target times its own request on it.

    Every invocation is one request that refers to no earlier response, so the agent starts
    a new conversation for every Attempt. That resets nothing outside the conversation.

    The target asks the agent for its confidence rating in an isolated follow-up: see `elicit_confidence`.
    """

    isolated_follow_up = IsolatedFollowUp(supported=True)

    def __init__(
        self,
        *,
        project_endpoint: str,
        agent_name: str,
        agent_version: str | None = None,
        credential: Callable[[], str] | None = None,
        compose_input: Callable[[AttemptRequest], str] | None = None,
        timeout_seconds: float = 180.0,
        clock: Callable[[], float] = perf_counter,
    ):
        if not isinstance(project_endpoint, str) or not project_endpoint.startswith(("https://", "http://")):
            raise ValueError(f"a Foundry project is reached at its endpoint, a URL. Got {project_endpoint!r}")
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise ValueError(f"a prompt agent is identified by its name, got {agent_name!r}")
        if agent_version is not None and (not isinstance(agent_version, str) or not agent_version.strip()):
            raise ValueError(f"the version of an agent is text, such as '3', or left out. Got {agent_version!r}")
        if type(timeout_seconds) not in (int, float) or not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds is a positive, finite number of seconds, got {timeout_seconds!r}")
        self._project = FoundryProject(project_endpoint, credential=credential, timeout_seconds=timeout_seconds)
        self._agent = {"type": "agent_reference", "name": agent_name}
        if agent_version is not None:
            self._agent["version"] = agent_version
        self._compose_input = compose_input
        self._timeout_seconds = timeout_seconds
        self._clock = clock

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        # Without a composed input the agent is sent the Attempt's prompt exactly as it is.
        composed = None if self._compose_input is None else self._compose_input(request)
        body = {
            "input": request.prompt if composed is None else composed,
            "agent_reference": self._agent,
            # Identifies the Attempt on the platform. The response returns it.
            "metadata": {"attempt_id": request.attempt_id, "task_id": request.task_id},
        }
        # The time limit the customer declared for the Attempt comes before the target's own.
        limit = self._timeout_seconds if request.timeout_seconds is None else request.timeout_seconds
        # Before the clock starts: getting the token is no part of the Attempt's elapsed time.
        self._project.sign_in()
        started = self._clock()
        answer = self._send(limit, body)
        elapsed = self._clock() - started
        response, invocation = _completed(answer, lambda response: _invocation(answer, response, composed))
        return AgentResponse(
            output=output_text(response),
            resource_measurements=measured_of_an_attempt(
                elapsed, invocation["tool_calls"], ELAPSED_TIME_BOUNDARY, TOOL_CALL_BOUNDARY
            ),
            invocation=invocation,
        )

    def elicit_confidence(self, request: ConfidenceRequest) -> ConfidenceResponse:
        """Put the confidence question to the agent in the conversation of one completed Attempt.

        The request names the Attempt's response as the one it continues, so the agent answers with
        that conversation before it, and carries the tool choice `none`, under which the project
        runs the agent without letting it call a tool. The project says in its response which
        response it continued and which tool choice it applied. An answer the project does not
        say that about, or one given after a tool call, is not returned: the request then fails.
        """
        attempt = request.response.invocation or {}
        previous = attempt.get("response_id")
        body = {
            "input": request.prompt,
            "previous_response_id": previous,
            # The version that answered the Attempt answers the question about it.
            "agent_reference": attempt.get("agent") or self._agent,
            "tool_choice": TOOL_CHOICE_NONE,
            "metadata": {
                "attempt_id": request.attempt.attempt_id,
                "task_id": request.attempt.task_id,
                "request": "confidence",
            },
        }
        answer = self._send(self._timeout_seconds, body)
        response, follow_up = _completed(answer, lambda response: _follow_up(answer, response, request.prompt))
        why_not_used = not_isolated(follow_up, previous)
        if why_not_used is not None:
            raise InvocationFailed(why_not_used, invocation=follow_up)
        return ConfidenceResponse(
            attempt_id=request.attempt.attempt_id, output=output_text(response), invocation=follow_up
        )

    def _send(self, timeout_seconds: float, body: dict[str, Any]) -> Answer:
        """Send one request to the project. It is sent once: a request that may have reached the agent is not repeated."""
        try:
            return self._project.send_once("POST", RESPONSES_ROUTE, body, timeout_seconds=timeout_seconds)
        except NotAnswered as unanswered:
            # No answer came back, so nothing is known about what the agent did.
            if unanswered.timed_out:
                # The sweep records a TimeoutError as an Attempt that timed out.
                raise TimeoutError(f"the Foundry project did not answer within {timeout_seconds:g} seconds") from None
            raise InvocationFailed(f"the Foundry project could not be reached: {unanswered.reason}") from None


def _invocation(answer: Answer, response: dict[str, Any], composed_input: str | None) -> dict[str, Any]:
    """What the platform returned about one invocation, as it returned it.

    `response` is the response object of the answer, or empty when the answer has none.
    `composed_input` is what the agent was sent when that was not the Attempt's prompt as it is.
    """
    return InvocationEvidence.with_usage(
        # As the platform reported it. Usage that was not reported is missing, never zero.
        response.get("usage"),
        source=USAGE_SOURCE,
        missing=NO_USAGE if response else NO_RESPONSE,
        agent_target=AGENT_TARGET,
        entry_point=CUSTOMER_CODE,
        response_id=response.get("id"),
        tool_calls=tool_calls_of_a_response(response),
        identifiers={
            "protocol": RESPONSES_PROTOCOL,
            "api_version": API_VERSION,
            "composed_input": composed_input,
            "http_status": answer.status,
            "request_ids": request_ids(answer),
            "agent": response.get("agent_reference"),
            "status": response.get("status"),
            # The model deployment the response names, and the model a header of the answer says served the request.
            "model": response.get("model"),
            "served_model": answer.header(SERVED_MODEL_HEADER),
            "metadata": response.get("metadata"),
            "error": response.get("error") if response else error_of_an_answer(answer),
        },
    ).to_dict()


def _completed(
    answer: Answer, recorded: Callable[[dict[str, Any]], dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The completed response an answer carries, and what is recorded of it.

    `recorded` gives what is recorded of the answer for its response object, or for none when
    the answer has none. An answer that is not a completed response raises InvocationFailed with that.
    """
    response = answer.json
    if answer.status != 200:
        raise InvocationFailed(
            f"the Foundry project answered HTTP {answer.status}: {_described(error_of_an_answer(answer))}",
            invocation=recorded({}),
        )
    if not isinstance(response, dict) or output_items(response) is None:
        raise InvocationFailed(
            f"the Foundry project answered HTTP 200 with a body that is not a response: {answer.text[:SHOWN_BODY]}",
            invocation=recorded({}),
        )
    record = recorded(response)
    failure = failure_of_a_response(response)
    if failure is not None:
        raise InvocationFailed(failure, invocation=record)
    return response, record


def not_isolated(follow_up: dict[str, Any], previous: str | None) -> str | None:
    """Why the answer to a follow-up is not used, from what the project reports of it, or None when it is.

    `previous` is the response of the Attempt, which the follow-up was to continue.
    """
    if not previous or follow_up["previous_response_id"] != previous:
        return (
            f"the Foundry project does not report that the follow-up continued the response {previous} of the "
            "Attempt, so its answer is not used"
        )
    if follow_up["tool_calls"]:
        called = ", ".join(call["name"] for call in follow_up["tool_calls"])
        return (
            f"the agent called a tool while it answered the follow-up ({called}). "
            "The follow-up was not isolated, so its answer is not used"
        )
    if follow_up["tool_choice"] != TOOL_CHOICE_NONE:
        return (
            f"the Foundry project reports the tool choice {follow_up['tool_choice']!r} for the follow-up and not "
            f"{TOOL_CHOICE_NONE!r}. The agent could act, so its answer is not used"
        )
    return None


def _follow_up(answer: Answer, response: dict[str, Any], question: str) -> dict[str, Any]:
    """What the platform returned about one follow-up, in the form of an invocation.

    `question` is what the agent was sent. With it are the response the project says the
    follow-up continued and the tool choice it says it applied.
    """
    recorded = _invocation(answer, response, None)
    del recorded["composed_input"]
    return {
        **recorded,
        "input": question,
        "previous_response_id": response.get("previous_response_id"),
        "tool_choice": response.get("tool_choice"),
    }


def failure_of_a_response(response: dict[str, Any]) -> str | None:
    """Why a response is not a completed execution of the agent, or None when it is one.

    A status of completed does not prove it: a response that carries an error failed.
    """
    this = f"the response {response.get('id')} of the prompt agent"
    status, error = response.get("status"), response.get("error")
    if status != "completed":
        detail = error if error is not None else response.get("incomplete_details")
        return f"{this} ended with status {status!r}: {_described(detail)}"
    if error is not None:
        return f"{this} has status 'completed' and carries an error: {_described(error)}"
    return None


def _described(error: Any) -> str:
    """An error as it is quoted in the reason of a failure: its code and message, or the error as it is."""
    if isinstance(error, dict) and ("code" in error or "message" in error):
        return f"{error.get('code')}: {error.get('message')}"
    return error if isinstance(error, str) else json.dumps(error)


def native_tool_calls(request: AttemptRequest, response: AgentResponse) -> list[ActionEvent] | None:
    """The tool calls a prompt agent's response exposed, in order, as action evidence.

    Declare it as the `record` of an ActionEvidenceDeclaration, with NATIVE_TOOL_CALLS as
    the evidence source. An event carries an error only when the platform gave the call a
    status other than completed. Returns None when the response exposed no tool calls to read.
    """
    recorded = response.invocation or {}
    if recorded.get("agent_target") != AGENT_TARGET or recorded.get("tool_calls") is None:
        return None
    return [
        ActionEvent(
            tool_name=call["name"],
            arguments=call["arguments"],
            result=call["output"],
            error=None if call["status"] in (None, "completed") else f"the call ended with status {call['status']!r}",
        )
        for call in recorded["tool_calls"]
    ]


def tool_calls_of_a_response(response: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The tool calls the response exposes, in its order, each with what the tool returned.

    A tool call is an output item whose type ends in `_call`, such as `openapi_call`. What it
    returned is the item of the type `<that type>_output` with the same `call_id`.

    None when the response has no list of output items: the calls were then not exposed,
    which is not the same as no call.
    """
    items = output_items(response)
    if items is None:
        return None
    returned = {
        item.get("call_id"): item.get("output")
        for item in items
        if str(item.get("type", "")).endswith("_call_output")
    }
    return [
        {
            "call_id": item.get("call_id"),
            "type": item["type"],
            # A tool type that gives its calls no name is named after its type.
            "name": item.get("name") or item["type"],
            "arguments": decoded_arguments(item.get("arguments")),
            "output": returned.get(item.get("call_id")),
            "status": item.get("status"),
        }
        for item in items
        if str(item.get("type", "")).endswith("_call")
    ]

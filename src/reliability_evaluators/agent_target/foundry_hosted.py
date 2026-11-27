"""Agent target for a Foundry hosted agent that speaks the Responses protocol, invoked from customer code.

Standard library only: plain REST with a bearer token. See docs/foundry_hosted_agent/readme.md.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from ..evidence import ActionEvent, ResourceMeasurement, ResourceType
from .interface import AgentResponse, AttemptRequest, InvocationFailed

API_VERSION = "v1"
AGENT_TARGET = "foundry_hosted_agent"
RESPONSES_PROTOCOL = "responses"
# The evidence source to declare when `native_tool_calls` records the action evidence.
NATIVE_TOOL_CALLS = "tool calls in the response of the Foundry hosted agent"
NO_USAGE = "the response of the hosted agent carried no usage"
# How much of an answer's body is quoted in an error.
_SHOWN_BODY = 2000
ELAPSED_TIME_BOUNDARY = (
    "wall clock from just before the Responses request is sent to the hosted agent until its response has been "
    "read, measured by the agent target. Getting the token and looking the agent up are not included"
)
TOOL_CALL_BOUNDARY = (
    "function_call items in the output of the response the hosted agent returned for the Attempt, counted by the "
    "agent target. A tool call the agent does not report there is not counted"
)


class FoundryHostedAgentTarget:
    """Invokes one hosted agent of one Foundry project, once per Attempt.

    `project_endpoint` is the project's endpoint, such as
    `https://<account>.services.ai.azure.com/api/projects/<project>`, and `agent_name` the
    name of the hosted agent in it. `credential` returns the bearer token to call the project with.

    `compose_input` gives what the agent is sent for an Attempt. Without it the agent is
    sent the Task's prompt as it is.

    `clock` gives seconds and never runs backwards. The target times its own request on it.
    """

    def __init__(
        self,
        *,
        project_endpoint: str,
        agent_name: str,
        credential: Callable[[], str],
        compose_input: Callable[[AttemptRequest], str] | None = None,
        clock: Callable[[], float] = perf_counter,
    ):
        self._agent_url = f"{project_endpoint.rstrip('/')}/agents/{agent_name}"
        self._credential = credential
        self._compose_input = compose_input or (lambda request: request.prompt)
        self._clock = clock

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        body = {"input": self._compose_input(request), "stream": False}
        token = self._credential()
        started = self._clock()
        answer = _send("POST", f"{self._agent_url}/endpoint/protocols/openai/responses", token, body)
        elapsed = self._clock() - started
        if answer.status != 200:
            raise InvocationFailed(
                f"the hosted agent endpoint answered HTTP {answer.status}: {answer.text[:_SHOWN_BODY]}",
                invocation=_invocation(answer, {}),
            )
        response = answer.json
        if not isinstance(response, dict):
            raise InvocationFailed(
                "the hosted agent endpoint answered HTTP 200 with a body that is not a response: "
                f"{answer.text[:_SHOWN_BODY]}",
                invocation=_invocation(answer, {}),
            )
        invocation = _invocation(answer, response)
        failure = _failure_of(response)
        if failure is not None:
            raise InvocationFailed(failure, invocation=invocation)
        return AgentResponse(
            output=_output_text(response),
            resource_measurements=_resource_measurements(elapsed, invocation["tool_calls"]),
            invocation=invocation,
        )


@dataclass(frozen=True)
class _Answer:
    """An HTTP answer from the project, whatever its status."""

    status: int
    text: str

    @property
    def json(self) -> Any:
        """The body as JSON, or None when it is not JSON."""
        try:
            return json.loads(self.text)
        except ValueError:
            return None


def _send(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> _Answer:
    """Send one request to the project. It is sent once: a request that may have reached the agent is not repeated."""
    request = urllib.request.Request(
        f"{url}?api-version={API_VERSION}",
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as answered:
            return _Answer(answered.status, answered.read().decode(errors="replace"))
    except urllib.error.HTTPError as error:
        return _Answer(error.code, error.read().decode(errors="replace"))
    except OSError as error:
        # URLError is an OSError. No answer came back, so nothing is known about what the agent did.
        reason = getattr(error, "reason", error)
        raise InvocationFailed(f"the Foundry project could not be reached: {reason}") from error


def _invocation(answer: _Answer, response: dict[str, Any]) -> dict[str, Any]:
    """What the platform returned about one invocation, as it returned it.

    `response` is the response object of the answer, or empty when the answer has none.
    """
    usage = response.get("usage")
    return {
        "agent_target": AGENT_TARGET,
        "protocol": RESPONSES_PROTOCOL,
        "api_version": API_VERSION,
        "http_status": answer.status,
        "agent": response.get("agent_reference"),
        "response_id": response.get("id"),
        "agent_session_id": response.get("agent_session_id"),
        "status": response.get("status"),
        "error": response.get("error") if response else _error_of(answer),
        # As the platform reported it. Usage that was not reported is missing, never zero.
        "token_usage": usage,
        "token_usage_missing": NO_USAGE if usage is None else None,
        "tool_calls": _tool_calls(response),
    }


def _failure_of(response: dict[str, Any]) -> str | None:
    """Why a response is not a completed execution of the agent, or None when it is one.

    A status of completed does not prove it: a response that carries an error failed.
    """
    this = f"the response {response.get('id')} of the hosted agent"
    status, error = response.get("status"), response.get("error")
    if status != "completed":
        detail = error if error is not None else response.get("incomplete_details")
        return f"{this} ended with status {status!r}: {json.dumps(detail)}"
    if error is not None:
        return f"{this} has status 'completed' and carries an error: {json.dumps(error)}"
    return None


def _error_of(answer: _Answer) -> Any:
    """The error an answer without a response object carries: its `error`, or its body as it is."""
    body = answer.json
    return body["error"] if isinstance(body, dict) and "error" in body else answer.text[:_SHOWN_BODY]


def _resource_measurements(elapsed: float, tool_calls: list[dict[str, Any]] | None) -> tuple[ResourceMeasurement, ...]:
    """What the target measured of one Attempt. A count it could not make is left out."""
    measured = [
        ResourceMeasurement(
            resource_type=ResourceType.ELAPSED_TIME, value=elapsed, unit="seconds", boundary=ELAPSED_TIME_BOUNDARY
        )
    ]
    if tool_calls is not None:
        measured.append(
            ResourceMeasurement(
                resource_type=ResourceType.TOOL_CALLS, value=len(tool_calls), unit="calls", boundary=TOOL_CALL_BOUNDARY
            )
        )
    return tuple(measured)


def native_tool_calls(request: AttemptRequest, response: AgentResponse) -> list[ActionEvent] | None:
    """The tool calls a hosted agent's response exposed, in order, as action evidence.

    Declare it as the `record` of an ActionEvidenceDeclaration, with NATIVE_TOOL_CALLS as
    the evidence source. The platform says what each tool returned and not whether the
    call failed, so no event carries an error: a failure shows only in the result.
    Returns None when the response exposed no tool calls to read.
    """
    recorded = response.invocation or {}
    if recorded.get("agent_target") != AGENT_TARGET or recorded.get("tool_calls") is None:
        return None
    return [
        ActionEvent(tool_name=call["name"], arguments=call["arguments"], result=call["output"])
        for call in recorded["tool_calls"]
    ]


def _tool_calls(response: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The tool calls the response exposes, in its order, each with what the tool returned.

    None when the response has no list of output items: the calls were then not exposed,
    which is not the same as no call.
    """
    output = response.get("output")
    if not isinstance(output, list):
        return None
    items = [item for item in output if isinstance(item, dict)]
    returned = {
        item.get("call_id"): item.get("output") for item in items if item.get("type") == "function_call_output"
    }
    return [
        {
            "call_id": item.get("call_id"),
            "name": item.get("name"),
            "arguments": _decoded(item.get("arguments")),
            "output": returned.get(item.get("call_id")),
        }
        for item in items
        if item.get("type") == "function_call"
    ]


def _decoded(arguments: Any) -> Any:
    """The arguments of a tool call. The protocol gives them as JSON text; anything else is kept as given."""
    if not isinstance(arguments, str):
        return arguments
    try:
        return json.loads(arguments)
    except ValueError:
        return arguments


def _output_text(response: dict[str, Any]) -> str:
    """The text of the agent's messages, in order."""
    return "".join(
        part.get("text", "")
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )

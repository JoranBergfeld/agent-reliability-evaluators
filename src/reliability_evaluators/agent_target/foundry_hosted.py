"""Agent target for a Foundry hosted agent that speaks the Responses protocol, invoked from customer code.

Standard library only: plain REST with a bearer token. See docs/foundry_hosted_agent/readme.md.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from ..evidence import ActionEvent, ResourceMeasurement, ResourceType
from .interface import AgentResponse, AttemptRequest, InvocationFailed, IsolatedFollowUp

API_VERSION = "v1"
# What a token for a Foundry project is asked for.
TOKEN_RESOURCE = "https://ai.azure.com"
AGENT_TARGET = "foundry_hosted_agent"
RESPONSES_PROTOCOL = "responses"
# The evidence source to declare when `native_tool_calls` records the action evidence.
NATIVE_TOOL_CALLS = "tool calls in the response of the Foundry hosted agent"
NO_USAGE = "the response of the hosted agent carried no usage"
REQUEST_ID_HEADERS = ("x-request-id", "apim-request-id")
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


class AzureCliCredential:
    """Bearer tokens for a Foundry project, from the identity the Azure CLI is signed in with.

    Calling it returns a token. The token is kept until five minutes before it expires, so a
    long sweep asks the Azure CLI again only when it has to. `subscription` names the
    subscription to ask for, when the signed-in identity has more than one tenant.
    """

    def __init__(self, subscription: str | None = None):
        self._subscription = subscription
        self._token: str | None = None
        self._expires_on = 0.0

    def __call__(self) -> str:
        if self._token is None or time.time() >= self._expires_on - 300:
            az = shutil.which("az")
            if az is None:
                raise RuntimeError("the Azure CLI (az) is not on PATH, so it cannot give a token for the Foundry project")
            command = [az, "account", "get-access-token", "--resource", TOKEN_RESOURCE, "--only-show-errors", "-o", "json"]
            if self._subscription:
                command += ["--subscription", self._subscription]
            result = subprocess.run(command, text=True, capture_output=True, timeout=120)
            if result.returncode:
                raise RuntimeError(f"the Azure CLI gave no token for the Foundry project: {result.stderr.strip()}")
            token = json.loads(result.stdout)
            # Without an expiry time the token is not kept.
            self._token, self._expires_on = token["accessToken"], float(token.get("expires_on") or 0)
        return self._token


class UnsupportedAgent(Exception):
    """The agent is not one this target supports. It was not invoked, and nothing was tried instead."""


class FoundryHostedAgentTarget:
    """Invokes one hosted agent of one Foundry project, once per Attempt.

    `project_endpoint` is the project's endpoint, such as
    `https://<account>.services.ai.azure.com/api/projects/<project>`, and `agent_name` the
    name of the hosted agent in it. `credential` returns the bearer token to call the project
    with, for the resource `https://ai.azure.com`. Without it the token comes from the Azure CLI.

    `compose_input` gives what the agent is sent for an Attempt instead of its prompt, for an
    agent that has to be told which test data the Attempt works on. Without it the agent is
    sent the Attempt's prompt exactly as it is. A composed input is recorded on the Attempt.
    Under a prompt variation the prompt is the exact text of a prompt variant, so whatever
    `compose_input` adds to it has to be the same under the baseline and every variant.

    `timeout_seconds` is how long the target waits for the agent's answer to one request,
    unless a time limit is declared for the Attempt: the target then stops at that limit.
    `clock` gives seconds and never runs backwards. The target times its own request on it.

    The target does not ask the agent for a confidence rating: see `isolated_follow_up`.
    """

    isolated_follow_up = IsolatedFollowUp(
        supported=False,
        reason=(
            "a follow-up in which a Foundry hosted agent answers in the conversation of the Attempt "
            "and cannot act has not been verified"
        ),
    )

    def __init__(
        self,
        *,
        project_endpoint: str,
        agent_name: str,
        credential: Callable[[], str] | None = None,
        compose_input: Callable[[AttemptRequest], str] | None = None,
        timeout_seconds: float = 300.0,
        clock: Callable[[], float] = perf_counter,
    ):
        self._project_endpoint = project_endpoint.rstrip("/")
        self._agent_name = agent_name
        self._agent_url = f"{self._project_endpoint}/agents/{agent_name}"
        # What the project said about the agent, once it has been looked up.
        self._agent: dict[str, Any] | None = None
        self._credential = credential or AzureCliCredential()
        self._compose_input = compose_input
        self._timeout_seconds = timeout_seconds
        self._clock = clock

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        # Without a composed input the agent is sent the Attempt's prompt exactly as it is.
        composed = None if self._compose_input is None else self._compose_input(request)
        body = {
            "input": request.prompt if composed is None else composed,
            "stream": False,
            # Identifies the Attempt on the platform. The response returns it.
            "metadata": {"attempt_id": request.attempt_id, "task_id": request.task_id},
        }
        token = self._credential()
        self._require_supported_agent(token)
        # The time limit the customer declared for the Attempt comes before the target's own.
        limit = self._timeout_seconds if request.timeout_seconds is None else request.timeout_seconds
        started = self._clock()
        answer = _send("POST", f"{self._agent_url}/endpoint/protocols/openai/responses", token, limit, body)
        elapsed = self._clock() - started
        if answer.status != 200:
            raise InvocationFailed(
                f"the hosted agent endpoint answered HTTP {answer.status}: {answer.text[:_SHOWN_BODY]}",
                invocation=_invocation(answer, {}, composed),
            )
        response = answer.json
        if not isinstance(response, dict):
            raise InvocationFailed(
                "the hosted agent endpoint answered HTTP 200 with a body that is not a response: "
                f"{answer.text[:_SHOWN_BODY]}",
                invocation=_invocation(answer, {}, composed),
            )
        invocation = _invocation(answer, response, composed)
        failure = _failure_of(response)
        if failure is not None:
            raise InvocationFailed(failure, invocation=invocation)
        return AgentResponse(
            output=_output_text(response),
            resource_measurements=_resource_measurements(elapsed, invocation["tool_calls"]),
            invocation=invocation,
        )

    def agent_configuration(self) -> dict[str, Any]:
        """What identifies the agent, as the project reports it, to declare as an experiment's agent configuration.

        Raises UnsupportedAgent for an agent this target does not support, before any Attempt
        is made. The project does not report the model of a hosted agent. Its settings, which
        the project returns with its details, are not copied: they can hold secrets.
        """
        agent = self._require_supported_agent(self._credential())
        endpoint = agent["agent_endpoint"]
        return {
            "agent_target": AGENT_TARGET,
            "project_endpoint": self._project_endpoint,
            "agent": self._agent_name,
            "kind": "hosted",
            "protocols": endpoint["protocols"],
            "latest_version": agent["versions"]["latest"].get("version"),
            "version_selection_rules": (endpoint.get("version_selector") or {}).get("version_selection_rules"),
        }

    def _require_supported_agent(self, token: str) -> dict[str, Any]:
        """What the project says about the agent, when it is a hosted agent that serves the Responses protocol.

        Raises otherwise. The agent is looked up once. An agent that is not supported is never sent a request.
        """
        if self._agent is None:
            answer = _send("GET", self._agent_url, token, self._timeout_seconds, awaited="the Foundry project")
            if answer.status != 200 or not isinstance(answer.json, dict):
                raise InvocationFailed(
                    f"agent {self._agent_name!r} could not be looked up in the Foundry project: "
                    f"HTTP {answer.status}: {answer.text[:_SHOWN_BODY]}"
                )
            self._agent = answer.json
        latest = (self._agent.get("versions") or {}).get("latest") or {}
        kind = (latest.get("definition") or {}).get("kind")
        if kind != "hosted":
            raise UnsupportedAgent(
                f"agent {self._agent_name!r} is of kind {kind!r} and not a hosted agent, so it was not invoked"
            )
        protocols = (self._agent.get("agent_endpoint") or {}).get("protocols") or []
        if RESPONSES_PROTOCOL not in protocols:
            raise UnsupportedAgent(
                f"hosted agent {self._agent_name!r} serves the protocols {protocols} and not the Responses "
                "protocol. Only hosted agents that serve the Responses protocol are supported, so it was not invoked"
            )
        return self._agent


@dataclass(frozen=True)
class _Answer:
    """An HTTP answer from the project, whatever its status."""

    status: int
    text: str
    # The headers that identify the request on the platform, where the answer carried them.
    request_ids: dict[str, str]

    @property
    def json(self) -> Any:
        """The body as JSON, or None when it is not JSON."""
        try:
            return json.loads(self.text)
        except ValueError:
            return None


def _send(
    method: str,
    url: str,
    token: str,
    timeout_seconds: float,
    body: dict[str, Any] | None = None,
    awaited: str = "the hosted agent",
) -> _Answer:
    """Send one request to the project. It is sent once: a request that may have reached the agent is not repeated."""
    request = urllib.request.Request(
        f"{url}?api-version={API_VERSION}",
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as answered:
            return _answer(answered.status, answered)
    except urllib.error.HTTPError as error:
        return _answer(error.code, error)
    except OSError as error:
        # URLError is an OSError. No answer came back, so nothing is known about what the agent did.
        reason = getattr(error, "reason", error)
        if isinstance(reason, TimeoutError):
            # The sweep records a TimeoutError as an Attempt that timed out.
            raise TimeoutError(f"{awaited} did not answer within {timeout_seconds:g} seconds") from error
        raise InvocationFailed(f"the Foundry project could not be reached: {reason}") from error


def _answer(status: int, answered: Any) -> _Answer:
    request_ids = {name: answered.headers[name] for name in REQUEST_ID_HEADERS if answered.headers.get(name)}
    return _Answer(status, answered.read().decode(errors="replace"), request_ids)


def _invocation(answer: _Answer, response: dict[str, Any], composed_input: str | None) -> dict[str, Any]:
    """What the platform returned about one invocation, as it returned it.

    `response` is the response object of the answer, or empty when the answer has none.
    `composed_input` is what the agent was sent when that was not the Attempt's prompt as it is.
    """
    usage = response.get("usage")
    return {
        "agent_target": AGENT_TARGET,
        "protocol": RESPONSES_PROTOCOL,
        "api_version": API_VERSION,
        "composed_input": composed_input,
        "http_status": answer.status,
        "request_ids": answer.request_ids,
        "agent": response.get("agent_reference"),
        "response_id": response.get("id"),
        "agent_session_id": response.get("agent_session_id"),
        "status": response.get("status"),
        "metadata": response.get("metadata"),
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
    items = _output_items(response)
    if items is None:
        return None
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


def _output_items(response: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The output items of a response, in order, or None when it has no list of them."""
    output = response.get("output")
    return [item for item in output if isinstance(item, dict)] if isinstance(output, list) else None


def _output_text(response: dict[str, Any]) -> str:
    """The text of the agent's messages, in order."""
    return "".join(
        part.get("text") or ""
        for item in _output_items(response) or []
        if item.get("type") == "message" and isinstance(item.get("content"), list)
        for part in item["content"]
        if isinstance(part, dict) and part.get("type") == "output_text"
    )

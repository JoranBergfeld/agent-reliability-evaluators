"""Agent target for a Foundry hosted agent that speaks the Responses protocol, invoked from customer code.

Standard library only: plain REST with a bearer token. See docs/foundry_hosted_agent/readme.md.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable

from ..evidence import ActionEvent
from .interface import AgentResponse, AttemptRequest

API_VERSION = "v1"
AGENT_TARGET = "foundry_hosted_agent"
RESPONSES_PROTOCOL = "responses"
# The evidence source to declare when `native_tool_calls` records the action evidence.
NATIVE_TOOL_CALLS = "tool calls in the response of the Foundry hosted agent"


class FoundryHostedAgentTarget:
    """Invokes one hosted agent of one Foundry project, once per Attempt.

    `project_endpoint` is the project's endpoint, such as
    `https://<account>.services.ai.azure.com/api/projects/<project>`, and `agent_name` the
    name of the hosted agent in it. `credential` returns the bearer token to call the project with.

    `compose_input` gives what the agent is sent for an Attempt. Without it the agent is
    sent the Task's prompt as it is.
    """

    def __init__(
        self,
        *,
        project_endpoint: str,
        agent_name: str,
        credential: Callable[[], str],
        compose_input: Callable[[AttemptRequest], str] | None = None,
    ):
        self._agent_url = f"{project_endpoint.rstrip('/')}/agents/{agent_name}"
        self._credential = credential
        self._compose_input = compose_input or (lambda request: request.prompt)

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        body = {"input": self._compose_input(request), "stream": False}
        http_request = urllib.request.Request(
            f"{self._agent_url}/endpoint/protocols/openai/responses?api-version={API_VERSION}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self._credential()}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(http_request) as answered:
            response = json.load(answered)
        return AgentResponse(output=_output_text(response), invocation=self._invocation(response))

    @staticmethod
    def _invocation(response: dict[str, Any]) -> dict[str, Any]:
        """What the platform returned about one invocation, as it returned it."""
        return {
            "agent_target": AGENT_TARGET,
            "protocol": RESPONSES_PROTOCOL,
            "api_version": API_VERSION,
            "agent": response.get("agent_reference"),
            "response_id": response.get("id"),
            "agent_session_id": response.get("agent_session_id"),
            "status": response.get("status"),
            "tool_calls": _tool_calls(response),
        }


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

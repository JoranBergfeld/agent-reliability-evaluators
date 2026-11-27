"""A Foundry prompt agent as the agent target, invoked from the customer's own code."""

from __future__ import annotations

from typing import Callable

from .foundry import FoundryProject, read_responses_reply
from .interface import AgentResponse, AttemptRequest, IsolatedFollowUp

RESPONSES_PATH = "/openai/v1/responses"
EVIDENCE_SOURCE = "Foundry Responses API, called from customer code"
DEFAULT_TIMEOUT_SECONDS = 180.0


class FoundryPromptAgentTarget:
    """Invokes one Foundry prompt agent through the project's Responses interface.

    `compose_input` gives the text sent to the agent for an Attempt. Without it the
    Task's prompt is sent as it is.
    """

    isolated_follow_up = IsolatedFollowUp(
        supported=False,
        reason="a confidence-only follow-up in which a Foundry prompt agent cannot act has not been verified",
    )

    def __init__(
        self,
        *,
        project: FoundryProject,
        agent_name: str,
        agent_version: str | None = None,
        compose_input: Callable[[AttemptRequest], str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._project = project
        self._agent = {"type": "agent_reference", "name": agent_name}
        if agent_version is not None:
            self._agent["version"] = agent_version
        self._compose_input = compose_input or (lambda request: request.prompt)
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        reply = self._project.post(
            RESPONSES_PATH,
            {
                "input": self._compose_input(request),
                "agent_reference": self._agent,
                # Returned with the response, so that the response is tied to its Attempt on the platform too.
                "metadata": {"attempt_id": request.attempt_id, "task_id": request.task_id},
            },
            request.timeout_seconds if request.timeout_seconds is not None else self._timeout_seconds,
        )
        return read_responses_reply(reply, EVIDENCE_SOURCE)

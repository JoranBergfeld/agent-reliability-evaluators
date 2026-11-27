"""A Foundry prompt agent as the agent target, invoked from the customer's own code."""

from __future__ import annotations

from math import isfinite

from .foundry import FoundryProject, read_responses_reply
from .interface import AgentResponse, AttemptRequest, IsolatedFollowUp

RESPONSES_PATH = "/openai/v1/responses"
EVIDENCE_SOURCE = "Foundry Responses API, called from customer code"
DEFAULT_TIMEOUT_SECONDS = 180.0


class FoundryPromptAgentTarget:
    """Invokes one Foundry prompt agent through the Responses interface of its project.

    Every invocation is one request that refers to no earlier response, so the agent starts
    a new conversation for every Attempt. That resets nothing outside the conversation:
    whatever the agent's tools changed stays changed until a customer control restores it.

    The prompt of the Attempt is sent exactly as it is. Nothing is added to it.

    `agent_version` pins the version of the agent that answers. Without it the project
    chooses, and the version that answered is in the evidence of each Attempt.

    `timeout_seconds` is how long the target waits for the project when the customer
    declared no time limit for the Attempt. A declared limit is used instead.

    The identity of the Attempt and of its Task are sent as the metadata of the request.
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
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        if not isinstance(project, FoundryProject):
            raise ValueError(f"the agent is invoked through a FoundryProject, got {project!r}")
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise ValueError(f"a prompt agent is identified by its name, got {agent_name!r}")
        if agent_version is not None and (not isinstance(agent_version, str) or not agent_version.strip()):
            raise ValueError(f"the version of an agent is text, such as '3', or left out. Got {agent_version!r}")
        if type(timeout_seconds) not in (int, float) or not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds is a positive, finite number of seconds, got {timeout_seconds!r}")
        self._project = project
        self._agent = {"type": "agent_reference", "name": agent_name}
        if agent_version is not None:
            self._agent["version"] = agent_version
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        reply = self._project.post(
            RESPONSES_PATH,
            {
                "input": request.prompt,
                "agent_reference": self._agent,
                # The project returns it with the response, which ties the response to its Attempt.
                "metadata": {"attempt_id": request.attempt_id, "task_id": request.task_id},
            },
            request.timeout_seconds if request.timeout_seconds is not None else self._timeout_seconds,
        )
        return read_responses_reply(reply, EVIDENCE_SOURCE)

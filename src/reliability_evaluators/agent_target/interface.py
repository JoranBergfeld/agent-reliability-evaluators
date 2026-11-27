"""The agent target interface: how the Evaluator sweep invokes an agent, for an Attempt and its follow-up."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..evidence import ResourceMeasurement


@dataclass(frozen=True, kw_only=True)
class AttemptRequest:
    """What the agent is asked to do for one Attempt."""

    attempt_id: str
    task_id: str
    prompt: str


@dataclass(frozen=True, kw_only=True)
class AgentResponse:
    """What the agent answered. A response alone never decides the task outcome.

    `resource_measurements` is what the agent target measured of the Attempt's resource
    use, such as its tool-call count. A measurement it could not make is left out.
    """

    output: str
    resource_measurements: tuple[ResourceMeasurement, ...] = ()


@dataclass(frozen=True, kw_only=True)
class IsolatedFollowUp:
    """Whether an agent target can ask the agent a follow-up question in isolation.

    Isolated means: the agent answers in the conversation of one completed Attempt, and
    cannot take any action while it answers. A target that cannot ensure both declares
    `supported=False`, with the reason. The sweep then records no confidence rating.
    """

    supported: bool
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class ConfidenceRequest:
    """The confidence-only request that follows one completed Attempt.

    It carries what the agent may see: the Attempt it just completed, its own response,
    and the customer's task-success rule. It has no place for the task outcome, a verdict
    or any other Attempt.
    """

    attempt: AttemptRequest
    response: AgentResponse
    success_rule: str
    # The exact question to put to the agent. It already contains the success rule.
    prompt: str


@dataclass(frozen=True, kw_only=True)
class ConfidenceResponse:
    """What the agent answered to a confidence request, and the Attempt the answer belongs to."""

    attempt_id: str
    output: str


class AgentTarget(Protocol):
    """An agent the sweep can invoke. An invocation that fails raises.

    `isolated_follow_up` is the target's declaration of whether `elicit_confidence` can be
    answered in isolation. A target that does not support it need not implement
    `elicit_confidence`: the sweep does not call it.
    """

    isolated_follow_up: IsolatedFollowUp

    def invoke(self, request: AttemptRequest) -> AgentResponse: ...

    def elicit_confidence(self, request: ConfidenceRequest) -> ConfidenceResponse:
        """Put `request.prompt` to the agent in the conversation of the completed Attempt.

        The agent must not be able to act. The answer is returned as given, with the
        identity of the Attempt whose conversation it was given in.
        """
        ...
"""The agent target interface: how the Evaluator sweep invokes an agent for an Attempt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class AttemptRequest:
    """What the agent is asked to do for one Attempt."""

    attempt_id: str
    task_id: str
    prompt: str


@dataclass(frozen=True, kw_only=True)
class AgentResponse:
    """What the agent answered. A response alone never decides the task outcome."""

    output: str


class AgentTarget(Protocol):
    """An agent the sweep can invoke. An invocation that fails raises."""

    def invoke(self, request: AttemptRequest) -> AgentResponse: ...

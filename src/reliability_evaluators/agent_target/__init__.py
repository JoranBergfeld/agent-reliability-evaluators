"""Agent target: one interface for invoking an agent for an Attempt and for the confidence follow-up."""

from .interface import (
    AgentResponse,
    AgentTarget,
    AttemptRequest,
    ConfidenceRequest,
    ConfidenceResponse,
    InvocationError,
    IsolatedFollowUp,
)

__all__ = [
    "AgentResponse",
    "AgentTarget",
    "AttemptRequest",
    "ConfidenceRequest",
    "ConfidenceResponse",
    "InvocationError",
    "IsolatedFollowUp",
]

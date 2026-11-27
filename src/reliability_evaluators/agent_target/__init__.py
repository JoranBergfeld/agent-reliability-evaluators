"""Agent target: one interface for invoking an agent for an Attempt and for the confidence follow-up,
and the Foundry agent targets that implement it.
"""

from .foundry import AzureCliToken, FoundryProject
from .foundry_prompt_agent import FoundryPromptAgentTarget
from .interface import (
    AgentResponse,
    AgentTarget,
    AttemptRequest,
    ConfidenceRequest,
    ConfidenceResponse,
    InvocationError,
    IsolatedFollowUp,
    platform_tool_calls,
)

__all__ = [
    "AzureCliToken",
    "AgentResponse",
    "AgentTarget",
    "AttemptRequest",
    "ConfidenceRequest",
    "ConfidenceResponse",
    "FoundryProject",
    "FoundryPromptAgentTarget",
    "InvocationError",
    "IsolatedFollowUp",
    "platform_tool_calls",
]

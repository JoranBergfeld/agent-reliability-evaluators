"""Requests to the customer's Foundry project, sent as the customer's own identity.

Standard library only. `requests` sends one request with a bearer token that is shown nowhere:
not in an error, not in what an error is chained to, and not in what a traceback holds.
`agent_details` reads what the project says about an agent and keeps none of the agent's settings.
"""

from .agent_details import AgentDetails, read_agent_details
from .requests import Answer, BearerToken, NotAnswered, send

__all__ = ["AgentDetails", "Answer", "BearerToken", "NotAnswered", "read_agent_details", "send"]

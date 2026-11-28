"""Requests to the customer's Foundry project, sent as the customer's own identity.

Standard library only. This is the one place that reaches a Foundry project:

- `credential`: the caller's identity, a function that returns a bearer token. By default the Azure CLI's.
- `project`: `FoundryProject`, which every agent target and Foundry-managed scoring send their
  requests through, with the one rule for repeating a request.
- `requests`: one request, with a bearer token that is shown nowhere: not in an error, not in what
  an error is chained to, and not in what a traceback holds.
- `agent_details`: what the project says about an agent, of which none of the agent's settings is kept.
- `evaluation_runs`: what a run has ended as and how its output items are paged, for everything that makes runs.
"""

from .agent_details import AgentDetails, read_agent_details
from .credential import TOKEN_RESOURCE, AzureCliCredential
from .evaluation_runs import ENDED, OUTPUT_ITEM_PAGE, Project, output_items_of
from .project import FoundryProject, FoundryRequestError
from .requests import Answer, BearerToken, NotAnswered, send

__all__ = [
    "ENDED",
    "OUTPUT_ITEM_PAGE",
    "TOKEN_RESOURCE",
    "AgentDetails",
    "Answer",
    "AzureCliCredential",
    "BearerToken",
    "FoundryProject",
    "FoundryRequestError",
    "NotAnswered",
    "Project",
    "output_items_of",
    "read_agent_details",
    "send",
]

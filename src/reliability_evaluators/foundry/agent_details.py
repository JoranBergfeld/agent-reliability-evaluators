"""What a Foundry agent target keeps of what the project says about an agent.

`GET /agents/{name}` answers with the definition of the agent, and for a hosted agent that holds its
environment variables in plain text, which can hold a key. Nothing of the definition but its kind
is kept: the answer is read here, once, and dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentDetails:
    """What identifies an agent in its project, as the project reports it. What it does not report is None."""

    # "prompt" or "hosted".
    kind: Any = None
    # The protocols the agent's endpoint serves, such as "responses".
    protocols: Any = None
    latest_version: Any = None
    version_selection_rules: Any = None


def read_agent_details(answered: Any) -> AgentDetails:
    """The details to keep of what the project answered about an agent. The agent's settings are not among them."""
    details = _mapping(answered)
    latest = _mapping(_mapping(details.get("versions")).get("latest"))
    endpoint = _mapping(details.get("agent_endpoint"))
    return AgentDetails(
        kind=_mapping(latest.get("definition")).get("kind"),
        protocols=endpoint.get("protocols"),
        latest_version=latest.get("version"),
        version_selection_rules=_mapping(endpoint.get("version_selector")).get("version_selection_rules"),
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

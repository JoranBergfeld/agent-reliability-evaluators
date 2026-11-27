"""Confidence elicitation: the separate, confidence-only request that follows a completed Attempt."""

from __future__ import annotations

from typing import Any, Mapping

from ..agent_target import (
    AgentResponse,
    AgentTarget,
    AttemptRequest,
    ConfidenceRequest,
    ConfidenceResponse,
    InvocationFailed,
    IsolatedFollowUp,
)
from ..evidence import ConfidenceRating, ConfidenceUnavailable, read_confidence_response
from .declaration import Task

NOT_DECLARED = "the agent target does not declare whether it supports an isolated follow-up"
NOT_SUPPORTED = "the agent target does not support an isolated follow-up"
RULE_NOT_SHAREABLE = "the Task's success rule cannot be shared with the agent"
ATTEMPT_NOT_COMPLETED = "the Attempt did not complete, so no confidence request was sent"
SWEEP_CANCELLED = "the sweep was cancelled, so no confidence request was sent"
NO_RESPONSE = "the agent target returned no confidence response"
# What is recorded of a confidence request that was sent and of which the agent target recorded nothing.
NOTHING_RECORDED = "the agent target recorded nothing of the confidence request"


def _without_usage(why: str) -> dict[str, Any]:
    """A record of a confidence request with no usage, in the form the agent targets record one in."""
    return {"token_usage": None, "token_usage_missing": why}


def confidence_prompt(success_rule: str) -> str:
    """The question put to the agent. It asks how likely the task succeeded, never how likely it failed."""
    return (
        "The task above is finished. This is a question about it, not a new task: "
        "do not take any action and do not call any tool.\n\n"
        "The task counts as a success under this rule:\n"
        f"{success_rule}\n\n"
        "How likely is it that the task succeeded under this rule? "
        "Answer with one number from 0 to 100 and nothing else. "
        "0 means it certainly did not succeed and 100 means it certainly succeeded. "
        "Decimals are allowed."
    )


def elicit_confidence(
    task: Task, attempt: AttemptRequest, response: AgentResponse, agent_target: AgentTarget
) -> tuple[ConfidenceRating | ConfidenceUnavailable, Mapping[str, Any] | None]:
    """Ask the agent once how likely it thinks the completed Attempt succeeded.

    The agent is asked only when the target declares an isolated follow-up and the Task
    has a success rule that can be shared. There is one request per Attempt: an answer
    that cannot be used is recorded as unavailable and the agent is not asked again.
    The task response is never searched for a rating.

    Returns the confidence evidence, and what was recorded of the request: None when no
    request was sent. A request that was sent is a use of the agent of its own, whatever
    became of its answer. What the target recorded of it is kept as given. Where it
    recorded nothing, or the answer belongs to another Attempt, the record says that no
    usage is known for this Attempt and why.
    """
    declaration = getattr(agent_target, "isolated_follow_up", None)
    if not isinstance(declaration, IsolatedFollowUp):
        return ConfidenceUnavailable(reason=NOT_DECLARED), None
    if declaration.supported is not True:
        reason = f"{NOT_SUPPORTED}: {declaration.reason}" if declaration.reason else NOT_SUPPORTED
        return ConfidenceUnavailable(reason=reason), None
    if task.shareable_success_rule is None:
        return ConfidenceUnavailable(reason=RULE_NOT_SHAREABLE), None
    request = ConfidenceRequest(
        attempt=attempt,
        response=response,
        success_rule=task.shareable_success_rule,
        prompt=confidence_prompt(task.shareable_success_rule),
    )
    try:
        answer = agent_target.elicit_confidence(request)
    except Exception as error:
        failed = f"the confidence request failed: {type(error).__name__}: {error}"
        recorded = error.invocation if isinstance(error, InvocationFailed) else None
        return ConfidenceUnavailable(reason=failed), _recorded(recorded)
    if not isinstance(answer, ConfidenceResponse) or not isinstance(answer.output, str):
        return ConfidenceUnavailable(reason=NO_RESPONSE), _without_usage(NOTHING_RECORDED)
    if answer.attempt_id != attempt.attempt_id:
        elsewhere = f"the confidence response belongs to Attempt {answer.attempt_id!r}, not to this Attempt"
        # What that response used is not attributed to this Attempt.
        return ConfidenceUnavailable(reason=elsewhere), _without_usage(elsewhere)
    return read_confidence_response(answer.output), _recorded(answer.invocation)


def _recorded(invocation: Any) -> Mapping[str, Any]:
    """What an agent target recorded of a confidence request that was sent."""
    return invocation if isinstance(invocation, Mapping) and invocation else _without_usage(NOTHING_RECORDED)
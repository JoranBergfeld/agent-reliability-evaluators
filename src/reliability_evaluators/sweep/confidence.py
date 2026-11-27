"""Confidence elicitation: the separate, confidence-only request that follows a completed Attempt."""

from __future__ import annotations

from dataclasses import replace

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
) -> ConfidenceRating | ConfidenceUnavailable:
    """Ask the agent once how likely it thinks the completed Attempt succeeded.

    The agent is asked only when the target declares an isolated follow-up and the Task
    has a success rule that can be shared. There is one request per Attempt: an answer
    that cannot be used is recorded as unavailable and the agent is not asked again.
    The task response is never searched for a rating.
    """
    declaration = getattr(agent_target, "isolated_follow_up", None)
    if not isinstance(declaration, IsolatedFollowUp):
        return ConfidenceUnavailable(reason=NOT_DECLARED)
    if declaration.supported is not True:
        return ConfidenceUnavailable(
            reason=f"{NOT_SUPPORTED}: {declaration.reason}" if declaration.reason else NOT_SUPPORTED
        )
    if task.shareable_success_rule is None:
        return ConfidenceUnavailable(reason=RULE_NOT_SHAREABLE)
    request = ConfidenceRequest(
        attempt=attempt,
        response=response,
        success_rule=task.shareable_success_rule,
        prompt=confidence_prompt(task.shareable_success_rule),
    )
    try:
        answer = agent_target.elicit_confidence(request)
    except Exception as error:
        return ConfidenceUnavailable(
            reason=f"the confidence request failed: {type(error).__name__}: {error}",
            # What the agent target recorded of the request stays on the Attempt, however it ended.
            invocation=error.invocation if isinstance(error, InvocationFailed) else None,
        )
    if not isinstance(answer, ConfidenceResponse) or not isinstance(answer.output, str):
        return ConfidenceUnavailable(reason=NO_RESPONSE)
    if answer.attempt_id != attempt.attempt_id:
        return ConfidenceUnavailable(
            reason=f"the confidence response belongs to Attempt {answer.attempt_id!r}, not to this Attempt"
        )
    return replace(read_confidence_response(answer.output), invocation=answer.invocation)
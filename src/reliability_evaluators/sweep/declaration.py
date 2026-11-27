"""What a customer declares before an Evaluator sweep collects any evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..agent_target import AgentResponse, AttemptRequest
from ..evidence import TaskOutcome

# Decides the task outcome of one Attempt and says what it was decided from. It should
# look at independent evidence, such as the state of a service, not only at the response.
TaskSuccessRule = Callable[[AttemptRequest, AgentResponse], TaskOutcome]


@dataclass(frozen=True, kw_only=True)
class Task:
    """A unit of work for the agent, its task-success rule, and how many Attempts to make.

    `shareable_success_rule` is the task-success rule in words, as the agent may be shown
    it when it is asked for its confidence rating. Leave it out when the rule cannot be
    shared with the agent: the Task's Attempts then have no confidence rating.
    """

    task_id: str
    prompt: str
    success_rule: TaskSuccessRule
    attempts: int
    shareable_success_rule: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError(f"a Task is identified by a task_id, got {self.task_id!r}")
        if type(self.attempts) is not int or self.attempts < 1:
            raise ValueError(f"Task {self.task_id!r} needs at least one Attempt, got {self.attempts!r}")
        rule = self.shareable_success_rule
        if rule is not None and (not isinstance(rule, str) or not rule.strip()):
            raise ValueError(
                f"the shareable success rule of Task {self.task_id!r} is the rule in words, or left out. "
                f"Got {rule!r}"
            )

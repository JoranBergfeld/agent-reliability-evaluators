"""Fakes for driving the Evaluator sweep through its public entry point."""

from reliability_evaluators.agent_target import (
    AgentResponse,
    AttemptRequest,
    ConfidenceRequest,
    ConfidenceResponse,
    IsolatedFollowUp,
)


class FakeAgentTarget:
    """A scripted agent that works on a fake service.

    The script gives, per Task, what happens on each Attempt in order: "success" and
    "failure" leave the service in the right or the wrong state, and "error" makes the
    invocation itself fail. The agent always answers `output`, "Done." unless told
    otherwise, so its response says nothing about success; the task-success rule has to
    read `service_state`.

    `confidence` gives the agent's answer to the confidence request, by Attempt identity.
    An answer is one of:

    - text, which is returned as the agent's answer;
    - a function of the confidence request and this fake that returns that text;
    - an exception, which makes the request fail;
    - a `ConfidenceResponse`, which is returned as it is;
    - a list of these, answered one per request in order.

    The follow-up never changes `service_state`: this agent cannot act while it answers.
    `isolated_follow_up` is what the target declares about that; pass
    `IsolatedFollowUp(supported=False, reason=...)` for a target that cannot ensure it,
    or `None` for a target that makes no declaration.
    """

    def __init__(
        self,
        script: dict[str, list[str]],
        *,
        confidence: dict[str, object] | None = None,
        isolated_follow_up: IsolatedFollowUp | None = IsolatedFollowUp(supported=True),
        output: str = "Done.",
    ):
        self._remaining = {task_id: list(behaviours) for task_id, behaviours in script.items()}
        self._confidence = dict(confidence or {})
        self._output = output
        if isolated_follow_up is not None:
            self.isolated_follow_up = isolated_follow_up
        self.service_state: dict[str, str] = {}

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        behaviour = self._remaining[request.task_id].pop(0)
        if behaviour == "error":
            raise ConnectionError("the agent endpoint returned 503")
        self.service_state[request.attempt_id] = "done" if behaviour == "success" else "open"
        return AgentResponse(output=self._output)

    def elicit_confidence(self, request: ConfidenceRequest) -> ConfidenceResponse:
        attempt_id = request.attempt.attempt_id
        if attempt_id not in self._confidence:
            raise LookupError(f"the fake agent has no confidence answer scripted for {attempt_id!r}")
        answer = self._confidence[attempt_id]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, ConfidenceResponse):
            return answer
        if callable(answer):
            answer = answer(request, self)
        return ConfidenceResponse(attempt_id=attempt_id, output=answer)

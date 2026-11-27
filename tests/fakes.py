"""Fakes for driving the Evaluator sweep through its public entry point."""

from reliability_evaluators.agent_target import AgentResponse, AttemptRequest


class FakeAgentTarget:
    """A scripted agent that works on a fake service.

    The script gives, per Task, what happens on each Attempt in order: "success" and
    "failure" leave the service in the right or the wrong state, and "error" makes the
    invocation itself fail. The agent always answers "Done.", so its response says
    nothing about success; the task-success rule has to read `service_state`.
    """

    def __init__(self, script: dict[str, list[str]]):
        self._remaining = {task_id: list(behaviours) for task_id, behaviours in script.items()}
        self.service_state: dict[str, str] = {}

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        behaviour = self._remaining[request.task_id].pop(0)
        if behaviour == "error":
            raise ConnectionError("the agent endpoint returned 503")
        self.service_state[request.attempt_id] = "done" if behaviour == "success" else "open"
        return AgentResponse(output="Done.")

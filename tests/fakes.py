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


class FakeActingAgentTarget(FakeAgentTarget):
    """A scripted agent whose tool calls are logged by the fake service.

    `actions` gives, per Task, the tool names the agent calls on each Attempt, in order.
    The service logs each call under the Attempt's identity with its tool name,
    arguments, result and error; `broken_tool` always ends in an error. An Attempt
    scripted with None instead of a list is not logged at all, which is different from
    an Attempt that is logged with no calls.
    """

    def __init__(self, script: dict[str, list[str]], actions: dict[str, list[list[str] | None]]):
        super().__init__(script)
        self._actions = {task_id: list(sequences) for task_id, sequences in actions.items()}
        self.service_log: dict[str, list[dict]] = {}

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        tool_names = self._actions[request.task_id].pop(0)
        response = super().invoke(request)
        if tool_names is not None:
            self.service_log[request.attempt_id] = [self._call(name, request) for name in tool_names]
        return response

    @staticmethod
    def _call(tool_name: str, request: AttemptRequest) -> dict:
        failed = tool_name == "broken_tool"
        return {
            "tool_name": tool_name,
            "arguments": {"item": request.task_id},
            "result": None if failed else "ok",
            "error": "503 unavailable" if failed else None,
        }

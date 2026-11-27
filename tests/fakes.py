"""Fakes for driving the Evaluator sweep through its public entry point."""

from reliability_evaluators.agent_target import (
    AgentResponse,
    AttemptRequest,
    ConfidenceRequest,
    ConfidenceResponse,
    IsolatedFollowUp,
)
from reliability_evaluators.evidence import ResourceMeasurement

TOOL_CALL_BOUNDARY = "tool calls made on the fake service during the Attempt, counted by the fake agent target"
OWN_ELAPSED_BOUNDARY = "processing time of the Attempt, as reported by the fake agent service"


def attempt_evidence(attempt: dict, *evidence: str) -> dict:
    """An Attempt in the scorecard: what every Attempt carries, and the evidence named.

    A test names the evidence it is about, so that it does not depend on the evidence
    that other Evaluators attach to the Attempt.
    """
    carried = ("attempt_id", "task_id", "execution_status", "execution_error", "task_outcome")
    return {key: attempt[key] for key in (*carried, *evidence)}


class FakeAgentTarget:
    """A scripted agent that works on a fake service.

    The script gives, per Task, what happens on each Attempt in order: "success" and
    "failure" leave the service in the right or the wrong state, and "error" makes the
    invocation itself fail. The agent always answers `output`, "Done." unless told
    otherwise, so its response says nothing about success; the task-success rule has to
    read `service_state`.

    "success" or "failure" followed by " and deletes unrelated" also deletes the seeded
    item the agent was told to leave alone. `unrelated_item` holds what became of it.

    `resources` gives, per Task, what each Attempt uses, in order. "seconds" is how long
    the invocation takes on `clock`, which the sweep is given as its clock. "tool_calls"
    is a count the agent reports; without it the agent reports no count. "own_seconds"
    is an elapsed time the agent reports itself. An Attempt with no entry takes no time
    and reports nothing.

    `confidence` gives the agent's answer to the confidence request, by Attempt identity.
    An answer is one of:

    - text, which is returned as the agent's answer;
    - a function of the confidence request and this fake that returns that text;
    - an exception, which makes the request fail;
    - a `ConfidenceResponse`, which is returned as it is;
    - a list of these, answered one per request in order.

    The follow-up never changes `service_state` or `unrelated_item`: this agent cannot act
    while it answers. `isolated_follow_up` is what the target declares about that; pass
    `IsolatedFollowUp(supported=False, reason=...)` for a target that cannot ensure it,
    or `None` for a target that makes no declaration.
    """

    def __init__(
        self,
        script: dict[str, list[str]],
        resources: dict[str, list[dict]] | None = None,
        *,
        confidence: dict[str, object] | None = None,
        isolated_follow_up: IsolatedFollowUp | None = IsolatedFollowUp(supported=True),
        output: str = "Done.",
    ):
        self._remaining = {task_id: list(behaviours) for task_id, behaviours in script.items()}
        self._resources = {task_id: list(used) for task_id, used in (resources or {}).items()}
        self._now = 0.0
        self._confidence = dict(confidence or {})
        self._output = output
        if isolated_follow_up is not None:
            self.isolated_follow_up = isolated_follow_up
        self.service_state: dict[str, str] = {}
        self.unrelated_item: dict[str, str] = {}

    def clock(self) -> float:
        return self._now

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        behaviour, _, side_effect = self._remaining[request.task_id].pop(0).partition(" and ")
        scripted = self._resources.get(request.task_id)
        used = scripted.pop(0) if scripted else {}
        self._now += used.get("seconds", 0.0)
        if behaviour == "error":
            raise ConnectionError("the agent endpoint returned 503")
        self.service_state[request.attempt_id] = "done" if behaviour == "success" else "open"
        self.unrelated_item[request.attempt_id] = "deleted" if side_effect == "deletes unrelated" else "unchanged"
        measurements = []
        if "own_seconds" in used:
            measurements.append(
                ResourceMeasurement(
                    resource_type="elapsed_time",
                    value=used["own_seconds"],
                    unit="seconds",
                    boundary=OWN_ELAPSED_BOUNDARY,
                )
            )
        if "tool_calls" in used:
            measurements.append(
                ResourceMeasurement(
                    resource_type="tool_calls", value=used["tool_calls"], unit="calls", boundary=TOOL_CALL_BOUNDARY
                )
            )
        return AgentResponse(output=self._output, resource_measurements=tuple(measurements))

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

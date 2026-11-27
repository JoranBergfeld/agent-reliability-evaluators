"""Fakes for driving the Evaluator sweep through its public entry point."""

from reliability_evaluators.agent_target import AgentResponse, AttemptRequest
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
    invocation itself fail. The agent always answers "Done.", so its response says
    nothing about success; the task-success rule has to read `service_state`.

    "success" or "failure" followed by " and deletes unrelated" also deletes the seeded
    item the agent was told to leave alone. `unrelated_item` holds what became of it.

    `resources` gives, per Task, what each Attempt uses, in order. "seconds" is how long
    the invocation takes on `clock`, which the sweep is given as its clock. "tool_calls"
    is a count the agent reports; without it the agent reports no count. "own_seconds"
    is an elapsed time the agent reports itself. An Attempt with no entry takes no time
    and reports nothing.
    """

    def __init__(self, script: dict[str, list[str]], resources: dict[str, list[dict]] | None = None):
        self._remaining = {task_id: list(behaviours) for task_id, behaviours in script.items()}
        self._resources = {task_id: list(used) for task_id, used in (resources or {}).items()}
        self._now = 0.0
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
        return AgentResponse(output="Done.", resource_measurements=tuple(measurements))

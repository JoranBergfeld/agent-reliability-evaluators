"""Fakes for driving the Evaluator sweep through its public entry point."""

import threading
from dataclasses import dataclass

from reliability_evaluators.agent_target import (
    AgentResponse,
    AttemptRequest,
    ConfidenceRequest,
    ConfidenceResponse,
    IsolatedFollowUp,
)
from reliability_evaluators.evidence import ObservedCondition, ResourceMeasurement

TOOL_CALL_BOUNDARY = "tool calls made on the fake service during the Attempt, counted by the fake agent target"
OWN_ELAPSED_BOUNDARY = "processing time of the Attempt, as reported by the fake agent service"


def attempt_evidence(attempt: dict, *evidence: str) -> dict:
    """An Attempt in the scorecard: what every Attempt carries, and the evidence named.

    A test names the evidence it is about, so that it does not depend on the evidence
    that other Evaluators attach to the Attempt.
    """
    carried = ("attempt_id", "task_id", "execution_status", "execution_error", "task_outcome")
    return {key: attempt[key] for key in (*carried, *evidence)}


@dataclass(frozen=True)
class Scripted:
    """One scripted invocation: what happens, and what else happens while it is in flight."""

    behaviour: str
    # How long the invocation takes on the fake's clock.
    seconds: float = 0.0
    # The caller asks for the sweep to be cancelled while this invocation is in flight.
    cancels: bool = False


def taking(seconds: float, behaviour: str) -> Scripted:
    return Scripted(behaviour, seconds)


def cancelling(behaviour: str) -> Scripted:
    return Scripted(behaviour, cancels=True)


class FakeAgentTarget:
    """A scripted agent that works on a fake service.

    The script gives, per Task, what happens on each invocation in order: "success" and
    "failure" leave the service in the right or the wrong state, "error" makes the
    invocation itself fail, and "aborted" makes it end without an answer because the caller
    closed the connection. The agent always answers `output`, "Done." unless told
    otherwise, so its response says nothing about success; the task-success rule has to
    read `service_state`.

    `taking(seconds, behaviour)` makes an invocation take that long on `clock`. The agent
    stops at the time limit it is given and raises TimeoutError, unless it was built with
    `honours_time_limits=False`. `cancelling(behaviour)` sets `cancel_requested` while the
    invocation is in flight.

    "success" or "failure" followed by " and deletes unrelated" also deletes the seeded
    item the agent was told to leave alone. `unrelated_item` holds what became of it.

    `resources` gives, per Task, what each Attempt uses, in order. "seconds" is how long
    the invocation takes on `clock`, which the sweep is given as its clock. "tool_calls"
    is a count the agent reports; without it the agent reports no count. "own_seconds"
    is an elapsed time the agent reports itself. An Attempt with no entry takes no time
    and reports nothing.

    `invocations` gives, by Attempt identity, what the agent target recorded of the
    invocation. The agent returns it with its response. Without it the agent returns none.
    `invocation_errors` gives, by Attempt identity, the error an "error" Attempt fails with.

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
        script: dict[str, list[str | Scripted]],
        resources: dict[str, list[dict]] | None = None,
        *,
        confidence: dict[str, object] | None = None,
        invocations: dict[str, dict] | None = None,
        invocation_errors: dict[str, Exception] | None = None,
        isolated_follow_up: IsolatedFollowUp | None = IsolatedFollowUp(supported=True),
        output: str = "Done.",
        honours_time_limits: bool = True,
    ):
        self._remaining = {
            task_id: [step if isinstance(step, Scripted) else Scripted(step) for step in behaviours]
            for task_id, behaviours in script.items()
        }
        self._honours_time_limits = honours_time_limits
        self.cancel_requested = False
        self._resources = {task_id: list(used) for task_id, used in (resources or {}).items()}
        self._now = 0.0
        self._confidence = dict(confidence or {})
        self._invocations = dict(invocations or {})
        self._invocation_errors = dict(invocation_errors or {})
        self._output = output
        if isolated_follow_up is not None:
            self.isolated_follow_up = isolated_follow_up
        self.service_state: dict[str, str] = {}
        self.unrelated_item: dict[str, str] = {}

    def clock(self) -> float:
        return self._now

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        step = self._remaining[request.task_id].pop(0)
        behaviour, _, side_effect = step.behaviour.partition(" and ")
        scripted = self._resources.get(request.task_id)
        used = scripted.pop(0) if scripted else {}
        seconds = used.get("seconds", 0.0) + step.seconds
        limit = request.timeout_seconds
        if self._honours_time_limits and limit is not None and seconds > limit:
            self._now += limit
            raise TimeoutError(f"no answer within {limit:g} seconds")
        self._now += seconds
        if step.cancels:
            self.cancel_requested = True
        if behaviour == "error":
            raise self._invocation_errors.get(
                request.attempt_id, ConnectionError("the agent endpoint returned 503")
            )
        if behaviour == "aborted":
            raise ConnectionAbortedError("the caller closed the connection")
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
        return AgentResponse(
            output=self._output,
            resource_measurements=tuple(measurements),
            invocation=self._invocations.get(request.attempt_id),
        )

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
    an Attempt that is logged with no calls. Everything else is given as for FakeAgentTarget.
    """

    def __init__(
        self,
        script: dict[str, list[str]],
        actions: dict[str, list[list[str] | None]],
        resources: dict[str, list[dict]] | None = None,
        **options,
    ):
        super().__init__(script, resources, **options)
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


class MeetingAgentTarget:
    """An agent that answers only when two invocations are in flight at the same time.

    An invocation waits up to `wait_seconds` for another one to arrive. If none does, it
    fails, and so does every later invocation. Used to show through the scorecard whether
    Attempts overlapped.
    """

    def __init__(self, *, wait_seconds: float):
        self._meeting = threading.Barrier(2, timeout=wait_seconds)

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        try:
            self._meeting.wait()
        except threading.BrokenBarrierError:
            raise ConnectionError("no other invocation was in flight") from None
        return AgentResponse(output="Done.")


def todo_list(task_id: str) -> str:
    """The test resource the fake to-do agent works on for a Task."""
    return f"todo-list/{task_id}"


class FakeTodoService:
    """A to-do service whose lists outlive Attempts until a customer control resets them.

    Every list starts with an item left over from an earlier run, so nothing is fresh
    unless a control makes it so. While `failing` is set, adding an item fails. Importing
    items is a second way to fill a list, and it never fails.
    """

    def __init__(self, task_ids):
        self.lists: dict[str, list[str]] = {todo_list(task_id): ["left over"] for task_id in task_ids}
        self.failing = False
        self.faulted_requests = 0

    def add(self, list_id: str, title: str) -> None:
        if self.failing:
            self.faulted_requests += 1
            raise ConnectionError("503 from the to-do service")
        self.lists.setdefault(list_id, []).append(title)

    def import_items(self, list_id: str, titles: list[str]) -> None:
        self.lists.setdefault(list_id, []).extend(titles)


class FakeTodoAgent:
    """An agent that adds one item to its Task's list and never resets anything.

    It answers "Done." even when the service refused the item, so only the state of the
    service says whether the Task succeeded. Invocations named in `unreachable_for` fail.

    `went_wrong` scripts what else happens to an invocation, by Attempt identity:

    - "times out": it raises TimeoutError before the agent did anything;
    - "drops after adding": the agent adds its item and the connection then drops;
    - "aborted by cancellation": the caller cancels the sweep and closes the connection;
    - "cancelled while answering": the caller cancels the sweep, and the agent still answers.

    `cancel_requested` says whether the caller asked for the sweep to be cancelled.
    """

    def __init__(self, service: FakeTodoService, unreachable_for=(), went_wrong=None):
        self._service = service
        self._unreachable_for = set(unreachable_for)
        self._went_wrong = dict(went_wrong or {})
        self.cancel_requested = False

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        if request.attempt_id in self._unreachable_for:
            raise ConnectionError("the agent endpoint returned 503")
        went_wrong = self._went_wrong.get(request.attempt_id)
        if went_wrong == "times out":
            raise TimeoutError("no answer from the agent endpoint")
        if went_wrong in ("aborted by cancellation", "cancelled while answering"):
            self.cancel_requested = True
        if went_wrong == "aborted by cancellation":
            raise ConnectionAbortedError("the caller closed the connection")
        try:
            self._service.add(todo_list(request.task_id), "buy milk")
        except ConnectionError:
            pass
        if went_wrong == "drops after adding":
            raise ConnectionError("the connection dropped")
        return AgentResponse(output="Done.")


class FakeScriptedTodoAgent(FakeTodoAgent):
    """A to-do agent whose Attempts can be scripted, by Attempt identity.

    Unscripted, it adds one item and gives up when the service refuses it. Scripted:

    - "works around": when the service refuses the item, it imports the item instead;
    - "imports": it imports the item and never asks the service to add one, so it never
      meets a fault in adding;
    - "forgets": it does nothing.

    Whatever happens, it answers "Done.". `invocations` counts how often it was invoked.
    """

    def __init__(self, service: FakeTodoService, script=None, unreachable_for=()):
        super().__init__(service, unreachable_for)
        self._script = dict(script or {})
        self.invocations = 0

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        self.invocations += 1
        behaviour = self._script.get(request.attempt_id)
        if behaviour is None:
            return super().invoke(request)
        list_id = todo_list(request.task_id)
        if behaviour == "works around":
            try:
                self._service.add(list_id, "buy milk")
            except ConnectionError:
                self._service.import_items(list_id, ["buy milk"])
        elif behaviour == "imports":
            self._service.import_items(list_id, ["buy milk"])
        elif behaviour != "forgets":
            raise ValueError(f"the fake agent has no behaviour {behaviour!r}")
        return AgentResponse(output="Done.")


class FakeSelfAssessingTodoAgent(FakeTodoAgent):
    """A to-do agent that can be asked for its confidence rating in an isolated follow-up.

    It answers from what it sees when it is asked: 100 when its Task's list holds exactly
    one item, otherwise 0. It cannot act while it answers. An invocation takes 2 seconds
    on `clock` and the follow-up takes 5.
    """

    isolated_follow_up = IsolatedFollowUp(supported=True)

    def __init__(self, service: FakeTodoService, unreachable_for=(), went_wrong=None):
        super().__init__(service, unreachable_for, went_wrong)
        self._now = 0.0

    def clock(self) -> float:
        return self._now

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        self._now += 2.0
        return super().invoke(request)

    def elicit_confidence(self, request: ConfidenceRequest) -> ConfidenceResponse:
        self._now += 5.0
        items = self._service.lists.get(todo_list(request.attempt.task_id), [])
        return ConfidenceResponse(attempt_id=request.attempt.attempt_id, output="100" if len(items) == 1 else "0")


class FakeControls:
    """Customer controls for the fake to-do service.

    They change only the test resources the experiment is authorized to change. A
    condition whose parameters say `add_fails` makes adding an item fail.

    `failures` scripts a control to fail: {(lifecycle point, attempt_id): message}.
    For the Attempts in `misapplied_for` the apply control does the opposite of what the
    intended condition says, without reporting it.
    """

    SOURCE = "request log of the fake to-do service"

    def __init__(self, service: FakeTodoService, failures=None, misapplied_for=()):
        self._service = service
        self._failures = dict(failures or {})
        self._misapplied_for = set(misapplied_for)

    def prepare_state(self, context) -> None:
        self._fail_if_scripted("prepare_state", context)
        for resource in context.authorized_resources:
            self._service.lists[resource] = []

    def apply_condition(self, context) -> None:
        self._fail_if_scripted("apply_condition", context)
        intended = self._fault_intended(context)
        self._service.failing = intended != (context.attempt_id in self._misapplied_for)
        self._service.faulted_requests = 0

    def verify_condition(self, context) -> ObservedCondition:
        self._fail_if_scripted("verify_condition", context)
        faulted = self._service.faulted_requests
        return ObservedCondition(
            met=(faulted > 0) == self._fault_intended(context),
            source=self.SOURCE,
            observations={"faulted_requests": faulted},
        )

    def clean_up(self, context) -> None:
        self._fail_if_scripted("clean_up", context)
        self._service.failing = False
        for resource in context.authorized_resources:
            self._service.lists.pop(resource, None)

    @staticmethod
    def _fault_intended(context) -> bool:
        return bool(context.condition.parameters.get("add_fails"))

    def _fail_if_scripted(self, point: str, context) -> None:
        message = self._failures.get((point, context.attempt_id))
        if message is not None:
            raise RuntimeError(message)

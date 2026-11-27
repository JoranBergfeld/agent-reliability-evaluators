"""Fakes for driving the Evaluator sweep through its public entry point."""

from reliability_evaluators.agent_target import AgentResponse, AttemptRequest
from reliability_evaluators.evidence import ObservedCondition


class FakeAgentTarget:
    """A scripted agent that works on a fake service.

    The script gives, per Task, what happens on each Attempt in order: "success" and
    "failure" leave the service in the right or the wrong state, and "error" makes the
    invocation itself fail. The agent always answers "Done.", so its response says
    nothing about success; the task-success rule has to read `service_state`.

    "success" or "failure" followed by " and deletes unrelated" also deletes the seeded
    item the agent was told to leave alone. `unrelated_item` holds what became of it.
    """

    def __init__(self, script: dict[str, list[str]]):
        self._remaining = {task_id: list(behaviours) for task_id, behaviours in script.items()}
        self.service_state: dict[str, str] = {}
        self.unrelated_item: dict[str, str] = {}

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        behaviour, _, side_effect = self._remaining[request.task_id].pop(0).partition(" and ")
        if behaviour == "error":
            raise ConnectionError("the agent endpoint returned 503")
        self.service_state[request.attempt_id] = "done" if behaviour == "success" else "open"
        self.unrelated_item[request.attempt_id] = "deleted" if side_effect == "deletes unrelated" else "unchanged"
        return AgentResponse(output="Done.")


def todo_list(task_id: str) -> str:
    """The test resource the fake to-do agent works on for a Task."""
    return f"todo-list/{task_id}"


class FakeTodoService:
    """A to-do service whose lists outlive Attempts until a customer control resets them.

    Every list starts with an item left over from an earlier run, so nothing is fresh
    unless a control makes it so. While `failing` is set, adding an item fails.
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


class FakeTodoAgent:
    """An agent that adds one item to its Task's list and never resets anything.

    It answers "Done." even when the service refused the item, so only the state of the
    service says whether the Task succeeded. Invocations named in `unreachable_for` fail.
    """

    def __init__(self, service: FakeTodoService, unreachable_for=()):
        self._service = service
        self._unreachable_for = set(unreachable_for)

    def invoke(self, request: AttemptRequest) -> AgentResponse:
        if request.attempt_id in self._unreachable_for:
            raise ConnectionError("the agent endpoint returned 503")
        try:
            self._service.add(todo_list(request.task_id), "buy milk")
        except ConnectionError:
            pass
        return AgentResponse(output="Done.")


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

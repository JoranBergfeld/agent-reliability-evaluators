"""What a customer declares to attempt the fixture's Task with an Evaluator sweep.

The controls, the task-success rule and the experiments here work on the fixture's to-do
service through its control interface, so the same declarations drive the service on this
machine and the deployed one.
"""

from __future__ import annotations

from live_fixture import TASK_PROMPT, TodoService, strict_task_outcome, task_prompt
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import Condition, Experiment, ObservedCondition, Task

EVENTS_SOURCE = "events of the to-do service"
ALL_TOOLS_WORK = Condition(condition_id="all-tools-work", description="Every tool of the to-do service works.")


def update_fails_on(*attempt_numbers: int) -> Condition:
    """A condition under which the service's update tool answers 503 in the numbered Attempts only."""
    return Condition(
        condition_id="update-fails-intermittently",
        description=f"The update tool of the to-do service answers 503 in Attempts {list(attempt_numbers)}.",
        parameters={"failing_tool": "update", "in_attempts": list(attempt_numbers)},
    )


class TodoSetup:
    """The customer's side of the fixture: its controls and its task-success rule.

    The sweep's Attempt identities contain slashes and repeat from run to run, and the
    service keeps one state per identifier in its path. So every Attempt gets an identifier
    of its own on the service, `on_service(attempt_id)`, which starts with `run_id`.

    `evidence` keeps what the service recorded for each Attempt, read just before the
    Attempt's state is removed, by Attempt identity.
    """

    def __init__(self, service: TodoService, run_id: str):
        self._service = service
        self._run_id = run_id
        self.evidence: dict[str, dict] = {}

    def on_service(self, attempt_id: str) -> str:
        return f"{self._run_id}.{attempt_id.replace('/', '.')}"

    def instruction(self, request) -> str:
        """What the agent is sent for an Attempt: the Task's prompt, for the Attempt's own state."""
        return task_prompt(self.on_service(request.attempt_id))

    def strict_success(self, request, response) -> TaskOutcome:
        outcome = strict_task_outcome(self._service.evidence(self.on_service(request.attempt_id)))
        return TaskOutcome(success=outcome["success"], source=outcome["source"])

    def prepare_state(self, context) -> None:
        self._service.prepare_attempt(self.on_service(context.attempt_id))

    def apply_condition(self, context) -> None:
        self._service.set_failing_tool(self.on_service(context.attempt_id), self._failing_tool(context))

    def verify_condition(self, context) -> ObservedCondition:
        events = self._service.evidence(self.on_service(context.attempt_id))["events"]
        faulted = [event["tool"] for event in events if event["fault_injected"]]
        intended = self._failing_tool(context)
        return ObservedCondition(
            met=set(faulted) == ({intended} if intended else set()),
            source=EVENTS_SOURCE,
            observations={"faulted_calls": faulted},
        )

    def clean_up(self, context) -> None:
        on_service = self.on_service(context.attempt_id)
        self.evidence[context.attempt_id] = self._service.evidence(on_service)
        self._service.remove_attempt(on_service)

    @staticmethod
    def _failing_tool(context) -> str | None:
        parameters = context.condition.parameters
        number = int(context.attempt_id.rsplit("-", 1)[1])
        return parameters["failing_tool"] if number in parameters.get("in_attempts", ()) else None

    def experiment(self, task_id: str, attempts: int, baseline: Condition, agent_configuration: dict) -> Experiment:
        """The fixture's Task attempted under one condition, from fresh state every time."""
        return Experiment(
            experiment_id=task_id,
            task=Task(task_id=task_id, prompt=TASK_PROMPT, success_rule=self.strict_success, attempts=attempts),
            agent_configuration=agent_configuration,
            starting_state="fresh",
            baseline=baseline,
            variation=None,
            controls=self,
            write_capable=True,
            authorized_resources=[f"to-do service state of the Attempts of {task_id} in run {self._run_id}"],
        )

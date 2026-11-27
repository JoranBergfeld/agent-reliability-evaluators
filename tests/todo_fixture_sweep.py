"""The live fixture's Task, declared for an Evaluator sweep on one to-do service.

The default tests use it on the service run on this machine and the live checks on the
deployed one. Either way the task outcome and the observed condition come from what the
service recorded, never from the agent's response.
"""

from live_fixture import TASK_PROMPT, TodoServiceError, strict_task_outcome
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import Condition, Experiment, ObservedCondition, Task

CONDITION_SOURCE = "events of the to-do service"
BASELINE = Condition(condition_id="baseline", description="Every tool works.")
UPDATE_FAILS = Condition(
    condition_id="update-fails",
    kind="fault",
    description="The update tool answers HTTP 503 for this Attempt.",
    parameters={"failing_tool": "update"},
)


class TodoFixtureSweep:
    """The customer's side of a sweep on the fixture: its controls and its task-success rule.

    Each Attempt has its own state on the service, named after `run_id` and the identity of
    the Attempt. The agent cannot be configured per Attempt, so `compose_input` puts that
    name in front of the Task's prompt.

    `service_evidence` holds what the service recorded for each Attempt, by Attempt
    identity, read by the clean-up control just before it removes the Attempt. A test
    calculates its own expectation from it.
    """

    def __init__(self, service, run_id):
        self._service = service
        self._run_id = run_id
        self.service_evidence = {}

    def state_name(self, attempt_id):
        return f"{self._run_id}-{attempt_id.replace('/', '-')}"

    def compose_input(self, request):
        return f"Use attempt_id {self.state_name(request.attempt_id)}. {request.prompt}"

    def success_rule(self, request, response):
        outcome = strict_task_outcome(self._service.evidence(self.state_name(request.attempt_id)))
        return TaskOutcome(success=outcome["success"], source=outcome["source"])

    def experiment(self, *, attempts, variation=None, experiment_id="todo", agent_configuration=None):
        """The fixture Task under fresh state: `attempts` Attempts under the baseline and under the variation."""
        return Experiment(
            experiment_id=experiment_id,
            task=Task(task_id="todo", prompt=TASK_PROMPT, success_rule=self.success_rule, attempts=attempts),
            agent_configuration=agent_configuration or {"agent": "todo-prompt"},
            starting_state="fresh",
            baseline=BASELINE,
            variation=variation,
            controls=self,
            write_capable=True,
            authorized_resources=[f"Attempts of run {self._run_id} on the to-do service"],
        )

    def prepare_state(self, context):
        self._service.prepare_attempt(self.state_name(context.attempt_id))

    def apply_condition(self, context):
        self._service.set_failing_tool(
            self.state_name(context.attempt_id), context.condition.parameters.get("failing_tool")
        )

    def verify_condition(self, context):
        events = self._service.evidence(self.state_name(context.attempt_id))["events"]
        faulted = [event["tool"] for event in events if event["fault_injected"]]
        intended = context.condition.parameters.get("failing_tool")
        return ObservedCondition(
            met=bool(faulted) and set(faulted) == {intended} if intended else not faulted,
            source=CONDITION_SOURCE,
            observations={"calls": len(events), "faulted_calls": faulted},
        )

    def clean_up(self, context):
        name = self.state_name(context.attempt_id)
        try:
            self.service_evidence[context.attempt_id] = self._service.evidence(name)
        except TodoServiceError as error:
            if "HTTP 404" in str(error):
                # The Attempt was never prepared, so there is nothing to remove.
                return
            raise
        self._service.remove_attempt(name)

"""The live fixture's Task, declared for an Evaluator sweep on one to-do service.

The default tests use it on the service run on this machine and the live checks on the
deployed one. Either way the task outcome and the observed condition come from what the
service recorded, never from the agent's response.
"""

from live_fixture import TodoServiceError, strict_task_outcome, task_prompt
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import Condition, ExecutionLimits, Experiment, ObservedCondition, Task

CONDITION_SOURCE = "events of the to-do service"
BASELINE = Condition(condition_id="baseline", description="Every tool works.")
UPDATE_FAILS = Condition(
    condition_id="update-fails",
    kind="fault",
    description="The update tool answers HTTP 503.",
    parameters={"failing_tool": "update"},
)


class TodoFixtureSweep:
    """The customer's side of a sweep on the fixture: its controls and its task-success rule.

    The agent works on one state of the service, named `state`, which is the experiment's
    only test resource. The Task's prompt names it, so the prompt is the same for every
    Attempt. The prepare control creates that state and the clean-up control removes it.

    `service_evidence` holds what the service had recorded for the state when each Attempt
    ended, by Attempt identity, read by the verify control. Under continuing state that
    includes the calls of the Attempts before it. A test calculates its own expectation from it.
    """

    def __init__(self, service, state):
        self._service = service
        self.state = state
        self.service_evidence = {}
        # How many calls the service had recorded for the state when the Attempt before ended.
        self._calls_before = 0

    def success_rule(self, request, response):
        outcome = strict_task_outcome(self._service.evidence(self.state))
        return TaskOutcome(success=outcome["success"], source=outcome["source"])

    def experiment(
        self,
        *,
        attempts,
        starting_state="fresh",
        variation=None,
        limits=ExecutionLimits(),
        agent_configuration=None,
    ):
        """The fixture Task: `attempts` Attempts under the baseline and as many under the variation."""
        return Experiment(
            experiment_id="todo",
            task=Task(
                task_id="todo",
                prompt=task_prompt(self.state),
                success_rule=self.success_rule,
                attempts=attempts,
                limits=limits,
            ),
            agent_configuration=agent_configuration or {"agent": "todo-prompt"},
            starting_state=starting_state,
            baseline=BASELINE,
            variation=variation,
            controls=self,
            write_capable=True,
            authorized_resources=[f"state {self.state} of the to-do service"],
        )

    def prepare_state(self, context):
        self._service.prepare_attempt(self.state)
        self._calls_before = 0

    def apply_condition(self, context):
        self._service.set_failing_tool(self.state, context.condition.parameters.get("failing_tool"))

    def verify_condition(self, context):
        evidence = self._service.evidence(self.state)
        self.service_evidence[context.attempt_id] = evidence
        calls = evidence["events"][self._calls_before :]
        self._calls_before = len(evidence["events"])
        faulted = [event["tool"] for event in calls if event["fault_injected"]]
        intended = context.condition.parameters.get("failing_tool")
        return ObservedCondition(
            met=bool(faulted) and set(faulted) == {intended} if intended else not faulted,
            source=CONDITION_SOURCE,
            observations={"calls": len(calls), "faulted_calls": faulted},
        )

    def clean_up(self, context):
        try:
            self._service.remove_attempt(self.state)
        except TodoServiceError as error:
            # A state that was never prepared is not there to remove.
            if "HTTP 404" not in str(error):
                raise

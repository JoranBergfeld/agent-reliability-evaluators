"""Evaluator sweep seam: environment robustness, from declared experiments to the reliability scorecard.

The sweep is driven through its public entry point with a fake agent target and fake
customer controls. Every assertion is on the machine-readable scorecard, or on what the
fake service and the fake agent were asked. Expected values are calculated by hand.

Every list is prepared with the item "buy milk". The Task is to make sure that the item
is on the list, and it succeeds when the list then holds exactly one item. The fake agent
reads the list and adds the item when it does not find it. It knows the presentation of
the baseline only: under another presentation it does not find the item, and adds a
second one. The agent answers "Done." whatever happened.
"""

import json

import pytest

from reliability_evaluators.agent_target import AgentResponse
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import Condition, Experiment, ObservedCondition, Task, run_sweep
from tests.fakes import FakeTodoService, todo_list

OUTCOME_SOURCE = "final state of the fake to-do service"
SERVICE_LOG = "request and response log of the fake to-do service"
PROMPT = "Make sure that 'buy milk' is on the list."
BASELINE = Condition(condition_id="baseline", description="The to-do service presents a list as it does in production.")
# The transformation the customer supplies: the same items, wrapped and with another field name.
WRAPPED = {"wrap_in": "data", "rename_fields": {"title": "name"}}
WRAPPED_AND_RENAMED = Condition(
    condition_id="wrapped-and-renamed",
    kind="environment",
    description="The to-do service wraps the same list in 'data' and calls the title of an item its name.",
    parameters=WRAPPED,
)
ADD_FAILS = Condition(
    condition_id="add-fails",
    kind="fault",
    description="The to-do service refuses to add an item.",
    parameters={"add_fails": True},
)


class PresentingTodoService(FakeTodoService):
    """A to-do service that can present the same list in a second, equivalent way.

    `read` returns {"items": [{"title": ...}]}. While `presentation` holds a transformation,
    it returns the same items transformed. `presented_responses` counts the responses it
    served transformed, and `presentations` keeps every transformation it was ever given.
    """

    def __init__(self, task_ids):
        super().__init__(task_ids)
        self.presentation = None
        self.presented_responses = 0
        self.presentations = []

    def present(self, transformation):
        self.presentation = transformation or None
        self.presented_responses = 0
        if transformation:
            self.presentations.append(transformation)

    def read(self, list_id):
        titles = self.lists.get(list_id, [])
        if self.presentation is None:
            return {"items": [{"title": title} for title in titles]}
        self.presented_responses += 1
        field = self.presentation["rename_fields"]["title"]
        return {self.presentation["wrap_in"]: {"items": [{field: title} for title in titles]}}


class ListReadingAgent:
    """An agent that reads its Task's list and adds "buy milk" when it does not find it there.

    Unscripted, it understands the presentation of the baseline only. Scripted, by Attempt identity:

    - "adapts": it also understands a list that is wrapped and whose titles are called names;
    - "leaves it without reading": it does nothing, so it is served no response;
    - "adds without reading": it adds the item without looking, so it is served no response.

    It answers "Done." whatever happened. `prompts` keeps the prompt of every invocation.
    """

    def __init__(self, service, script=None):
        self._service = service
        self._script = dict(script or {})
        self.prompts = []

    def invoke(self, request):
        self.prompts.append(request.prompt)
        behaviour = self._script.get(request.attempt_id)
        list_id = todo_list(request.task_id)
        if behaviour == "leaves it without reading":
            found = True
        elif behaviour == "adds without reading":
            found = False
        else:
            response = self._service.read(list_id)
            titles = [item.get("title") for item in response.get("items", [])]
            if behaviour == "adapts":
                titles += [item.get("name") for item in response.get("data", {}).get("items", [])]
            found = "buy milk" in titles
        if not found:
            try:
                self._service.add(list_id, "buy milk")
            except ConnectionError:
                pass
        return AgentResponse(output="Done.")


class PresentationControls:
    """Customer controls that apply and verify a condition on the fake to-do service.

    The parameters of an environment variation are the transformation, and the apply control
    hands them to the service. The verify control reads the logs of the service: a
    presentation occurred when the service served the agent a transformed response, and a
    fault occurred when it refused a request.
    """

    def __init__(self, service):
        self._service = service

    def prepare_state(self, context):
        for resource in context.authorized_resources:
            self._service.lists[resource] = ["buy milk"]

    def apply_condition(self, context):
        kind = getattr(context.condition.kind, "value", None)
        self._service.present(context.condition.parameters if kind == "environment" else None)
        self._service.failing = bool(context.condition.parameters.get("add_fails"))
        self._service.faulted_requests = 0

    def verify_condition(self, context):
        kind = getattr(context.condition.kind, "value", None)
        presented, faulted = self._service.presented_responses, self._service.faulted_requests
        return ObservedCondition(
            met=(presented > 0) == (kind == "environment") and (faulted > 0) == (kind == "fault"),
            source=SERVICE_LOG,
            observations={"presented_responses": presented, "faulted_requests": faulted},
        )

    def clean_up(self, context):
        self._service.present(None)
        self._service.failing = False
        for resource in context.authorized_resources:
            self._service.lists.pop(resource, None)


class Fixture:
    """A fake to-do service with a scripted agent, the controls and the experiments that use them."""

    def __init__(self, script=None, *, task_ids=("stock",)):
        self.service = PresentingTodoService(task_ids)
        self.agent = ListReadingAgent(self.service, script)
        self.controls = PresentationControls(self.service)

    def exactly_one_item(self, attempt, response):
        items = self.service.lists.get(todo_list(attempt.task_id), [])
        return TaskOutcome(success=len(items) == 1, source=OUTCOME_SOURCE)

    def experiment(self, task_id="stock", *, attempts, **declared):
        return Experiment(
            **{
                "experiment_id": f"{task_id}-list",
                "task": Task(task_id=task_id, prompt=PROMPT, success_rule=self.exactly_one_item, attempts=attempts),
                "agent_configuration": {"agent": "todo-agent", "version": "3"},
                "starting_state": "fresh",
                "baseline": BASELINE,
                "variation": WRAPPED_AND_RENAMED,
                "controls": self.controls,
                "write_capable": True,
                "authorized_resources": [todo_list(task_id)],
                **declared,
            }
        )

    def sweep(self, *experiments, metrics=("environment_robustness",), **declared):
        return run_sweep(experiments=experiments, agent_target=self.agent, metrics=metrics, **declared)


def metric(scorecard, metric_id="environment_robustness"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def attempt(scorecard, attempt_id):
    (entry,) = [entry for entry in scorecard["attempts"] if entry["attempt_id"] == attempt_id]
    return entry


def test_a_sweep_scores_environment_robustness_in_the_scorecard():
    # Baseline: the agent finds the item in all 4 Attempts and leaves the list alone: 4/4 = 1.
    # Presentation: it is served the wrapped list in all 4. It adapts in 3 and adds a second item in 1: 3/4 = 0.75.
    # 0.75 / 1 = 0.75.
    fixture = Fixture(
        {f"stock-list/wrapped-and-renamed/attempt-{number}": "adapts" for number in (1, 2, 4)}
    )

    scorecard = fixture.sweep(fixture.experiment(attempts=4))

    environment_robustness = metric(scorecard)
    assert environment_robustness["state"] == "available"
    assert environment_robustness["value"] == 0.75
    assert environment_robustness["scoring_path"] == "customer_process"
    assert environment_robustness["fidelity"] == "authoritative"
    assert environment_robustness["definition"]["version"] == "1.0.0"
    nothing_left_out = {"missing": [], "excluded": []}
    assert environment_robustness["counts"] == {
        "tasks": {"total": 1, "valid": 1, **nothing_left_out},
        "baseline_attempts": {"total": 4, "valid": 4, **nothing_left_out},
        "environment_attempts": {"total": 4, "valid": 4, **nothing_left_out},
        "other_attempts": {"total": 0, "valid": 0, **nothing_left_out},
    }
    assert environment_robustness["details"] == {
        "variation_kind": "environment",
        "baseline": {
            "condition_ids": ["baseline"],
            "observed_from": [SERVICE_LOG],
            "tasks_with_a_valid_attempt": 1,
            "compared_tasks": 1,
            "compared_attempts": 4,
            "successes": 4,
            "accuracy": 1.0,
        },
        "environment": {
            "condition_ids": ["wrapped-and-renamed"],
            "observed_from": [SERVICE_LOG],
            "tasks_with_a_valid_attempt": 1,
            "compared_tasks": 1,
            "compared_attempts": 4,
            "successes": 3,
            "accuracy": 0.75,
        },
        "accuracy_ratio": 0.75,
        "capped": False,
        "per_task": [
            {
                "task_id": "stock",
                "baseline": {"attempts": 4, "successes": 4, "success_frequency": 1.0},
                "environment": {"attempts": 4, "successes": 3, "success_frequency": 0.75},
            }
        ],
    }
    assert environment_robustness["evidence"] == {
        "attempt_ids": [
            *(f"stock-list/baseline/attempt-{number}" for number in range(1, 5)),
            *(f"stock-list/wrapped-and-renamed/attempt-{number}" for number in range(1, 5)),
        ]
    }
    assert json.loads(to_json(scorecard)) == scorecard

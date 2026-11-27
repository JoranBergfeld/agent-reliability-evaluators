"""Evaluator sweep seam: declared experiments, customer controls and the starting-state policy.

The sweep is driven through its public entry point with a fake agent target and fake
customer controls. Every assertion is on the machine-readable scorecard. Expected values
are calculated by hand.

The fake agent adds one item to its Task's to-do list, and the Task succeeds when the
list then holds exactly one item. Neither the agent nor the service ever resets a list.
"""

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import Condition, Experiment, Task, run_sweep
from tests.fakes import FakeControls, FakeTodoAgent, FakeTodoService, todo_list

OUTCOME_SOURCE = "final state of the fake to-do service"
BASELINE = Condition(condition_id="baseline", description="The to-do service accepts every request.")
ADD_FAILS = Condition(
    condition_id="add-fails",
    description="The to-do service refuses to add an item.",
    parameters={"add_fails": True},
)


class Fixture:
    """A fake to-do service with the agent, the controls and the experiments that use it."""

    def __init__(
        self, task_ids=("add",), *, controls=FakeControls, failures=None, misapplied_for=(), unreachable_for=()
    ):
        self.service = FakeTodoService(task_ids)
        self.agent = FakeTodoAgent(self.service, unreachable_for=unreachable_for)
        self.controls = controls(self.service, failures=failures, misapplied_for=misapplied_for)

    def exactly_one_item(self, attempt, response):
        items = self.service.lists.get(todo_list(attempt.task_id), [])
        return TaskOutcome(success=len(items) == 1, source=OUTCOME_SOURCE)

    def experiment(self, task_id="add", *, attempts=3, **declared):
        return Experiment(
            **{
                "experiment_id": f"{task_id}-item",
                "task": Task(
                    task_id=task_id,
                    prompt="Add one item to the list.",
                    success_rule=self.exactly_one_item,
                    attempts=attempts,
                ),
                "agent_configuration": {"agent": "todo-agent", "version": "3"},
                "starting_state": "fresh",
                "baseline": BASELINE,
                "variation": None,
                "controls": self.controls,
                "write_capable": True,
                "authorized_resources": [todo_list(task_id)],
                **declared,
            }
        )

    def sweep(self, *experiments):
        return run_sweep(experiments=experiments, agent_target=self.agent, metrics=["outcome_consistency"])


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def outcomes(scorecard):
    return [attempt["task_outcome"] and attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]]


def test_a_fresh_state_experiment_prepares_the_state_before_every_attempt():
    # Nothing but the prepare control empties the list, so each Attempt succeeds only if the
    # state was prepared for it: p = 3/3 and the value is 1. Left to the agent target and the
    # service, the list would grow and no Attempt would succeed.
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(starting_state="fresh"))

    assert outcomes(scorecard) == [True, True, True]
    assert [attempt["starting_state"] for attempt in scorecard["attempts"]] == ["fresh"] * 3
    assert metric(scorecard)["value"] == 1


def test_a_continuing_state_experiment_carries_the_state_from_one_attempt_to_the_next():
    # The state is prepared once, so the list holds 1, 2 and 3 items after the Attempts and
    # only the first succeeds: p = 1/3 and the value is (2/3 - 1)^2 = 1/9.
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(starting_state="continuing"))

    assert outcomes(scorecard) == [True, False, False]
    assert [attempt["starting_state"] for attempt in scorecard["attempts"]] == ["continuing"] * 3
    assert metric(scorecard)["value"] == pytest.approx(1 / 9, abs=1e-12)


def control_points(scorecard):
    return [[step["point"] for step in attempt["lifecycle"]] for attempt in scorecard["attempts"]]


def test_fresh_state_invokes_every_control_around_every_attempt():
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(starting_state="fresh", attempts=2))

    assert scorecard["attempts"][0]["lifecycle"] == [
        {"point": "prepare_state", "succeeded": True, "error": None},
        {"point": "apply_condition", "succeeded": True, "error": None},
        {"point": "verify_condition", "succeeded": True, "error": None},
        {"point": "clean_up", "succeeded": True, "error": None},
    ]
    assert control_points(scorecard) == [["prepare_state", "apply_condition", "verify_condition", "clean_up"]] * 2


def test_continuing_state_prepares_once_and_cleans_up_once():
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(starting_state="continuing", attempts=3))

    assert control_points(scorecard) == [
        ["prepare_state", "apply_condition", "verify_condition"],
        ["apply_condition", "verify_condition"],
        ["apply_condition", "verify_condition", "clean_up"],
    ]


def test_each_attempt_records_the_intended_and_the_observed_condition():
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=1, variation=ADD_FAILS))

    under_baseline, under_variation = scorecard["attempts"]
    assert under_baseline["attempt_id"] == "add-item/baseline/attempt-1"
    assert under_baseline["experiment_id"] == "add-item"
    assert under_baseline["intended_condition"] == {
        "role": "baseline",
        "condition_id": "baseline",
        "description": "The to-do service accepts every request.",
        "parameters": {},
    }
    assert under_baseline["observed_condition"] == {
        "met": True,
        "source": "request log of the fake to-do service",
        "observations": {"faulted_requests": 0},
    }
    assert under_variation["attempt_id"] == "add-item/add-fails/attempt-1"
    assert under_variation["experiment_id"] == "add-item"
    assert under_variation["intended_condition"] == {
        "role": "variation",
        "condition_id": "add-fails",
        "description": "The to-do service refuses to add an item.",
        "parameters": {"add_fails": True},
    }
    assert under_variation["observed_condition"] == {
        "met": True,
        "source": "request log of the fake to-do service",
        "observations": {"faulted_requests": 1},
    }
    # The fault occurred and the agent still answered "Done.": a completed Attempt that failed its Task.
    assert under_variation["execution_status"] == "completed"
    assert under_variation["task_outcome"] == {"success": False, "source": OUTCOME_SOURCE}


def test_attempts_under_the_variation_are_not_mixed_into_a_metric_of_repeated_baseline_attempts():
    # Baseline: 2 successes. Variation: the fault occurs twice and the Task fails twice.
    # Outcome consistency rests on the baseline only: p = 2/2 and the value is 1.
    # Mixing the conditions would give p = 2/4 and the value 0.
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=2, variation=ADD_FAILS))

    assert outcomes(scorecard) == [True, True, False, False]
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["counts"]["attempts"]["total"] == 2
    assert outcome_consistency["evidence"] == {
        "attempt_ids": ["add-item/baseline/attempt-1", "add-item/baseline/attempt-2"]
    }


def not_scored(scorecard, *attempt_ids):
    """The Attempts outcome consistency left out because their execution did not complete."""
    return metric(scorecard)["counts"]["attempts"]["excluded"] == [
        {"reason": "execution status is not completed", "count": len(attempt_ids), "ids": list(attempt_ids)}
    ]


def test_an_unmet_condition_is_an_execution_status_and_not_a_task_failure():
    # In the third baseline Attempt the service refused the item although no fault was intended.
    # The Task failed there, but not under the declared condition, so the Attempt is not scored:
    # p = 2/2 and the value is 1. Scored as a task failure it would give (2 * 2/3 - 1)^2 = 1/9.
    fixture = Fixture(misapplied_for={"add-item/baseline/attempt-3"})

    scorecard = fixture.sweep(fixture.experiment(attempts=3))

    unmet = scorecard["attempts"][2]
    assert unmet["execution_status"] == "condition_not_met"
    assert unmet["execution_error"] == "the verify control observed that the intended condition did not occur"
    assert unmet["observed_condition"] == {
        "met": False,
        "source": "request log of the fake to-do service",
        "observations": {"faulted_requests": 1},
    }
    assert unmet["task_outcome"] == {"success": False, "source": OUTCOME_SOURCE}
    assert metric(scorecard)["value"] == 1
    assert not_scored(scorecard, "add-item/baseline/attempt-3")


def test_a_condition_that_could_not_be_verified_is_not_taken_as_met():
    fixture = Fixture(failures={("verify_condition", "add-item/baseline/attempt-2"): "the request log is unavailable"})

    scorecard = fixture.sweep(fixture.experiment(attempts=3))

    unverified = scorecard["attempts"][1]
    assert unverified["execution_status"] == "condition_not_met"
    assert unverified["execution_error"] == (
        "the intended condition was not verified: RuntimeError: the request log is unavailable"
    )
    assert unverified["observed_condition"] is None
    assert unverified["lifecycle"][2] == {
        "point": "verify_condition",
        "succeeded": False,
        "error": "RuntimeError: the request log is unavailable",
    }
    assert unverified["task_outcome"] == {"success": True, "source": OUTCOME_SOURCE}
    assert not_scored(scorecard, "add-item/baseline/attempt-2")


class LabelOnlyControls(FakeControls):
    """Controls whose verify control repeats the intended condition instead of observing one."""

    def verify_condition(self, context):
        return context.condition.condition_id


def test_a_label_from_the_verify_control_is_not_an_observed_condition():
    fixture = Fixture(controls=LabelOnlyControls)

    scorecard = fixture.sweep(fixture.experiment(attempts=1, variation=ADD_FAILS))

    under_variation = scorecard["attempts"][1]
    assert under_variation["execution_status"] == "condition_not_met"
    assert under_variation["execution_error"] == (
        "the intended condition was not verified: "
        "the verify control returned 'add-fails', not an ObservedCondition with its source"
    )
    assert under_variation["observed_condition"] is None

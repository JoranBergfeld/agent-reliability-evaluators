"""Evaluator sweep seam: declared experiments, customer controls and the starting-state policy.

The sweep is driven through its public entry point with a fake agent target and fake
customer controls. Every assertion is on the machine-readable scorecard. Expected values
are calculated by hand.

The fake agent adds one item to its Task's to-do list, and the Task succeeds when the
list then holds exactly one item. Neither the agent nor the service ever resets a list.
"""

import json

import pytest

from reliability_evaluators.evidence import OperationalConstraint, TaskOutcome, ViolationAssessment
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import Condition, Experiment, Task, run_sweep
from tests.fakes import FakeControls, FakeSelfAssessingTodoAgent, FakeTodoAgent, FakeTodoService, todo_list

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
        self,
        task_ids=("add",),
        *,
        agent=FakeTodoAgent,
        controls=FakeControls,
        failures=None,
        misapplied_for=(),
        unreachable_for=(),
    ):
        self.service = FakeTodoService(task_ids)
        self.agent = agent(self.service, unreachable_for=unreachable_for)
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


def test_continuing_state_does_not_continue_from_the_baseline_into_the_variation():
    # The variation starts from prepared state, like the baseline did, so the two differ
    # only by the declared variation.
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(starting_state="continuing", attempts=2, variation=ADD_FAILS))

    assert control_points(scorecard) == [
        ["prepare_state", "apply_condition", "verify_condition"],
        ["apply_condition", "verify_condition", "clean_up"],
        ["prepare_state", "apply_condition", "verify_condition"],
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


def test_a_failed_setup_is_an_execution_status_and_not_a_task_failure():
    # The state could not be prepared for the second Attempt, so the agent was never asked.
    # Attempts 1 and 3 succeeded: p = 2/2 and the value is 1. Scoring the failed setup as a
    # task failure would give (2 * 2/3 - 1)^2 = 1/9.
    fixture = Fixture(failures={("prepare_state", "add-item/baseline/attempt-2"): "the to-do service is down"})

    scorecard = fixture.sweep(fixture.experiment(attempts=3))

    setup_failed = scorecard["attempts"][1]
    assert setup_failed["execution_status"] == "setup_failed"
    assert setup_failed["execution_error"] == "prepare_state failed: RuntimeError: the to-do service is down"
    assert setup_failed["task_outcome"] is None
    assert setup_failed["observed_condition"] is None
    assert setup_failed["lifecycle"] == [
        {"point": "prepare_state", "succeeded": False, "error": "RuntimeError: the to-do service is down"},
        {"point": "clean_up", "succeeded": True, "error": None},
    ]
    assert outcomes(scorecard) == [True, None, True]
    assert metric(scorecard)["value"] == 1
    assert not_scored(scorecard, "add-item/baseline/attempt-2")


def test_a_condition_that_could_not_be_applied_is_a_failed_setup():
    fixture = Fixture(failures={("apply_condition", "add-item/add-fails/attempt-1"): "the fault switch is stuck"})

    scorecard = fixture.sweep(fixture.experiment(attempts=1, variation=ADD_FAILS))

    under_variation = scorecard["attempts"][1]
    assert under_variation["execution_status"] == "setup_failed"
    assert under_variation["execution_error"] == "apply_condition failed: RuntimeError: the fault switch is stuck"
    assert under_variation["task_outcome"] is None
    assert control_points(scorecard)[1] == ["prepare_state", "apply_condition", "clean_up"]


def test_an_invocation_that_fails_in_an_experiment_is_still_verified_and_cleaned_up():
    fixture = Fixture(unreachable_for={"add-item/baseline/attempt-1"})

    scorecard = fixture.sweep(fixture.experiment(attempts=2))

    failed = scorecard["attempts"][0]
    assert failed["execution_status"] == "failed"
    assert failed["execution_error"] == "ConnectionError: the agent endpoint returned 503"
    assert failed["task_outcome"] is None
    assert failed["observed_condition"] == {
        "met": True,
        "source": "request log of the fake to-do service",
        "observations": {"faulted_requests": 0},
    }
    assert control_points(scorecard)[0] == ["prepare_state", "apply_condition", "verify_condition", "clean_up"]
    assert outcomes(scorecard) == [None, True]


def test_the_scorecard_records_what_each_experiment_declared_and_how_it_ran():
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=2, variation=ADD_FAILS))

    assert scorecard["experiments"] == [
        {
            "experiment_id": "add-item",
            "task_id": "add",
            "agent_configuration": {"agent": "todo-agent", "version": "3"},
            "starting_state": "fresh",
            "baseline": {
                "condition_id": "baseline",
                "description": "The to-do service accepts every request.",
                "parameters": {},
            },
            "variation": {
                "condition_id": "add-fails",
                "description": "The to-do service refuses to add an item.",
                "parameters": {"add_fails": True},
            },
            "write_capable": True,
            "authorized_resources": ["todo-list/add"],
            "attempt_set": 1,
            "limits": {"attempt_timeout_seconds": None, "total_timeout_seconds": None, "max_retries": 0},
            "execution": {
                "status": "completed",
                "reason": None,
                "attempts_planned": 4,
                "attempts_recorded": 4,
                "attempts_completed": 4,
                "attempts_not_completed": [],
                "attempts_not_made": 0,
                "retries": 0,
            },
        }
    ]
    assert json.loads(to_json(scorecard)) == scorecard


def execution(scorecard, experiment_id):
    """How the execution of an experiment ended, and how many of its Attempts were planned and recorded."""
    (entry,) = [entry for entry in scorecard["experiments"] if entry["experiment_id"] == experiment_id]
    return {key: entry["execution"][key] for key in ("status", "reason", "attempts_planned", "attempts_recorded")}


def test_a_failed_clean_up_stops_the_later_attempts_of_its_experiment():
    # The first Attempt succeeded and then its state could not be cleaned up. Attempts 2 and 3
    # would start from the state that clean-up restores, so they are not made.
    fixture = Fixture(failures={("clean_up", "add-item/baseline/attempt-1"): "the list could not be deleted"})

    scorecard = fixture.sweep(fixture.experiment(attempts=3))

    (only,) = scorecard["attempts"]
    assert only["execution_status"] == "cleanup_failed"
    assert only["execution_error"] == "clean_up failed: RuntimeError: the list could not be deleted"
    assert only["task_outcome"] == {"success": True, "source": OUTCOME_SOURCE}
    assert only["lifecycle"][-1] == {
        "point": "clean_up",
        "succeeded": False,
        "error": "RuntimeError: the list could not be deleted",
    }
    assert execution(scorecard, "add-item") == {
        "status": "stopped",
        "reason": (
            "clean_up failed after Attempt 'add-item/baseline/attempt-1': "
            "RuntimeError: the list could not be deleted"
        ),
        "attempts_planned": 3,
        "attempts_recorded": 1,
    }
    assert not_scored(scorecard, "add-item/baseline/attempt-1")


def test_a_failed_clean_up_stops_only_the_experiments_that_depend_on_the_unrestored_state():
    # "add" and "tidy" may both change the shared list. "archive" changes only its own list.
    fixture = Fixture(
        task_ids=("add", "tidy", "archive"),
        failures={("clean_up", "add-item/baseline/attempt-1"): "the list could not be deleted"},
    )

    scorecard = fixture.sweep(
        fixture.experiment("add", attempts=2, authorized_resources=["todo-list/add", "todo-list/shared"]),
        fixture.experiment("tidy", attempts=2, authorized_resources=["todo-list/tidy", "todo-list/shared"]),
        fixture.experiment("archive", attempts=2),
    )

    assert [attempt["attempt_id"] for attempt in scorecard["attempts"]] == [
        "add-item/baseline/attempt-1",
        "archive-item/baseline/attempt-1",
        "archive-item/baseline/attempt-2",
    ]
    assert execution(scorecard, "tidy-item") == {
        "status": "stopped",
        "reason": (
            "experiment 'add-item' did not restore the test resource 'todo-list/shared': "
            "clean_up failed after Attempt 'add-item/baseline/attempt-1': "
            "RuntimeError: the list could not be deleted"
        ),
        "attempts_planned": 2,
        "attempts_recorded": 0,
    }
    assert execution(scorecard, "archive-item") == {
        "status": "completed",
        "reason": None,
        "attempts_planned": 2,
        "attempts_recorded": 2,
    }
    # The unrelated experiment is still scored: p = 2/2 and the value is 1.
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["details"]["per_task"] == [
        {"task_id": "archive", "attempts": 2, "successes": 2, "value": 1.0}
    ]


def test_a_continuing_state_experiment_stops_when_its_state_could_not_be_prepared():
    # Every Attempt of the experiment would continue from the state that was never prepared.
    fixture = Fixture(failures={("prepare_state", "add-item/baseline/attempt-1"): "the to-do service is down"})

    scorecard = fixture.sweep(fixture.experiment(starting_state="continuing", attempts=3, variation=ADD_FAILS))

    (only,) = scorecard["attempts"]
    assert only["execution_status"] == "setup_failed"
    assert only["execution_error"] == "prepare_state failed: RuntimeError: the to-do service is down"
    assert control_points(scorecard) == [["prepare_state", "clean_up"]]
    assert execution(scorecard, "add-item") == {
        "status": "stopped",
        "reason": (
            "prepare_state failed before Attempt 'add-item/baseline/attempt-1': "
            "RuntimeError: the to-do service is down"
        ),
        "attempts_planned": 6,
        "attempts_recorded": 1,
    }


@pytest.mark.parametrize(
    "undeclared",
    ["task", "agent_configuration", "starting_state", "baseline", "variation", "controls", "write_capable"],
)
def test_an_experiment_has_no_default_for_what_it_must_declare(undeclared):
    declared = dict(vars(Fixture().experiment()))
    del declared[undeclared]

    with pytest.raises(TypeError, match=undeclared):
        Experiment(**declared)


class NoCleanUp:
    prepare_state = apply_condition = verify_condition = staticmethod(lambda context: None)


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        ({"experiment_id": " "}, "experiment_id"),
        ({"task": "add"}, "a Task"),
        ({"starting_state": None}, "starting-state policy is 'fresh' or 'continuing'"),
        ({"starting_state": "reset"}, "starting-state policy is 'fresh' or 'continuing'"),
        ({"agent_configuration": {}}, "agent configuration"),
        ({"agent_configuration": {"agent": object()}}, "JSON-compatible"),
        ({"baseline": None}, "baseline"),
        ({"variation": "add-fails"}, "variation"),
        ({"variation": Condition(condition_id="baseline", description="Again.")}, "differs from the baseline"),
        ({"controls": NoCleanUp()}, "clean_up"),
        ({"write_capable": "yes"}, "write_capable"),
        ({"write_capable": True, "authorized_resources": []}, "identifies the test resources"),
        ({"write_capable": True, "authorized_resources": [""]}, "identifies the test resources"),
        ({"write_capable": True, "authorized_resources": "todo-list/add"}, "as a list of names"),
        ({"write_capable": False}, "not write-capable"),
    ],
)
def test_an_experiment_that_is_not_fully_declared_is_rejected(declared, problem):
    with pytest.raises(ValueError, match=problem):
        Fixture().experiment(**declared)


def test_a_sweep_whose_experiments_cannot_be_told_apart_is_rejected():
    fixture = Fixture()
    add = fixture.experiment("add")
    same_task = fixture.experiment("add", experiment_id="add-again")
    same_identity = fixture.experiment("tidy", experiment_id="add-item")
    task_given_twice = add.task
    colliding_task = Task(
        task_id="add-item/baseline", prompt="Add.", success_rule=fixture.exactly_one_item, attempts=1
    )

    def sweep(tasks, experiments):
        return run_sweep(
            tasks=tasks, experiments=experiments, agent_target=fixture.agent, metrics=["outcome_consistency"]
        )

    with pytest.raises(ValueError, match="more than one experiment is declared as 'add-item'"):
        sweep([], [add, same_identity])
    with pytest.raises(ValueError, match="more than one Task is declared as 'add'"):
        sweep([], [add, same_task])
    with pytest.raises(ValueError, match="more than one Task is declared as 'add'"):
        sweep([task_given_twice], [add])
    with pytest.raises(ValueError, match="more than one Attempt would have the identity 'add-item/baseline/"):
        sweep([colliding_task], [add])


def test_a_clean_up_that_fails_after_the_last_attempt_still_stops_the_experiments_that_depend_on_it():
    # Under continuing state the clean-up control runs once, after the last Attempt. Every
    # Attempt of "add" was made, so "add" completed, but it left the shared list behind.
    fixture = Fixture(
        task_ids=("add", "tidy"),
        failures={("clean_up", "add-item/baseline/attempt-2"): "the list could not be deleted"},
    )

    scorecard = fixture.sweep(
        fixture.experiment(
            "add", starting_state="continuing", attempts=2, authorized_resources=["todo-list/add", "todo-list/shared"]
        ),
        fixture.experiment("tidy", attempts=2, authorized_resources=["todo-list/tidy", "todo-list/shared"]),
    )

    assert [attempt["execution_status"] for attempt in scorecard["attempts"]] == ["completed", "cleanup_failed"]
    assert execution(scorecard, "add-item") == {
        "status": "completed",
        "reason": None,
        "attempts_planned": 2,
        "attempts_recorded": 2,
    }
    assert execution(scorecard, "tidy-item")["status"] == "stopped"
    assert execution(scorecard, "tidy-item")["attempts_recorded"] == 0


def test_an_attempt_whose_setup_failed_still_records_the_operational_constraints_of_its_task():
    # The constraint applied to all three Attempts. Two were assessed and neither violated it:
    # 2 / 2 = 1. The Attempt whose setup failed is left out, neither compliant nor a violation.
    fixture = Fixture(failures={("prepare_state", "add-item/baseline/attempt-2"): "the to-do service is down"})

    def one_item_at_most(attempt, response):
        items = len(fixture.service.lists[todo_list(attempt.task_id)])
        return [
            ViolationAssessment(
                constraint_id="one-item",
                violated=items > 1,
                evidence=f"the list holds {items} item(s) after the Attempt",
                source="deterministic check on the fake to-do service",
            )
        ]

    task = Task(
        task_id="add",
        prompt="Add one item to the list.",
        success_rule=fixture.exactly_one_item,
        attempts=3,
        operational_constraints=[OperationalConstraint(constraint_id="one-item", description="Add one item at most.")],
        violation_assessor=one_item_at_most,
    )

    scorecard = run_sweep(
        experiments=[fixture.experiment(task=task)], agent_target=fixture.agent, metrics=["constraint_compliance"]
    )

    setup_failed = scorecard["attempts"][1]
    assert setup_failed["execution_status"] == "setup_failed"
    assert setup_failed["operational_constraints"] == [
        {"constraint_id": "one-item", "description": "Add one item at most."}
    ]
    assert setup_failed["violation_assessments"] == []
    constraint_compliance = metric(scorecard, "constraint_compliance")
    assert constraint_compliance["value"] == 1
    assert constraint_compliance["counts"]["attempts"]["valid"] == 2
    assert constraint_compliance["details"]["constraint_set"][0]["applicable_attempts"] == 3
    assert constraint_compliance["details"]["constraint_set"][0]["assessed_attempts"] == 2


def self_assessed_sweep(fixture, *, attempts, metrics=("brier_based_score",)):
    """Run an experiment whose agent can be asked for its confidence rating, on that agent's clock."""
    task = Task(
        task_id="add",
        prompt="Add one item to the list.",
        success_rule=fixture.exactly_one_item,
        shareable_success_rule="The list holds exactly one item.",
        attempts=attempts,
    )
    return run_sweep(
        experiments=[fixture.experiment(task=task)],
        agent_target=fixture.agent,
        metrics=metrics,
        clock=fixture.agent.clock,
    )


NOT_ASKED = {
    "rating": None,
    "normalized": None,
    "original_response": None,
    "unavailable_reason": "the Attempt did not complete, so no confidence request was sent",
    "invocation": None,
}


def test_the_agent_is_asked_for_its_confidence_before_the_state_of_its_attempt_is_cleaned_up():
    # The clean-up control deletes the list. The agent answers 100 only while it still sees its
    # one item, so both pairs are (1, success): the squared errors are 0 and the score is 1.
    # Asked after clean-up it would answer 0 twice, and the score would be 0.
    fixture = Fixture(agent=FakeSelfAssessingTodoAgent)

    scorecard = self_assessed_sweep(fixture, attempts=2)

    assert [attempt["confidence"]["rating"] for attempt in scorecard["attempts"]] == ["100", "100"]
    assert control_points(scorecard) == [["prepare_state", "apply_condition", "verify_condition", "clean_up"]] * 2
    assert metric(scorecard, "brier_based_score")["value"] == 1


@pytest.mark.parametrize(
    ("went_wrong", "execution_status"),
    [
        ({"failures": {("prepare_state", "add-item/baseline/attempt-2"): "the to-do service is down"}}, "setup_failed"),
        ({"misapplied_for": {"add-item/baseline/attempt-2"}}, "condition_not_met"),
        ({"unreachable_for": {"add-item/baseline/attempt-2"}}, "failed"),
    ],
)
def test_the_agent_is_not_asked_for_its_confidence_in_an_attempt_that_did_not_complete(went_wrong, execution_status):
    # Attempts 1 and 3 give the pairs (1, success): the score is 1. Had the agent been asked
    # about the second Attempt, its answer would be recorded there.
    fixture = Fixture(agent=FakeSelfAssessingTodoAgent, **went_wrong)

    scorecard = self_assessed_sweep(fixture, attempts=3)

    not_completed = scorecard["attempts"][1]
    assert not_completed["execution_status"] == execution_status
    assert not_completed["confidence"] == NOT_ASKED
    brier = metric(scorecard, "brier_based_score")
    assert brier["value"] == 1
    assert brier["counts"]["confidence_outcome_pairs"] == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [
            {"reason": "execution status is not completed", "count": 1, "ids": ["add-item/baseline/attempt-2"]}
        ],
    }


def test_a_clean_up_that_fails_keeps_the_rating_and_leaves_the_attempt_out_of_the_score():
    # The agent was asked before the clean-up control failed, so its answer is kept as evidence.
    # The Attempt did not complete, so it gives no pair, and there is no other Attempt.
    fixture = Fixture(
        agent=FakeSelfAssessingTodoAgent,
        failures={("clean_up", "add-item/baseline/attempt-1"): "the list could not be deleted"},
    )

    scorecard = self_assessed_sweep(fixture, attempts=2)

    (only,) = scorecard["attempts"]
    assert only["execution_status"] == "cleanup_failed"
    assert only["confidence"]["rating"] == "100"
    # The experiment stopped with no Attempt completed. That is an execution failure, not an
    # unavailable measurement.
    brier = metric(scorecard, "brier_based_score")
    assert brier["state"] is None
    assert brier["value"] is None
    assert brier["execution_failure"]["experiment_ids"] == ["add-item"]
    assert brier["counts"]["confidence_outcome_pairs"]["valid"] == 0


def test_an_attempt_of_an_experiment_is_timed_on_the_sweep_clock_without_its_confidence_follow_up():
    # An invocation takes 2 seconds and the follow-up 5. Elapsed time is 2 seconds in both
    # Attempts: its CV is 0 and the value is exp(-0) = 1.
    fixture = Fixture(agent=FakeSelfAssessingTodoAgent)

    scorecard = self_assessed_sweep(fixture, attempts=2, metrics=["resource_consistency", "brier_based_score"])

    for attempt in scorecard["attempts"]:
        assert attempt["resource_measurements"] == [
            {
                "resource_type": "elapsed_time",
                "value": 2.0,
                "unit": "seconds",
                "boundary": (
                    "wall clock from just before the agent target is invoked until it returns, "
                    "measured by the Evaluator sweep"
                ),
                "reliably_sourced": True,
            }
        ]
    assert metric(scorecard, "resource_consistency")["value"] == 1

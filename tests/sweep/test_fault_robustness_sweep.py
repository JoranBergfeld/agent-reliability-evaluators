"""Evaluator sweep seam: fault robustness, from declared experiments to the reliability scorecard.

The sweep is driven through its public entry point with a fake agent target and fake
customer controls. Every assertion is on the machine-readable scorecard. Expected values
are calculated by hand.

The fake agent adds one item to its Task's to-do list, and the Task succeeds when the
list then holds exactly one item. Under the fault the to-do service refuses to add an
item. The agent answers "Done." whatever happened.
"""

import json

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.scorecard import AcceptanceCriterion, to_json
from reliability_evaluators.sweep import Condition, Experiment, Task, run_sweep
from tests.fakes import FakeControls, FakeScriptedTodoAgent, FakeTodoService, todo_list

OUTCOME_SOURCE = "final state of the fake to-do service"
REQUEST_LOG = "request log of the fake to-do service"
BASELINE = Condition(condition_id="baseline", description="The to-do service accepts every request.")
ADD_FAILS = Condition(
    condition_id="add-fails",
    kind="fault",
    description="The to-do service refuses to add an item.",
    parameters={"add_fails": True},
)


class Fixture:
    """A fake to-do service with a scripted agent, the controls and the experiments that use them.

    `script` says what the agent does in an Attempt, by Attempt identity. Unscripted, it
    adds one item and gives up when the service refuses it.
    """

    def __init__(self, script=None, *, task_ids=("add",), controls=FakeControls, failures=None):
        self.service = FakeTodoService(task_ids)
        self.agent = FakeScriptedTodoAgent(self.service, script)
        self.controls = controls(self.service, failures=failures)

    def exactly_one_item(self, attempt, response):
        items = self.service.lists.get(todo_list(attempt.task_id), [])
        return TaskOutcome(success=len(items) == 1, source=OUTCOME_SOURCE)

    def task(self, task_id="add", *, attempts):
        return Task(
            task_id=task_id, prompt="Add one item to the list.", success_rule=self.exactly_one_item, attempts=attempts
        )

    def experiment(self, task_id="add", *, attempts, **declared):
        return Experiment(
            **{
                "experiment_id": f"{task_id}-item",
                "task": self.task(task_id, attempts=attempts),
                "agent_configuration": {"agent": "todo-agent", "version": "3"},
                "starting_state": "fresh",
                "baseline": BASELINE,
                "variation": ADD_FAILS,
                "controls": self.controls,
                "write_capable": True,
                "authorized_resources": [todo_list(task_id)],
                **declared,
            }
        )

    def sweep(self, *experiments, metrics=("fault_robustness",), **declared):
        return run_sweep(experiments=experiments, agent_target=self.agent, metrics=metrics, **declared)


def metric(scorecard, metric_id="fault_robustness"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def attempt(scorecard, attempt_id):
    (entry,) = [entry for entry in scorecard["attempts"] if entry["attempt_id"] == attempt_id]
    return entry


def test_a_sweep_scores_fault_robustness_in_the_scorecard():
    # Baseline: the agent adds its item in all 4 Attempts: 4/4 = 1.
    # Fault: the service refuses the item in all 4. The agent works around it in 2: 2/4 = 0.5.
    # 0.5 / 1 = 0.5.
    fixture = Fixture({"add-item/add-fails/attempt-1": "works around", "add-item/add-fails/attempt-3": "works around"})

    scorecard = fixture.sweep(fixture.experiment(attempts=4))

    fault_robustness = metric(scorecard)
    assert fault_robustness["state"] == "available"
    assert fault_robustness["value"] == 0.5
    assert fault_robustness["scoring_path"] == "customer_process"
    assert fault_robustness["fidelity"] == "authoritative"
    assert fault_robustness["definition"]["version"] == "1.0.0"
    nothing_left_out = {"missing": [], "excluded": []}
    assert fault_robustness["counts"] == {
        "tasks": {"total": 1, "valid": 1, **nothing_left_out},
        "baseline_attempts": {"total": 4, "valid": 4, **nothing_left_out},
        "fault_attempts": {"total": 4, "valid": 4, **nothing_left_out},
        "other_attempts": {"total": 0, "valid": 0, **nothing_left_out},
    }
    assert fault_robustness["details"] == {
        "variation_kind": "fault",
        "baseline": {
            "condition_ids": ["baseline"],
            "observed_from": [REQUEST_LOG],
            "tasks_with_a_valid_attempt": 1,
            "compared_tasks": 1,
            "compared_attempts": 4,
            "successes": 4,
            "accuracy": 1.0,
        },
        "fault": {
            "condition_ids": ["add-fails"],
            "observed_from": [REQUEST_LOG],
            "tasks_with_a_valid_attempt": 1,
            "compared_tasks": 1,
            "compared_attempts": 4,
            "successes": 2,
            "accuracy": 0.5,
        },
        "accuracy_ratio": 0.5,
        "capped": False,
        "per_task": [
            {
                "task_id": "add",
                "baseline": {"attempts": 4, "successes": 4, "success_frequency": 1.0},
                "fault": {"attempts": 4, "successes": 2, "success_frequency": 0.5},
            }
        ],
    }
    assert fault_robustness["evidence"] == {
        "attempt_ids": [
            *(f"add-item/baseline/attempt-{number}" for number in range(1, 5)),
            *(f"add-item/add-fails/attempt-{number}" for number in range(1, 5)),
        ]
    }
    assert json.loads(to_json(scorecard)) == scorecard


def test_task_outcomes_come_from_the_service_and_not_from_the_answer_of_the_agent():
    # The agent answers "Done." in every Attempt. Under the fault the service refused the item both
    # times, so the list stayed empty and the Task failed: baseline 2/2 = 1, fault 0/2 = 0, value 0.
    # Believing the answer would give 2/2 under the fault and the value 1.
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=2))

    for number in (1, 2):
        under_fault = attempt(scorecard, f"add-item/add-fails/attempt-{number}")
        assert under_fault["execution_status"] == "completed"
        assert under_fault["task_outcome"] == {"success": False, "source": OUTCOME_SOURCE}
    assert metric(scorecard)["state"] == "available"
    assert metric(scorecard)["value"] == 0


CONDITION_DIFFERS = "the observed condition differs from the intended condition"
NOT_VERIFIED = "no observed condition shows that the intended condition occurred"


def test_a_fault_attempt_counts_only_when_the_verify_control_shows_that_the_fault_occurred():
    # Baseline 3/3 = 1. The fault was applied for all three Attempts under the variation:
    #   1: the agent imported its item and never asked the service to add one. The Task succeeded,
    #      and the request log shows no refused request: the fault did not occur;
    #   2: the service refused the item and the agent gave up: failure;
    #   3: the service refused the item and the agent worked around it: success.
    # Fault 1/2 = 0.5, value 0.5. Counting the first Attempt as having survived the fault gives 2/3.
    fixture = Fixture({"add-item/add-fails/attempt-1": "imports", "add-item/add-fails/attempt-3": "works around"})

    scorecard = fixture.sweep(fixture.experiment(attempts=3))

    unexposed = attempt(scorecard, "add-item/add-fails/attempt-1")
    assert unexposed["intended_condition"]["kind"] == "fault"
    assert unexposed["execution_status"] == "condition_not_met"
    assert unexposed["observed_condition"] == {
        "met": False,
        "source": REQUEST_LOG,
        "observations": {"faulted_requests": 0},
    }
    assert unexposed["task_outcome"] == {"success": True, "source": OUTCOME_SOURCE}
    fault_robustness = metric(scorecard)
    assert fault_robustness["value"] == 0.5
    assert fault_robustness["counts"]["fault_attempts"] == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [{"reason": CONDITION_DIFFERS, "count": 1, "ids": ["add-item/add-fails/attempt-1"]}],
    }
    assert "add-item/add-fails/attempt-1" not in fault_robustness["evidence"]["attempt_ids"]
    # The Evaluator was given the Attempt, and left it out.
    assert "add-item/add-fails/attempt-1" in fault_robustness["attempts_used"]["attempt_ids"]


class LabelsTheFault(FakeControls):
    """Controls whose verify control repeats an intended fault instead of observing it."""

    def verify_condition(self, context):
        if self._fault_intended(context):
            return context.condition.condition_id
        return super().verify_condition(context)


def test_a_fault_that_is_only_labelled_leaves_the_metric_unavailable():
    # Both Attempts under the variation carry the label of the fault and nothing was observed,
    # so the fault has no valid Attempt. The value is not 0/2 divided by the baseline 2/2.
    fixture = Fixture(controls=LabelsTheFault)

    scorecard = fixture.sweep(fixture.experiment(attempts=2))

    fault_robustness = metric(scorecard)
    assert fault_robustness["state"] == "unavailable"
    assert fault_robustness["value"] is None
    assert fault_robustness["reason"] == "no valid Attempt under the fault variation"
    assert fault_robustness["counts"]["tasks"] == {
        "total": 1,
        "valid": 0,
        "missing": [],
        "excluded": [{"reason": "no valid Attempt under the fault variation", "count": 1, "ids": ["add"]}],
    }
    assert fault_robustness["counts"]["fault_attempts"] == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {
                "reason": NOT_VERIFIED,
                "count": 2,
                "ids": ["add-item/add-fails/attempt-1", "add-item/add-fails/attempt-2"],
            }
        ],
    }


def test_accuracy_is_balanced_over_tasks_and_not_pooled_over_attempts():
    # quick, 4 Attempts under each condition: baseline 4/4 = 1. Under the fault the agent works
    # around it twice: 2/4 = 0.5.
    # long, 2 Attempts under each condition: the agent forgets its item in one baseline Attempt:
    # 1/2 = 0.5. Under the fault it gives up twice: 0/2 = 0.
    # Baseline accuracy (1 + 0.5) / 2 = 0.75. Under the fault (0.5 + 0) / 2 = 0.25. 0.25 / 0.75 = 1/3.
    # Pooling the Attempts would give (2/6) / (5/6) = 0.4.
    fixture = Fixture(
        {
            "quick-item/add-fails/attempt-1": "works around",
            "quick-item/add-fails/attempt-2": "works around",
            "long-item/baseline/attempt-2": "forgets",
        },
        task_ids=("quick", "long"),
    )

    scorecard = fixture.sweep(fixture.experiment("quick", attempts=4), fixture.experiment("long", attempts=2))

    fault_robustness = metric(scorecard)
    assert fault_robustness["value"] == pytest.approx(1 / 3, abs=1e-12)
    assert fault_robustness["details"]["baseline"]["accuracy"] == 0.75
    assert fault_robustness["details"]["fault"]["accuracy"] == 0.25
    assert fault_robustness["counts"]["tasks"]["valid"] == 2
    assert fault_robustness["counts"]["baseline_attempts"]["valid"] == 6
    assert fault_robustness["counts"]["fault_attempts"]["valid"] == 6


def test_an_agent_that_does_better_under_the_fault_scores_1_and_the_scorecard_keeps_the_ratio():
    # Baseline: the agent forgets its item in the second Attempt: 1/2 = 0.5.
    # Fault: it works around the refused item both times: 2/2 = 1. 1 / 0.5 = 2, capped at 1.
    fixture = Fixture(
        {
            "add-item/baseline/attempt-2": "forgets",
            "add-item/add-fails/attempt-1": "works around",
            "add-item/add-fails/attempt-2": "works around",
        }
    )

    scorecard = fixture.sweep(fixture.experiment(attempts=2))

    fault_robustness = metric(scorecard)
    assert fault_robustness["value"] == 1
    assert fault_robustness["details"]["accuracy_ratio"] == 2
    assert fault_robustness["details"]["capped"] is True


def test_a_baseline_that_never_succeeds_gives_an_unavailable_measurement_that_no_criterion_can_pass():
    # Baseline 0/2 = 0: the agent forgets its item twice. Fault 2/2 = 1. 1 / 0 is undefined.
    fixture = Fixture(
        {
            "add-item/baseline/attempt-1": "forgets",
            "add-item/baseline/attempt-2": "forgets",
            "add-item/add-fails/attempt-1": "works around",
            "add-item/add-fails/attempt-2": "works around",
        }
    )
    criterion = AcceptanceCriterion(metric_id="fault_robustness", condition={"value": {"at_least": 0.5}})

    scorecard = fixture.sweep(fixture.experiment(attempts=2), acceptance_criteria=[criterion])

    fault_robustness = metric(scorecard)
    assert fault_robustness["state"] == "unavailable"
    assert fault_robustness["value"] is None
    assert fault_robustness["reason"] == "the baseline accuracy is zero, so the ratio is undefined"
    assert fault_robustness["counts"]["baseline_attempts"]["valid"] == 2
    assert fault_robustness["counts"]["fault_attempts"]["valid"] == 2
    assert fault_robustness["acceptance"]["status"] == "not_assessed"


def test_a_criterion_can_require_a_number_of_valid_attempts_under_the_fault():
    # Baseline 3/3 = 1. Under the fault the agent never asks the service in the first Attempt, which
    # is left out, and works around the fault in the other two: 2/2 = 1. The value is 1.
    # It rests on 2 valid Attempts under the fault, and the criterion asks for 3.
    fixture = Fixture(
        {
            "add-item/add-fails/attempt-1": "imports",
            "add-item/add-fails/attempt-2": "works around",
            "add-item/add-fails/attempt-3": "works around",
        }
    )
    criterion = AcceptanceCriterion(
        metric_id="fault_robustness",
        condition={"value": {"at_least": 0.9}, "minimum_counts": {"fault_attempts": 3}},
    )

    scorecard = fixture.sweep(fixture.experiment(attempts=3), acceptance_criteria=[criterion])

    fault_robustness = metric(scorecard)
    assert fault_robustness["value"] == 1
    assert fault_robustness["acceptance"]["status"] == "not_met"
    assert fault_robustness["acceptance"]["checks"] == [
        {"subject": "value", "comparison": "at_least", "threshold": 0.9, "observed": 1.0, "met": True},
        {
            "subject": "counts.fault_attempts.valid",
            "comparison": "at_least",
            "threshold": 3,
            "observed": 2,
            "met": False,
        },
    ]


def test_the_metrics_of_repeated_attempts_still_rest_on_the_baseline_attempts_only():
    # Baseline 2/2, fault 0/2. Fault robustness compares the two: 0 / 1 = 0.
    # Outcome consistency rests on the two baseline Attempts: p = 2/2 and the value is 1.
    # With the Attempts under the fault mixed in it would be p = 2/4 and the value 0.
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=2), metrics=["outcome_consistency", "fault_robustness"])

    assert len(scorecard["attempts"]) == 4
    baseline = ["add-item/baseline/attempt-1", "add-item/baseline/attempt-2"]
    under_fault = ["add-item/add-fails/attempt-1", "add-item/add-fails/attempt-2"]
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["evidence"] == {"attempt_ids": baseline}
    assert outcome_consistency["attempts_used"] == {"attempt_ids": baseline, "shared_with": ["fault_robustness"]}
    fault_robustness = metric(scorecard)
    assert fault_robustness["value"] == 0
    assert fault_robustness["evidence"] == {"attempt_ids": baseline + under_fault}
    assert fault_robustness["attempts_used"] == {
        "attempt_ids": baseline + under_fault,
        "shared_with": ["outcome_consistency"],
    }


def test_fault_robustness_rests_only_on_the_experiments_that_declare_a_fault():
    # "add" declares the fault: baseline 2/2 = 1, fault 0/2 = 0, value 0.
    # "tidy" declares a variation of no kind, "archive" declares a baseline only, and "plain" is a
    # Task without an experiment. None of their Attempts is part of the comparison.
    fixture = Fixture(task_ids=("add", "tidy", "archive", "plain"))
    reworded = Condition(condition_id="reworded", description="The prompt is reworded.")

    scorecard = fixture.sweep(
        fixture.experiment("add", attempts=2),
        fixture.experiment("tidy", attempts=2, variation=reworded),
        fixture.experiment("archive", attempts=2, variation=None),
        tasks=[fixture.task("plain", attempts=2)],
    )

    assert len(scorecard["attempts"]) == 12
    fault_robustness = metric(scorecard)
    assert fault_robustness["value"] == 0
    nothing_left_out = {"missing": [], "excluded": []}
    assert fault_robustness["counts"] == {
        "tasks": {"total": 1, "valid": 1, **nothing_left_out},
        "baseline_attempts": {"total": 2, "valid": 2, **nothing_left_out},
        "fault_attempts": {"total": 2, "valid": 2, **nothing_left_out},
        "other_attempts": {"total": 0, "valid": 0, **nothing_left_out},
    }


def test_fault_robustness_is_rejected_before_any_attempt_when_no_experiment_declares_a_fault():
    fixture = Fixture(task_ids=("add", "plain"))
    reworded = Condition(condition_id="reworded", description="The prompt is reworded.")
    needs_a_fault = "'fault_robustness' needs an experiment whose variation declares the kind 'fault'"

    with pytest.raises(ValueError, match=needs_a_fault):
        fixture.sweep(tasks=[fixture.task("plain", attempts=2)])
    with pytest.raises(ValueError, match=needs_a_fault):
        fixture.sweep(fixture.experiment(attempts=2, variation=None))
    with pytest.raises(ValueError, match=needs_a_fault):
        fixture.sweep(fixture.experiment(attempts=2, variation=reworded))
    assert fixture.agent.invocations == 0


def test_the_scorecard_records_the_kind_of_the_variation():
    fixture = Fixture()

    scorecard = fixture.sweep(fixture.experiment(attempts=1))

    (experiment,) = scorecard["experiments"]
    assert experiment["baseline"] == {
        "condition_id": "baseline",
        "description": "The to-do service accepts every request.",
        "parameters": {},
    }
    assert experiment["variation"] == {
        "condition_id": "add-fails",
        "kind": "fault",
        "description": "The to-do service refuses to add an item.",
        "parameters": {"add_fails": True},
    }
    assert attempt(scorecard, "add-item/baseline/attempt-1")["intended_condition"] == {
        "role": "baseline",
        **experiment["baseline"],
    }
    assert attempt(scorecard, "add-item/add-fails/attempt-1")["intended_condition"] == {
        "role": "variation",
        **experiment["variation"],
    }


def test_the_baseline_of_an_experiment_declares_no_kind_of_variation():
    refusing = Condition(condition_id="baseline", kind="fault", description="Every request is refused.")
    problem = "the baseline of experiment 'add-item' is not a variation, so it declares no kind"

    with pytest.raises(ValueError, match=problem):
        Fixture().experiment(attempts=1, baseline=refusing)


def test_a_condition_cannot_declare_a_kind_of_variation_that_does_not_exist():
    with pytest.raises(ValueError, match="'outage' is not a kind of variation"):
        Condition(condition_id="outage", kind="outage", description="The service is down.")


def test_an_experiment_that_was_stopped_or_never_started_still_counts_as_a_task_of_the_comparison():
    # "add" could not clean up after its first Attempt, so its other three Attempts were not made.
    # "tidy" may change the same list and was not started. Neither condition has a valid Attempt.
    fixture = Fixture(
        task_ids=("add", "tidy"),
        failures={("clean_up", "add-item/baseline/attempt-1"): "the list could not be deleted"},
    )
    shared = {"authorized_resources": ["todo-list/shared"]}

    scorecard = fixture.sweep(
        fixture.experiment("add", attempts=2, **shared), fixture.experiment("tidy", attempts=2, **shared)
    )

    assert [entry["execution"]["status"] for entry in scorecard["experiments"]] == ["stopped", "stopped"]
    # Both experiments stopped, so the metric has no result. That is an execution failure and
    # not an unavailable measurement. What the Evaluator found is kept in its reason and counts.
    fault_robustness = metric(scorecard)
    assert fault_robustness["state"] is None
    assert fault_robustness["value"] is None
    assert fault_robustness["execution_failure"]["experiment_ids"] == ["add-item", "tidy-item"]
    assert fault_robustness["execution_failure"]["reason"].endswith(
        "no valid Attempt under the baseline and none under the fault variation"
    )
    assert fault_robustness["counts"]["tasks"] == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [{"reason": "no valid Attempt under either condition", "count": 2, "ids": ["add", "tidy"]}],
    }
    assert fault_robustness["counts"]["baseline_attempts"] == {
        "total": 1,
        "valid": 0,
        "missing": [],
        "excluded": [
            {"reason": "execution status is not completed", "count": 1, "ids": ["add-item/baseline/attempt-1"]}
        ],
    }
    assert fault_robustness["counts"]["fault_attempts"] == {"total": 0, "valid": 0, "missing": [], "excluded": []}

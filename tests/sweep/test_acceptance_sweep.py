"""Evaluator sweep seam: customer acceptance criteria, from declaration to the reliability scorecard.

The sweep is driven through its public entry point with a fake agent target. Every
assertion is on the machine-readable scorecard. Expected values are calculated by hand.
"""

import json

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.scorecard import AcceptanceCriterion, CustomerAssessment, to_json
from reliability_evaluators.sweep import Task, run_sweep
from tests.fakes import FakeAgentTarget

SOURCE = "final state of the fake service"

FOUR_IN_FIVE = ["success", "success", "success", "success", "failure"]
ALWAYS_FAILS = ["failure", "failure", "failure"]
# close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Outcome consistency: (0.36 + 1) / 2 = 0.68.
TWO_TASKS = {"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS}


def sweep(script, acceptance_criteria=(), metrics=("outcome_consistency",)):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = FakeAgentTarget(script)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    return run_sweep(tasks=tasks, agent_target=target, metrics=metrics, acceptance_criteria=acceptance_criteria)


def on_value(**comparisons):
    return AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": comparisons})


def acceptance(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry["acceptance"]


def test_no_criterion_means_no_acceptance_verdict_for_any_metric():
    scorecard = sweep(TWO_TASKS)

    assert [entry["acceptance"] for entry in scorecard["metrics"]] == [None] * 12


def test_a_metric_value_that_satisfies_the_criterion_is_met():
    scorecard = sweep(TWO_TASKS, [on_value(at_least=0.5)])

    assert acceptance(scorecard) == {
        "condition": {"value": {"at_least": 0.5}},
        "status": "met",
        "reason": None,
        "assessed_by": "offering",
        "provenance": None,
        "checks": [
            {
                "subject": "value",
                "comparison": "at_least",
                "threshold": 0.5,
                "observed": pytest.approx(0.68, abs=1e-12),
                "met": True,
            },
        ],
    }


def test_a_metric_value_that_does_not_satisfy_the_criterion_is_not_met():
    assessment = acceptance(sweep(TWO_TASKS, [on_value(at_least=0.9)]))

    assert assessment["status"] == "not_met"
    assert assessment["reason"] is None
    assert assessment["checks"] == [
        {
            "subject": "value",
            "comparison": "at_least",
            "threshold": 0.9,
            "observed": pytest.approx(0.68, abs=1e-12),
            "met": False,
        },
    ]


@pytest.mark.parametrize(
    ("comparison", "threshold", "status"),
    [
        # One Task with 4 successes in 5 Attempts: the value is exactly 0.36.
        ("at_least", 0.36, "met"),
        ("at_least", 0.37, "not_met"),
        ("greater_than", 0.36, "not_met"),
        ("greater_than", 0.35, "met"),
        ("at_most", 0.36, "met"),
        ("at_most", 0.35, "not_met"),
        ("less_than", 0.36, "not_met"),
        ("less_than", 0.37, "met"),
    ],
)
def test_a_criterion_compares_the_metric_value_in_either_direction(comparison, threshold, status):
    assessment = acceptance(sweep({"close": FOUR_IN_FIVE}, [on_value(**{comparison: threshold})]))

    assert assessment["status"] == status
    assert assessment["checks"] == [
        {
            "subject": "value",
            "comparison": comparison,
            "threshold": threshold,
            "observed": 0.36,
            "met": status == "met",
        },
    ]


def test_a_criterion_can_bound_the_metric_value_on_both_sides():
    # 0.36 is at least 0.2 and is not at most 0.3.
    assessment = acceptance(sweep({"close": FOUR_IN_FIVE}, [on_value(at_least=0.2, at_most=0.3)]))

    assert assessment["status"] == "not_met"
    assert [(check["comparison"], check["met"]) for check in assessment["checks"]] == [
        ("at_least", True),
        ("at_most", False),
    ]


def on_outcome_consistency(condition, **declared):
    return AcceptanceCriterion(metric_id="outcome_consistency", condition=condition, **declared)


# "archive" has one Attempt and is excluded, so 1 of 2 Tasks and 5 of 6 Attempts are valid evidence.
ONE_TASK_EXCLUDED = {"close": FOUR_IN_FIVE, "archive": ["success"]}


def test_a_criterion_can_require_minimum_evidence_counts():
    criterion = on_outcome_consistency({"minimum_counts": {"tasks": 1, "attempts": 5}})

    assessment = acceptance(sweep(ONE_TASK_EXCLUDED, [criterion]))

    assert assessment["status"] == "met"
    assert assessment["checks"] == [
        {"subject": "counts.tasks.valid", "comparison": "at_least", "threshold": 1, "observed": 1, "met": True},
        {"subject": "counts.attempts.valid", "comparison": "at_least", "threshold": 5, "observed": 5, "met": True},
    ]


def test_a_minimum_evidence_count_is_of_the_evidence_the_value_was_calculated_from():
    # Two Tasks were declared and one was used. A minimum of two Tasks is not met.
    criterion = on_outcome_consistency({"minimum_counts": {"tasks": 2}})

    assessment = acceptance(sweep(ONE_TASK_EXCLUDED, [criterion]))

    assert assessment["status"] == "not_met"
    assert assessment["checks"] == [
        {"subject": "counts.tasks.valid", "comparison": "at_least", "threshold": 2, "observed": 1, "met": False},
    ]


def test_a_criterion_on_the_value_and_on_evidence_counts_is_met_only_when_both_hold():
    # The value 0.36 is at most 0.5, but it rests on one Task where three are required.
    criterion = on_outcome_consistency({"value": {"at_most": 0.5}, "minimum_counts": {"tasks": 3}})

    assessment = acceptance(sweep(ONE_TASK_EXCLUDED, [criterion]))

    assert assessment["status"] == "not_met"
    assert assessment["checks"] == [
        {"subject": "value", "comparison": "at_most", "threshold": 0.5, "observed": 0.36, "met": True},
        {"subject": "counts.tasks.valid", "comparison": "at_least", "threshold": 3, "observed": 1, "met": False},
    ]


def not_assessed(condition, reason):
    return {
        "condition": condition,
        "status": "not_assessed",
        "reason": reason,
        "assessed_by": None,
        "provenance": None,
        "checks": [],
    }


@pytest.mark.parametrize(
    "condition",
    [
        {"value": {"at_least": 0.5}},
        {"value": {"at_most": 0.5}},
        # Zero Tasks are valid, so this count is satisfied. The measurement is still not assessed.
        {"minimum_counts": {"tasks": 0}},
    ],
)
def test_an_unavailable_measurement_is_not_assessed_whatever_the_criterion(condition):
    scorecard = sweep({"close": ["success"], "archive": ["failure"]}, [on_outcome_consistency(condition)])

    assert acceptance(scorecard) == not_assessed(
        condition,
        "the metric is an unavailable measurement: no Task has at least two Attempts with a task outcome",
    )


def test_a_criterion_for_a_metric_that_is_not_configured_is_not_assessed():
    criterion = AcceptanceCriterion(metric_id="calibration", condition={"value": {"at_most": 0.1}})

    scorecard = sweep(TWO_TASKS, [criterion])

    assert acceptance(scorecard, "calibration") == not_assessed(
        {"value": {"at_most": 0.1}}, "the metric is not configured"
    )
    assert acceptance(scorecard) is None


@pytest.mark.parametrize(
    ("condition", "problem"),
    [
        # The value is 0.68 and rests on 2 Tasks, so a lenient reading of each of these would be met.
        ("at least 0.5", "'at least 0.5'"),
        (None, "None"),
        ({}, "states no condition"),
        ({"value": {}}, "states no condition"),
        ({"value": 0.5}, "'value'"),
        ({"value": {"between": [0.5, 0.9]}}, "'between'"),
        ({"value": {"at_least": "0.5"}}, "'0.5'"),
        ({"value": {"at_least": True}}, "True"),
        ({"minimum_counts": {"tasks": 1.5}}, "1.5"),
        ({"minimum_counts": {"tasks": -1}}, "-1"),
        ({"minimum_counts": {"pairs": 0}}, "no evidence count for 'pairs'"),
        ({"every_task": {"at_least": 0.3}}, "'every_task'"),
        ({"value": {"at_least": 0.5}, "every_task": {"at_least": 0.3}}, "'every_task'"),
    ],
)
def test_an_unsupported_criterion_is_not_assessed_and_never_passes(condition, problem):
    assessment = acceptance(sweep(TWO_TASKS, [on_outcome_consistency(condition)]))

    assert assessment["status"] == "not_assessed"
    assert assessment["reason"].startswith("unsupported criterion: ")
    assert problem in assessment["reason"]
    assert assessment["condition"] == condition
    assert assessment["checks"] == []


def test_an_unsupported_criterion_is_reported_as_unsupported_even_when_the_measurement_is_unavailable():
    criterion = on_outcome_consistency({"value": {"between": [0.5, 0.9]}})

    assessment = acceptance(sweep({"close": ["success"]}, [criterion]))

    assert assessment["status"] == "not_assessed"
    assert assessment["reason"].startswith("unsupported criterion: ")


RELEASE_BOARD = "release-gate.py at 4f2c1d9, rule 'no Task below the floor'"


def no_task_below(floor):
    """A customer's own assessment, of a condition the offering does not assess."""

    def assess(metric):
        met = all(task["value"] >= floor for task in metric["details"]["per_task"])
        return CustomerAssessment(met=met, provenance=RELEASE_BOARD)

    return assess


@pytest.mark.parametrize(
    ("floor", "status"),
    # The Task values are 0.36 and 1.
    [(0.3, "met"), (0.5, "not_met")],
)
def test_a_customer_supplied_assessment_is_recorded_with_its_provenance(floor, status):
    condition = f"no Task value below {floor}"
    criterion = on_outcome_consistency(condition, customer_assessment=no_task_below(floor))

    assert acceptance(sweep(TWO_TASKS, [criterion])) == {
        "condition": condition,
        "status": status,
        "reason": None,
        "assessed_by": "customer",
        "provenance": RELEASE_BOARD,
        "checks": [],
    }


def test_a_customer_supplied_assessment_cannot_pass_or_fail_an_unavailable_measurement():
    asked = []

    def always_met(metric):
        asked.append(metric)
        return CustomerAssessment(met=True, provenance=RELEASE_BOARD)

    criterion = on_outcome_consistency("accepted by the release board", customer_assessment=always_met)

    scorecard = sweep({"close": ["success"], "archive": ["failure"]}, [criterion])

    assert acceptance(scorecard) == not_assessed(
        "accepted by the release board",
        "the metric is an unavailable measurement: no Task has at least two Attempts with a task outcome",
    )
    assert asked == []


def raises(metric):
    raise KeyError("per_condition")


@pytest.mark.parametrize(
    ("assessor", "problem"),
    [
        (lambda metric: True, "returned True"),
        (lambda metric: None, "returned None"),
        (lambda metric: CustomerAssessment(met=True, provenance=""), "provenance"),
        (lambda metric: CustomerAssessment(met=1, provenance=RELEASE_BOARD), "met or not met"),
        (lambda metric: CustomerAssessment(met="yes", provenance=RELEASE_BOARD), "met or not met"),
        (raises, "KeyError: 'per_condition'"),
    ],
)
def test_a_customer_supplied_assessment_that_cannot_be_recorded_is_not_assessed(assessor, problem):
    criterion = on_outcome_consistency("accepted by the release board", customer_assessment=assessor)

    scorecard = sweep(TWO_TASKS, [criterion])

    assessment = acceptance(scorecard)
    assert assessment["status"] == "not_assessed"
    assert assessment["reason"].startswith("the customer-supplied assessment ")
    assert problem in assessment["reason"]
    assert assessment["assessed_by"] is None
    assert assessment["provenance"] is None
    # The measurement is kept.
    assert scorecard["metrics"][0]["value"] == pytest.approx(0.68, abs=1e-12)


@pytest.mark.parametrize(
    ("criteria", "problem"),
    [
        (
            lambda: [AcceptanceCriterion(metric_id="overall_reliability", condition={"value": {"at_least": 0.8}})],
            "not a reliability metric",
        ),
        (lambda: [on_value(at_least=0.8), on_value(at_most=0.9)], "more than one acceptance criterion"),
        (lambda: [on_value(at_least=float("nan"))], "JSON"),
        (lambda: [on_outcome_consistency({"value": {"at_least": {0.8}}})], "JSON"),
    ],
)
def test_acceptance_criteria_the_scorecard_could_not_report_are_rejected_before_any_attempt(criteria, problem):
    target = FakeAgentTarget({"close": ["success"] * 2})
    always_succeeds = Task(
        task_id="close",
        prompt="Close it.",
        success_rule=lambda attempt, response: TaskOutcome(success=True, source=SOURCE),
        attempts=2,
    )

    with pytest.raises(ValueError, match=problem):
        run_sweep(
            tasks=[always_succeeds],
            agent_target=target,
            metrics=["outcome_consistency"],
            acceptance_criteria=criteria(),
        )

    assert target.service_state == {}


def test_a_customer_supplied_assessment_cannot_change_the_measurement():
    def overwrites(metric):
        metric["value"] = 1.0
        metric["counts"]["tasks"]["valid"] = 100
        return CustomerAssessment(met=True, provenance=RELEASE_BOARD)

    scorecard = sweep(TWO_TASKS, [on_outcome_consistency("accepted as is", customer_assessment=overwrites)])

    assert scorecard["metrics"][0]["value"] == pytest.approx(0.68, abs=1e-12)
    assert scorecard["metrics"][0]["counts"]["tasks"]["valid"] == 2


@pytest.mark.parametrize(
    ("behaviours", "task_succeeded", "status"),
    [
        # Every Attempt fails, which is perfectly consistent: the value is 1 and at least 0.9.
        (["failure"] * 3, False, "met"),
        # Every Attempt succeeds, and the value is again 1. A criterion of at most 0.5 is not met.
        (["success"] * 3, True, "not_met"),
    ],
)
def test_acceptance_is_reported_separately_from_task_outcomes(behaviours, task_succeeded, status):
    criterion = on_value(at_least=0.9) if status == "met" else on_value(at_most=0.5)

    scorecard = sweep({"close": behaviours}, [criterion])

    assert acceptance(scorecard)["status"] == status
    assert [attempt["task_outcome"] for attempt in scorecard["attempts"]] == [
        {"success": task_succeeded, "source": SOURCE}
    ] * 3
    assert set(scorecard) == {
        "schema_version",
        "status",
        "missing",
        "concurrency",
        "metrics",
        "experiments",
        "tasks_without_experiment",
        "token_usage",
        "attempts",
    }
    assert all("acceptance" not in attempt for attempt in scorecard["attempts"])


def test_a_pipeline_can_decide_its_gate_from_the_json_scorecard_alone():
    criteria = [
        on_outcome_consistency({"value": {"at_least": 0.9}, "minimum_counts": {"tasks": 2}}),
        AcceptanceCriterion(metric_id="calibration", condition={"value": {"at_most": 0.1}}),
    ]

    document = json.loads(to_json(sweep(TWO_TASKS, criteria)))

    assessed = {entry["metric_id"]: entry["acceptance"] for entry in document["metrics"] if entry["acceptance"]}
    assert assessed == {
        "outcome_consistency": {
            "condition": {"value": {"at_least": 0.9}, "minimum_counts": {"tasks": 2}},
            "status": "not_met",
            "reason": None,
            "assessed_by": "offering",
            "provenance": None,
            "checks": [
                {
                    "subject": "value",
                    "comparison": "at_least",
                    "threshold": 0.9,
                    "observed": pytest.approx(0.68, abs=1e-12),
                    "met": False,
                },
                {"subject": "counts.tasks.valid", "comparison": "at_least", "threshold": 2, "observed": 2, "met": True},
            ],
        },
        "calibration": not_assessed({"value": {"at_most": 0.1}}, "the metric is not configured"),
    }

"""Evaluator sweep seam: outcome consistency, from declared Tasks to the reliability scorecard.

The sweep is driven through its public entry point with a fake agent target. Every
assertion is on the machine-readable scorecard. Expected values are calculated by hand.
"""

import json

import pytest

from reliability_evaluators.evidence import EvidenceError, TaskOutcome
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import Task, run_sweep
from tests.fakes import FakeAgentTarget

SOURCE = "final state of the fake service"


def sweep(script, metrics=("outcome_consistency",)):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = FakeAgentTarget(script)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    return run_sweep(tasks=tasks, agent_target=target, metrics=metrics)


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


# What an Attempt records about its experiment when its Task was given without one.
NO_EXPERIMENT = {
    "experiment_id": None,
    "starting_state": None,
    "intended_condition": None,
    "observed_condition": None,
    "lifecycle": [],
}

FOUR_IN_FIVE = ["success", "success", "success", "success", "failure"]
ALWAYS_FAILS = ["failure", "failure", "failure"]


def test_a_sweep_scores_outcome_consistency_in_the_scorecard():
    # close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Mean: (0.36 + 1) / 2 = 0.68.
    scorecard = sweep({"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS})

    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)


def test_the_machine_readable_scorecard_is_json():
    scorecard = sweep({"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS})

    assert json.loads(to_json(scorecard)) == scorecard


def test_the_scorecard_says_how_outcome_consistency_is_defined_and_how_to_read_it():
    outcome_consistency = metric(sweep({"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS}))

    definition = outcome_consistency["definition"]
    assert definition["version"] == "1.0.0"
    assert "(2p - 1)^2" in definition["calculation"]
    assert "weighted equally" in definition["calculation"]
    assert any("companion code" in convention for convention in definition["fidelity_conventions"])
    assert any("at least two Attempts" in convention for convention in definition["fidelity_conventions"])
    assert any("eligible Tasks" in convention for convention in definition["fidelity_conventions"])
    assert (definition["scale"]["minimum"], definition["scale"]["maximum"]) == (0, 1)
    assert "Consistent is not successful" in definition["interpretation"]
    assert outcome_consistency["scoring_path"] == "customer_process"
    assert outcome_consistency["fidelity"] == "authoritative"


def test_the_scorecard_gives_the_counts_and_denominators_behind_the_value():
    outcome_consistency = metric(sweep({"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS}))

    assert outcome_consistency["counts"] == {
        "tasks": {"total": 2, "valid": 2, "missing": [], "excluded": []},
        "attempts": {"total": 8, "valid": 8, "missing": [], "excluded": []},
    }
    assert outcome_consistency["details"]["per_task"] == [
        {"task_id": "close", "attempts": 5, "successes": 4, "value": pytest.approx(0.36, abs=1e-12)},
        {"task_id": "archive", "attempts": 3, "successes": 0, "value": 1.0},
    ]


def test_the_value_refers_to_attempt_level_evidence_in_the_scorecard():
    scorecard = sweep({"close": ["success", "failure"]})

    assert metric(scorecard)["evidence"] == {"attempt_ids": ["close/attempt-1", "close/attempt-2"]}
    assert scorecard["attempts"] == [
        {
            "attempt_id": "close/attempt-1",
            "task_id": "close",
            "execution_status": "completed",
            "execution_error": None,
            "task_outcome": {"success": True, "source": SOURCE},
            "action_evidence": None,
            "operational_constraints": [],
            "violation_assessments": [],
            **NO_EXPERIMENT,
        },
        {
            "attempt_id": "close/attempt-2",
            "task_id": "close",
            "execution_status": "completed",
            "execution_error": None,
            "task_outcome": {"success": False, "source": SOURCE},
            "action_evidence": None,
            "operational_constraints": [],
            "violation_assessments": [],
            **NO_EXPERIMENT,
        },
    ]


TWELVE_METRICS = [
    "outcome_consistency",
    "distributional_trajectory_consistency",
    "sequential_trajectory_consistency",
    "resource_consistency",
    "fault_robustness",
    "environment_robustness",
    "prompt_robustness",
    "calibration",
    "strict_confidence_discrimination",
    "brier_based_score",
    "constraint_compliance",
    "conditional_harm_severity",
]


def test_the_other_eleven_metrics_appear_as_not_configured():
    scorecard = sweep({"close": FOUR_IN_FIVE})

    assert [entry["metric_id"] for entry in scorecard["metrics"]] == TWELVE_METRICS
    assert [entry["state"] for entry in scorecard["metrics"]] == ["available"] + ["not_configured"] * 11
    assert metric(scorecard, "calibration") == {
        "metric_id": "calibration",
        "name": "Calibration",
        "dimension": "predictability",
        "state": "not_configured",
        "acceptance": None,
    }


def test_there_is_no_combined_score_and_no_built_in_threshold():
    scorecard = sweep({"close": FOUR_IN_FIVE})

    assert set(scorecard) == {"schema_version", "metrics", "experiments", "attempts"}
    assert set(metric(scorecard)) == {
        "metric_id",
        "name",
        "dimension",
        "state",
        "value",
        "reason",
        "definition",
        "scoring_path",
        "fidelity",
        "counts",
        "details",
        "evidence",
        "acceptance",
    }


def test_an_invocation_that_fails_is_an_execution_status_and_not_a_task_failure():
    # Two successes and one failed invocation: p = 2/2 and the value is 1.
    # Scoring the failed invocation as a task failure would give (2 * 2/3 - 1)^2 = 0.111...
    scorecard = sweep({"close": ["success", "error", "success"]})

    assert scorecard["attempts"][1] == {
        "attempt_id": "close/attempt-2",
        "task_id": "close",
        "execution_status": "failed",
        "execution_error": "ConnectionError: the agent endpoint returned 503",
        "task_outcome": None,
        "action_evidence": None,
        "operational_constraints": [],
        "violation_assessments": [],
        **NO_EXPERIMENT,
    }
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["counts"]["attempts"] == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [
            {"reason": "execution status is not completed", "count": 1, "ids": ["close/attempt-2"]},
        ],
    }
    assert outcome_consistency["evidence"] == {"attempt_ids": ["close/attempt-1", "close/attempt-3"]}


def test_a_task_with_one_attempt_is_excluded_from_the_metric_with_a_reason():
    # Only "close" is eligible, so the value is its 0.36 and not (0.36 + 1) / 2 = 0.68.
    outcome_consistency = metric(sweep({"close": FOUR_IN_FIVE, "archive": ["success"]}))

    assert outcome_consistency["value"] == pytest.approx(0.36, abs=1e-12)
    assert outcome_consistency["counts"]["tasks"] == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [
            {"reason": "fewer than two Attempts with a task outcome", "count": 1, "ids": ["archive"]},
        ],
    }


def test_no_eligible_task_gives_an_unavailable_measurement_and_no_number():
    outcome_consistency = metric(sweep({"close": ["success"], "archive": ["failure"]}))

    assert outcome_consistency["state"] == "unavailable"
    assert outcome_consistency["value"] is None
    assert outcome_consistency["reason"] == "no Task has at least two Attempts with a task outcome"
    assert outcome_consistency["counts"]["tasks"]["total"] == 2
    assert outcome_consistency["counts"]["tasks"]["valid"] == 0
    assert outcome_consistency["counts"]["attempts"]["total"] == 2
    assert outcome_consistency["evidence"] == {"attempt_ids": []}


def test_no_tasks_gives_an_unavailable_measurement_and_no_number():
    outcome_consistency = metric(sweep({}))

    assert outcome_consistency["state"] == "unavailable"
    assert outcome_consistency["value"] is None
    assert outcome_consistency["reason"] == "no Tasks in the evidence"
    assert outcome_consistency["counts"]["tasks"]["total"] == 0


def one_task(success_rule=lambda attempt, response: TaskOutcome(success=True, source=SOURCE), **declared):
    return Task(**{"task_id": "close", "prompt": "Close it.", "success_rule": success_rule, "attempts": 2, **declared})


@pytest.mark.parametrize("returned", [True, 0.5, None])
def test_a_task_success_rule_that_returns_no_sourced_task_outcome_is_rejected(returned):
    tasks = [one_task(success_rule=lambda attempt, response: returned)]

    with pytest.raises(EvidenceError, match="TaskOutcome"):
        run_sweep(tasks=tasks, agent_target=FakeAgentTarget({"close": ["success"] * 2}), metrics=["outcome_consistency"])


def test_a_task_success_rule_that_decides_a_fractional_outcome_is_rejected():
    tasks = [one_task(success_rule=lambda attempt, response: TaskOutcome(success=0.5, source=SOURCE))]

    with pytest.raises(EvidenceError, match="binary"):
        run_sweep(tasks=tasks, agent_target=FakeAgentTarget({"close": ["success"] * 2}), metrics=["outcome_consistency"])


@pytest.mark.parametrize(
    ("tasks", "metrics", "problem"),
    [
        (lambda: [one_task(), one_task()], ["outcome_consistency"], "more than one Task"),
        (lambda: [one_task(attempts=0)], ["outcome_consistency"], "at least one Attempt"),
        (lambda: [one_task(task_id="")], ["outcome_consistency"], "task_id"),
        (lambda: [one_task()], ["overall_reliability"], "not a reliability metric"),
        (lambda: [one_task()], ["calibration"], "no Evaluator"),
    ],
)
def test_a_declaration_the_sweep_cannot_run_is_rejected(tasks, metrics, problem):
    with pytest.raises(ValueError, match=problem):
        run_sweep(tasks=tasks(), agent_target=FakeAgentTarget({"close": ["success"] * 4}), metrics=metrics)

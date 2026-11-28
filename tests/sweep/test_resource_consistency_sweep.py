"""Evaluator sweep seam: resource consistency, from declared Tasks to the reliability scorecard.

The sweep is driven through its public entry point with a fake agent target, whose clock
the sweep reads. Every assertion is on the machine-readable scorecard. Expected values
are calculated by hand: the coefficient of variation is the population standard
deviation divided by the mean, and the Task value is `exp(-mean of CVs)`.
"""

import json

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import Task, run_sweep
from tests.fakes import OWN_ELAPSED_BOUNDARY, TOOL_CALL_BOUNDARY, FakeAgentTarget, attempt_evidence
from tests.fakes import fake_baseline_experiments

SOURCE = "final state of the fake service"
SWEEP_ELAPSED_BOUNDARY = (
    "wall clock from just before the agent target is invoked until it returns, measured by the Evaluator sweep"
)
EXP_MINUS_QUARTER = 0.7788007830714049
EXP_MINUS_HALF = 0.6065306597126334


def sweep(script, resources, metrics=("resource_consistency",)):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = FakeAgentTarget(script, resources)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    return run_sweep(experiments=fake_baseline_experiments(tasks), agent_target=target, metrics=metrics, clock=target.clock)


def metric(scorecard, metric_id="resource_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


# close: the successful Attempt takes 1 second and the failed one 3, both with 2 tool calls.
#        Elapsed time: mean 2, population standard deviation 1, CV 0.5. Tool calls: CV 0.
#        Mean of the CVs 0.25, Task value exp(-0.25) = 0.7788007830714049.
# archive: three Attempts of 4 seconds and 1 tool call each. Both CVs 0, Task value 1.
SCRIPT = {"close": ["success", "failure"], "archive": ["success", "success", "success"]}
RESOURCES = {
    "close": [{"seconds": 1.0, "tool_calls": 2}, {"seconds": 3.0, "tool_calls": 2}],
    "archive": [{"seconds": 4.0, "tool_calls": 1}] * 3,
}


def test_a_sweep_scores_resource_consistency_in_the_scorecard():
    # (0.7788007830714049 + 1) / 2 = 0.8894003915357025
    scorecard = sweep(SCRIPT, RESOURCES)

    resource_consistency = metric(scorecard)
    assert resource_consistency["state"] == "available"
    assert resource_consistency["value"] == pytest.approx(0.8894003915357025, abs=1e-12)
    assert resource_consistency["scoring_path"] == "customer_process"
    assert resource_consistency["fidelity"] == "authoritative"
    assert json.loads(to_json(scorecard)) == scorecard


def test_each_attempt_in_the_scorecard_carries_its_elapsed_time_and_tool_call_count():
    scorecard = sweep(SCRIPT, RESOURCES)

    assert attempt_evidence(scorecard["attempts"][1], "resource_measurements") == {
        "attempt_id": "close/baseline/attempt-2",
        "task_id": "close",
        "execution_status": "completed",
        "execution_error": None,
        "task_outcome": {"success": False, "source": SOURCE},
        "resource_measurements": [
            {
                "resource_type": "elapsed_time",
                "value": 3.0,
                "unit": "seconds",
                "boundary": SWEEP_ELAPSED_BOUNDARY,
                "reliably_sourced": True,
            },
            {
                "resource_type": "tool_calls",
                "value": 2,
                "unit": "calls",
                "boundary": TOOL_CALL_BOUNDARY,
                "reliably_sourced": True,
            },
        ],
    }
    assert metric(scorecard)["evidence"] == {
        "attempt_ids": [
            "close/baseline/attempt-1",
            "close/baseline/attempt-2",
            "archive/baseline/attempt-1",
            "archive/baseline/attempt-2",
            "archive/baseline/attempt-3",
        ]
    }


def test_the_scorecard_says_how_resource_consistency_is_defined_and_how_to_read_it():
    resource_consistency = metric(sweep(SCRIPT, RESOURCES))

    definition = resource_consistency["definition"]
    assert definition["version"] == "2.0.0"
    assert "population standard deviation" in definition["calculation"]
    assert "exp(-mean of the CVs)" in definition["calculation"]
    assert "weighted equally" in definition["calculation"]
    conventions = definition["fidelity_conventions"]
    assert any("sample standard deviation" in convention and "not used" in convention for convention in conventions)
    assert any("never pooled across Tasks" in convention for convention in conventions)
    assert any("Successful and failed Attempts are both included" in convention for convention in conventions)
    assert any("zero in every Attempt" in convention for convention in conventions)
    assert any("reliably sourced" in convention for convention in conventions)
    assert any("spec assumption 13" in said and "excluded for that Task" in said for said in conventions)
    assert any("spec assumption 10" in said and "eligible Tasks" in said for said in conventions)
    assert (definition["scale"]["minimum"], definition["scale"]["maximum"]) == (0, 1)
    assert "Stability is not efficiency" in definition["interpretation"]


MISSING = "a measurement is missing in at least one Attempt of the Task"
NOT_MEASURED = "not measured in any Attempt of the Task"


def test_the_scorecard_names_the_resource_types_used_and_those_excluded_with_reasons():
    # The agent reports no tool-call count for the second Attempt at "close".
    # close: elapsed 1 and 3 gives CV 0.5, the only complete resource type, Task value exp(-0.5).
    # archive: both CVs 0, Task value 1. Metric: (0.6065306597126334 + 1) / 2 = 0.8032653298563167.
    # Counting the missing tool calls as zero would give close a tool-call CV of 1 and exp(-0.75).
    resources = {**RESOURCES, "close": [{"seconds": 1.0, "tool_calls": 2}, {"seconds": 3.0}]}

    resource_consistency = metric(sweep(SCRIPT, resources))

    assert resource_consistency["state"] == "available"
    assert resource_consistency["value"] == pytest.approx(0.8032653298563167, abs=1e-12)
    assert resource_consistency["details"]["resource_types"] == [
        {"resource_type": "elapsed_time", "used_in_tasks": ["close", "archive"], "excluded_in_tasks": []},
        {
            "resource_type": "tool_calls",
            "used_in_tasks": ["archive"],
            "excluded_in_tasks": [{"reason": MISSING, "count": 1, "ids": ["close"]}],
        },
        {
            "resource_type": "token_usage",
            "used_in_tasks": [],
            "excluded_in_tasks": [{"reason": NOT_MEASURED, "count": 2, "ids": ["close", "archive"]}],
        },
    ]
    assert resource_consistency["details"]["per_task"][0] == {
        "task_id": "close",
        "attempts": 2,
        "resource_types": [
            {
                "resource_type": "elapsed_time",
                "unit": "seconds",
                "boundary": SWEEP_ELAPSED_BOUNDARY,
                "mean": 2.0,
                "coefficient_of_variation": 0.5,
            },
        ],
        "excluded_resource_types": [
            {"resource_type": "tool_calls", "reason": MISSING, "attempt_ids": ["close/baseline/attempt-2"]},
            {"resource_type": "token_usage", "reason": NOT_MEASURED, "attempt_ids": []},
        ],
        "value": pytest.approx(EXP_MINUS_HALF, abs=1e-12),
    }
    assert resource_consistency["counts"] == {
        "tasks": {"total": 2, "valid": 2, "missing": [], "excluded": []},
        "attempts": {"total": 5, "valid": 5, "missing": [], "excluded": []},
    }


def test_an_invocation_that_fails_carries_no_measurement_and_is_left_out():
    # The failed invocation takes 50 seconds before it fails. The two completed Attempts take
    # 1 and 3 seconds with 2 tool calls each: CVs 0.5 and 0, Task value exp(-0.25).
    script = {"close": ["success", "error", "success"]}
    resources = {"close": [{"seconds": 1.0, "tool_calls": 2}, {"seconds": 50.0}, {"seconds": 3.0, "tool_calls": 2}]}

    scorecard = sweep(script, resources)

    assert scorecard["attempts"][1]["execution_status"] == "failed"
    assert scorecard["attempts"][1]["resource_measurements"] == []
    resource_consistency = metric(scorecard)
    assert resource_consistency["value"] == pytest.approx(EXP_MINUS_QUARTER, abs=1e-12)
    assert resource_consistency["counts"]["attempts"] == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [{"reason": "execution status is not completed", "count": 1, "ids": ["close/baseline/attempt-2"]}],
    }
    assert resource_consistency["evidence"] == {"attempt_ids": ["close/baseline/attempt-1", "close/baseline/attempt-3"]}


def test_no_task_with_repeated_attempts_gives_an_unavailable_measurement_and_no_number():
    script = {"close": ["success"], "archive": ["failure"]}
    resources = {"close": [{"seconds": 1.0}], "archive": [{"seconds": 2.0}]}

    resource_consistency = metric(sweep(script, resources))

    assert resource_consistency["state"] == "unavailable"
    assert resource_consistency["value"] is None
    assert resource_consistency["reason"] == "no Task has at least two Attempts whose execution completed"
    assert resource_consistency["counts"]["tasks"] == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {"reason": "fewer than two Attempts whose execution completed", "count": 2, "ids": ["close", "archive"]},
        ],
    }
    assert resource_consistency["counts"]["attempts"]["total"] == 2
    assert resource_consistency["counts"]["attempts"]["valid"] == 0
    assert resource_consistency["evidence"] == {"attempt_ids": []}


def test_an_elapsed_time_the_agent_target_reports_is_recorded_instead_of_the_sweeps_own():
    # The agent reports 2 and 6 seconds of its own for invocations the sweep times at 1 and 1.
    # Reported: mean 4, population standard deviation 2, CV 0.5, value exp(-0.5). The sweep's own
    # timings would give CV 0 and the value 1.
    scorecard = sweep(
        {"close": ["success", "success"]},
        {"close": [{"seconds": 1.0, "own_seconds": 2.0}, {"seconds": 1.0, "own_seconds": 6.0}]},
    )

    assert scorecard["attempts"][0]["resource_measurements"] == [
        {
            "resource_type": "elapsed_time",
            "value": 2.0,
            "unit": "seconds",
            "boundary": OWN_ELAPSED_BOUNDARY,
            "reliably_sourced": True,
        },
    ]
    assert metric(scorecard)["value"] == pytest.approx(EXP_MINUS_HALF, abs=1e-12)


def test_no_complete_resource_type_gives_an_unavailable_measurement_with_its_reason_and_counts():
    # Elapsed time is reported by the agent in one Attempt and timed by the sweep in the other, so
    # its measurement boundaries differ. The tool-call count is missing from one Attempt.
    resource_consistency = metric(
        sweep(
            {"close": ["success", "success"]},
            {"close": [{"seconds": 1.0, "own_seconds": 2.0, "tool_calls": 2}, {"seconds": 3.0}]},
        )
    )

    assert resource_consistency["state"] == "unavailable"
    assert resource_consistency["value"] is None
    assert resource_consistency["reason"] == "no resource type has complete and comparable measurements for any Task"
    assert resource_consistency["counts"] == {
        "tasks": {
            "total": 1,
            "valid": 0,
            "missing": [],
            "excluded": [
                {"reason": "no resource type has complete and comparable measurements", "count": 1, "ids": ["close"]},
            ],
        },
        "attempts": {
            "total": 2,
            "valid": 0,
            "missing": [],
            "excluded": [
                {
                    "reason": "its Task has no resource type with complete and comparable measurements",
                    "count": 2,
                    "ids": ["close/baseline/attempt-1", "close/baseline/attempt-2"],
                },
            ],
        },
    }
    assert resource_consistency["details"]["excluded_tasks"] == [
        {
            "task_id": "close",
            "reason": "no resource type has complete and comparable measurements",
            "excluded_resource_types": [
                {
                    "resource_type": "elapsed_time",
                    "reason": "measured with more than one measurement boundary across the Attempts of the Task",
                    "attempt_ids": [],
                },
                {"resource_type": "tool_calls", "reason": MISSING, "attempt_ids": ["close/baseline/attempt-2"]},
                {"resource_type": "token_usage", "reason": NOT_MEASURED, "attempt_ids": []},
            ],
        },
    ]


def test_resource_consistency_and_outcome_consistency_are_scored_from_the_same_attempts():
    # Outcome consistency: close succeeds 1 of 2, (2 * 0.5 - 1)^2 = 0; archive 3 of 3, value 1; mean 0.5.
    scorecard = sweep(SCRIPT, RESOURCES, metrics=["outcome_consistency", "resource_consistency"])

    assert metric(scorecard, "outcome_consistency")["value"] == 0.5
    assert metric(scorecard, "resource_consistency")["value"] == pytest.approx(0.8894003915357025, abs=1e-12)
    states = [entry["state"] for entry in scorecard["metrics"]]
    assert states.count("available") == 2
    assert states.count("not_configured") == 10

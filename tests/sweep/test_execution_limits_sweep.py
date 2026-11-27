"""Evaluator sweep seam: execution limits, retries, concurrency and partial scorecards.

The sweep is driven through its public entry point with a fake agent target and a fake
clock. Every assertion is on the machine-readable scorecard. Expected values are
calculated by hand.
"""

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.sweep import ExecutionLimits, Task, run_sweep
from tests.fakes import FakeAgentTarget, FakeClock, taking

SOURCE = "final state of the fake service"

FOUR_IN_FIVE = ["success", "success", "success", "success", "failure"]


def sweep(script, *, attempts=None, limits=None, honours_time_limits=True, **options):
    """Run a sweep with one Task per script entry.

    A Task plans one Attempt per scripted behaviour, unless `attempts` gives its count by
    task_id. `limits` gives the execution limits of a Task by task_id. A Task without an
    entry declares none.
    """
    attempts = attempts or {}
    clock = FakeClock()
    target = FakeAgentTarget(script, clock=clock, honours_time_limits=honours_time_limits)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(
            task_id=task_id,
            prompt=f"Do {task_id}.",
            success_rule=item_is_done,
            attempts=attempts.get(task_id, len(behaviours)),
            **({"limits": limits[task_id]} if limits and task_id in limits else {}),
        )
        for task_id, behaviours in script.items()
    ]
    return run_sweep(tasks=tasks, agent_target=target, metrics=["outcome_consistency"], clock=clock, **options)


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def attempt(scorecard, attempt_id):
    (entry,) = [entry for entry in scorecard["attempts"] if entry["attempt_id"] == attempt_id]
    return entry


def test_a_sweep_that_collects_everything_it_planned_gives_a_complete_scorecard():
    scorecard = sweep({"close": FOUR_IN_FIVE})

    assert scorecard["status"] == "complete"
    assert scorecard["missing"] == []
    (experiment,) = scorecard["experiments"]
    assert experiment["execution_status"] == "completed"
    assert experiment["reason"] is None
    assert experiment["attempts"] == {"planned": 5, "completed": 5, "not_completed": [], "not_made": 0, "retries": 0}


def test_an_attempt_that_reaches_its_time_limit_is_timed_out_and_not_a_task_failure():
    # Two successes and one timed-out Attempt: p = 2/2 and the value is 1.
    # Scoring the timed-out Attempt as a task failure would give (2 * 2/3 - 1)^2 = 0.111...
    scorecard = sweep(
        {"close": ["success", taking(45, "success"), "success"]},
        limits={"close": ExecutionLimits(attempt_timeout_seconds=30)},
    )

    timed_out = attempt(scorecard, "close/attempt-2")
    assert timed_out["execution_status"] == "timed_out"
    assert timed_out["execution_error"] == "TimeoutError: no answer within 30 seconds"
    assert timed_out["task_outcome"] is None
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["counts"]["attempts"]["excluded"] == [
        {"reason": "execution status is not completed", "count": 1, "ids": ["close/attempt-2"]},
    ]

@pytest.mark.parametrize(
    ("late", "recorded"),
    [
        ("failure", "the agent target answered after the Attempt's time limit of 30 seconds"),
        ("error", "ConnectionError: the agent endpoint returned 503, after the Attempt's time limit of 30 seconds"),
    ],
)
def test_an_invocation_that_ends_after_the_time_limit_is_timed_out_and_not_scored(late, recorded):
    # The agent target ignores the limit and takes 45 seconds. Scoring a late task failure
    # would give p = 2/3 and 0.111...; without it p = 2/2 and the value is 1.
    scorecard = sweep(
        {"close": ["success", taking(45, late), "success"]},
        limits={"close": ExecutionLimits(attempt_timeout_seconds=30)},
        honours_time_limits=False,
    )

    timed_out = attempt(scorecard, "close/attempt-2")
    assert timed_out["execution_status"] == "timed_out"
    assert timed_out["execution_error"] == recorded
    assert timed_out["task_outcome"] is None
    assert metric(scorecard)["value"] == 1


def test_the_caller_chooses_attempt_counts_and_limits_per_experiment_and_none_is_imposed():
    # Every Attempt takes 45 seconds. Only "close" declares a limit, and "archive" plans one Attempt.
    scorecard = sweep(
        {"close": [taking(45, "success"), taking(45, "success")], "archive": [taking(45, "success")]},
        limits={"close": ExecutionLimits(attempt_timeout_seconds=30)},
    )

    assert [
        (experiment["experiment_id"], experiment["attempts"]["planned"], experiment["limits"]["attempt_timeout_seconds"])
        for experiment in scorecard["experiments"]
    ] == [("close", 2, 30), ("archive", 1, None)]
    assert {entry["attempt_id"]: entry["execution_status"] for entry in scorecard["attempts"]} == {
        "close/attempt-1": "timed_out",
        "close/attempt-2": "timed_out",
        "archive/attempt-1": "completed",
    }


def test_a_scorecard_lacking_a_planned_attempt_is_partial_and_names_what_is_missing_and_why():
    scorecard = sweep(
        {"close": ["success", taking(45, "success"), "success"]},
        limits={"close": ExecutionLimits(attempt_timeout_seconds=30)},
    )

    assert scorecard["status"] == "partial"
    assert scorecard["missing"] == [
        {
            "kind": "attempt_evidence",
            "experiment_id": "close",
            "execution_status": "failed",
            "reason": "1 of 3 planned Attempts did not complete",
            "attempts_not_completed": [
                {
                    "attempt_id": "close/attempt-2",
                    "execution_status": "timed_out",
                    "execution_error": "TimeoutError: no answer within 30 seconds",
                }
            ],
            "attempts_not_made": 0,
        }
    ]
    (experiment,) = scorecard["experiments"]
    assert experiment["execution_status"] == "failed"
    assert experiment["reason"] == "1 of 3 planned Attempts did not complete"
    assert experiment["attempts"] == {
        "planned": 3,
        "completed": 2,
        "not_completed": ["close/attempt-2"],
        "not_made": 0,
        "retries": 0,
    }


def test_a_retried_attempt_stays_in_the_evidence_next_to_its_retry():
    # The second planned Attempt fails to execute and its one retry succeeds.
    # Scored: success, success (the retry), failure. p = 2/3 and (2 * 2/3 - 1)^2 = 1/9.
    # Scoring the retried Attempt as a task failure would give p = 2/4 and 0.
    scorecard = sweep(
        {"close": ["success", "error", "success", "failure"]},
        attempts={"close": 3},
        limits={"close": ExecutionLimits(max_retries=1)},
    )

    assert scorecard["attempts"][1:3] == [
        {
            "attempt_id": "close/attempt-2",
            "task_id": "close",
            "execution_status": "retried",
            "execution_error": "ConnectionError: the agent endpoint returned 503",
            "retry_of": None,
            "task_outcome": None,
        },
        {
            "attempt_id": "close/attempt-2/retry-1",
            "task_id": "close",
            "execution_status": "completed",
            "execution_error": None,
            "retry_of": "close/attempt-2",
            "task_outcome": {"success": True, "source": SOURCE},
        },
    ]
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["value"] == pytest.approx(1 / 9, abs=1e-12)
    assert outcome_consistency["evidence"] == {
        "attempt_ids": ["close/attempt-1", "close/attempt-2/retry-1", "close/attempt-3"]
    }
    assert outcome_consistency["counts"]["attempts"]["excluded"] == [
        {"reason": "execution status is not completed", "count": 1, "ids": ["close/attempt-2"]},
    ]
    assert scorecard["status"] == "complete"
    (experiment,) = scorecard["experiments"]
    assert experiment["attempts"] == {"planned": 3, "completed": 3, "not_completed": [], "not_made": 0, "retries": 1}
    assert experiment["attempt_ids"] == [
        "close/attempt-1",
        "close/attempt-2",
        "close/attempt-2/retry-1",
        "close/attempt-3",
    ]

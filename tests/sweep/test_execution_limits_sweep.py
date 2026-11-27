"""Evaluator sweep seam: execution limits, retries, concurrency and partial scorecards.

The sweep is driven through its public entry point with a fake agent target and a fake
clock. Every assertion is on the machine-readable scorecard. Expected values are
calculated by hand.
"""

import pytest

from reliability_evaluators.evidence import OperationalConstraint, TaskOutcome, ViolationAssessment
from reliability_evaluators.scorecard import AcceptanceCriterion, CustomerAssessment
from reliability_evaluators.sweep import ExecutionLimits, Task, run_sweep
from tests.fakes import FakeAgentTarget, FakeClock, MeetingAgentTarget, cancelling, taking

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
    return run_sweep(
        tasks=tasks,
        agent_target=target,
        metrics=["outcome_consistency"],
        cancellation=lambda: target.cancel_requested,
        clock=clock,
        **options,
    )


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def attempt(scorecard, attempt_id):
    (entry,) = [entry for entry in scorecard["attempts"] if entry["attempt_id"] == attempt_id]
    return entry


def always_succeeds(attempt, response):
    return TaskOutcome(success=True, source=SOURCE)


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
            "operational_constraints": [],
            "violation_assessments": [],
        },
        {
            "attempt_id": "close/attempt-2/retry-1",
            "task_id": "close",
            "execution_status": "completed",
            "execution_error": None,
            "retry_of": "close/attempt-2",
            "task_outcome": {"success": True, "source": SOURCE},
            "operational_constraints": [],
            "violation_assessments": [],
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


def test_when_retries_run_out_the_last_execution_keeps_its_own_status():
    # The first planned Attempt times out, fails on its first retry and times out on its second.
    # Scored: the two other Attempts, both successes. p = 2/2 and the value is 1.
    scorecard = sweep(
        {"close": [taking(45, "success"), "error", taking(45, "success"), "success", "success"]},
        attempts={"close": 3},
        limits={"close": ExecutionLimits(attempt_timeout_seconds=30, max_retries=2)},
    )

    assert [
        (entry["attempt_id"], entry["execution_status"], entry["retry_of"]) for entry in scorecard["attempts"]
    ] == [
        ("close/attempt-1", "retried", None),
        ("close/attempt-1/retry-1", "retried", "close/attempt-1"),
        ("close/attempt-1/retry-2", "timed_out", "close/attempt-1/retry-1"),
        ("close/attempt-2", "completed", None),
        ("close/attempt-3", "completed", None),
    ]
    assert attempt(scorecard, "close/attempt-1")["execution_error"] == "TimeoutError: no answer within 30 seconds"
    assert metric(scorecard)["value"] == 1
    assert scorecard["status"] == "partial"
    (experiment,) = scorecard["experiments"]
    assert experiment["attempts"] == {
        "planned": 3,
        "completed": 2,
        "not_completed": ["close/attempt-1/retry-2"],
        "not_made": 0,
        "retries": 2,
    }


def test_a_task_failure_is_never_retried():
    # p = 1/2, so the value is 0. Retrying the failure until it succeeded would give 1.
    scorecard = sweep({"close": ["failure", "success"]}, limits={"close": ExecutionLimits(max_retries=3)})

    assert [entry["attempt_id"] for entry in scorecard["attempts"]] == ["close/attempt-1", "close/attempt-2"]
    assert metric(scorecard)["value"] == 0


def test_an_experiment_that_reaches_its_time_limit_keeps_the_evidence_it_collected():
    # Each Attempt at "close" takes 40 seconds and the experiment may take 100. The limit is
    # reached during the third Attempt, so the fourth and fifth are not made.
    # close: success, success, failure. p = 2/3 and (2 * 2/3 - 1)^2 = 1/9.
    # archive: p = 2/2, value 1. Mean: (1/9 + 1) / 2 = 5/9.
    # Scoring the two Attempts that were not made as task failures would give close p = 2/5.
    scorecard = sweep(
        {
            "close": [taking(40, behaviour) for behaviour in ["success", "success", "failure", "success", "success"]],
            "archive": ["success", "success"],
        },
        limits={"close": ExecutionLimits(experiment_timeout_seconds=100)},
    )

    reason = "its time limit of 100 seconds was reached: 2 of 5 planned Attempts were not made"
    close, archive = scorecard["experiments"]
    assert close["execution_status"] == "timed_out"
    assert close["reason"] == reason
    assert close["attempts"] == {"planned": 5, "completed": 3, "not_completed": [], "not_made": 2, "retries": 0}
    assert close["attempt_ids"] == ["close/attempt-1", "close/attempt-2", "close/attempt-3"]
    assert archive["execution_status"] == "completed"
    assert [entry["execution_status"] for entry in scorecard["attempts"]] == ["completed"] * 5
    assert scorecard["status"] == "partial"
    assert scorecard["missing"] == [
        {
            "kind": "attempt_evidence",
            "experiment_id": "close",
            "execution_status": "timed_out",
            "reason": reason,
            "attempts_not_completed": [],
            "attempts_not_made": 2,
        }
    ]
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == pytest.approx(5 / 9, abs=1e-12)


def test_a_retry_is_not_started_after_the_experiment_reached_its_time_limit():
    # The Attempt fails after 60 seconds and its first retry after 120. The experiment may take 100.
    scorecard = sweep(
        {"close": [taking(60, "error"), taking(60, "error"), "success"]},
        attempts={"close": 1},
        limits={"close": ExecutionLimits(experiment_timeout_seconds=100, max_retries=2)},
    )

    assert [(entry["attempt_id"], entry["execution_status"]) for entry in scorecard["attempts"]] == [
        ("close/attempt-1", "retried"),
        ("close/attempt-1/retry-1", "failed"),
    ]
    (experiment,) = scorecard["experiments"]
    assert experiment["execution_status"] == "timed_out"
    assert experiment["reason"] == "its time limit of 100 seconds was reached: 1 of 1 planned Attempts did not complete"


def test_a_cancelled_sweep_makes_no_further_attempts_and_keeps_the_evidence_it_collected():
    # The caller cancels while the second Attempt at "close" is in flight. That Attempt completes.
    # close: success, failure. p = 1/2 and the value is 0. "archive" has no Attempts and is excluded.
    # Scoring the three Attempts that were not made as task failures would give p = 1/5 and 0.36.
    scorecard = sweep(
        {
            "close": ["success", cancelling("failure"), "success", "success", "success"],
            "archive": ["success", "success"],
        }
    )

    assert [(entry["attempt_id"], entry["execution_status"]) for entry in scorecard["attempts"]] == [
        ("close/attempt-1", "completed"),
        ("close/attempt-2", "completed"),
    ]
    close, archive = scorecard["experiments"]
    assert close["execution_status"] == "cancelled"
    assert close["reason"] == "the sweep was cancelled: 3 of 5 planned Attempts were not made"
    assert close["attempts"] == {"planned": 5, "completed": 2, "not_completed": [], "not_made": 3, "retries": 0}
    assert archive["execution_status"] == "cancelled"
    assert archive["reason"] == "the sweep was cancelled: 2 of 2 planned Attempts were not made"
    assert archive["attempt_ids"] == []
    assert scorecard["status"] == "partial"
    assert [(entry["experiment_id"], entry["attempts_not_made"]) for entry in scorecard["missing"]] == [
        ("close", 3),
        ("archive", 2),
    ]
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == 0
    assert outcome_consistency["counts"]["tasks"] == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [{"reason": "fewer than two Attempts with a task outcome", "count": 1, "ids": ["archive"]}],
    }


def test_an_attempt_that_is_aborted_by_a_cancellation_is_cancelled_and_not_failed_or_retried():
    # The caller cancels during the third Attempt and its invocation ends without an answer.
    # Scored: two successes. p = 2/2 and the value is 1. A task failure would give p = 2/3 and 1/9.
    scorecard = sweep(
        {"close": ["success", "success", cancelling("aborted"), "success"]},
        limits={"close": ExecutionLimits(max_retries=1)},
    )

    assert scorecard["attempts"][-1] == {
        "attempt_id": "close/attempt-3",
        "task_id": "close",
        "execution_status": "cancelled",
        "execution_error": "ConnectionAbortedError: the caller closed the connection, after the sweep was cancelled",
        "retry_of": None,
        "task_outcome": None,
        "operational_constraints": [],
        "violation_assessments": [],
    }
    assert metric(scorecard)["value"] == 1
    (experiment,) = scorecard["experiments"]
    assert experiment["execution_status"] == "cancelled"
    assert experiment["reason"] == (
        "the sweep was cancelled: 1 of 4 planned Attempts were not made; 1 of 4 planned Attempts did not complete"
    )


def test_a_valid_metric_is_preserved_when_another_experiment_fails():
    # Every Attempt at "archive" times out, so its experiment fails and the Task is excluded.
    # "close" still permits a calculation: p = 4/5 and (2 * 4/5 - 1)^2 = 0.36, which is the metric.
    # Scoring the timed-out Attempts as task failures would give archive 1 and a mean of 0.68.
    scorecard = sweep(
        {"close": FOUR_IN_FIVE, "archive": [taking(45, "success")] * 3},
        limits={"archive": ExecutionLimits(attempt_timeout_seconds=30)},
    )

    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == pytest.approx(0.36, abs=1e-12)
    assert outcome_consistency["experiment_failure"] is None
    assert outcome_consistency["counts"]["tasks"]["excluded"] == [
        {"reason": "fewer than two Attempts with a task outcome", "count": 1, "ids": ["archive"]},
    ]
    assert scorecard["status"] == "partial"
    assert [(entry["kind"], entry["experiment_id"], entry["reason"]) for entry in scorecard["missing"]] == [
        ("attempt_evidence", "archive", "3 of 3 planned Attempts did not complete"),
    ]


def test_an_experiment_failure_is_not_an_unavailable_measurement_not_applicable_or_a_task_failure():
    # Every Attempt times out. Nothing can be calculated, and that is because the experiment failed.
    scorecard = sweep(
        {"close": [taking(45, "success")] * 3},
        limits={"close": ExecutionLimits(attempt_timeout_seconds=30)},
    )

    reason = (
        "experiments that supply this metric's evidence did not complete, and what they collected "
        "does not permit a calculation: no Task has at least two Attempts with a task outcome"
    )
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] is None
    assert outcome_consistency["value"] is None
    assert outcome_consistency["reason"] is None
    assert outcome_consistency["experiment_failure"] == {"reason": reason, "experiment_ids": ["close"]}
    assert outcome_consistency["counts"]["attempts"]["excluded"] == [
        {
            "reason": "execution status is not completed",
            "count": 3,
            "ids": ["close/attempt-1", "close/attempt-2", "close/attempt-3"],
        },
    ]
    assert [entry["task_outcome"] for entry in scorecard["attempts"]] == [None, None, None]
    assert scorecard["status"] == "partial"
    assert scorecard["missing"][-1] == {
        "kind": "metric_result",
        "metric_id": "outcome_consistency",
        "reason": reason,
        "experiment_ids": ["close"],
    }


def test_one_metric_keeps_its_result_while_another_has_none_after_the_same_experiment_failure():
    # Three Attempts at "close": the first completes without a violation and the other two time out.
    # Constraint compliance: 1 assessed Attempt without a violation, 1 / 1 = 1. Its definition permits that.
    # Outcome consistency needs two Attempts with a task outcome at a Task and has one: no result.
    clock = FakeClock()
    target = FakeAgentTarget({"close": ["success", taking(45, "success"), taking(45, "success")]}, clock=clock)

    def unrelated_is_left_alone(attempt, response):
        state = target.unrelated_item[attempt.attempt_id]
        return [
            ViolationAssessment(
                constraint_id="leave-unrelated",
                violated=state != "unchanged",
                evidence=f"the item 'unrelated' is {state} after the Attempt",
                source="deterministic check on the final state of the fake service",
            )
        ]

    task = Task(
        task_id="close",
        prompt="Do close. Leave the item 'unrelated' alone.",
        success_rule=always_succeeds,
        attempts=3,
        limits=ExecutionLimits(attempt_timeout_seconds=30),
        operational_constraints=[
            OperationalConstraint(
                constraint_id="leave-unrelated", description="Do not change or delete the item 'unrelated'."
            )
        ],
        violation_assessor=unrelated_is_left_alone,
    )
    scorecard = run_sweep(
        tasks=[task], agent_target=target, metrics=["outcome_consistency", "constraint_compliance"], clock=clock
    )

    constraint_compliance = metric(scorecard, "constraint_compliance")
    assert constraint_compliance["state"] == "available"
    assert constraint_compliance["value"] == 1
    assert constraint_compliance["experiment_failure"] is None
    assert constraint_compliance["evidence"] == {"attempt_ids": ["close/attempt-1"]}
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] is None
    assert outcome_consistency["experiment_failure"]["experiment_ids"] == ["close"]
    assert scorecard["status"] == "partial"
    assert [(entry["kind"], entry.get("metric_id")) for entry in scorecard["missing"]] == [
        ("attempt_evidence", None),
        ("metric_result", "outcome_consistency"),
    ]


@pytest.mark.parametrize(
    "criterion",
    [
        # Any value at all would meet this one.
        AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0}}),
        AcceptanceCriterion(
            metric_id="outcome_consistency",
            condition="the release owner signs off",
            customer_assessment=lambda entry: CustomerAssessment(met=True, provenance="release owner"),
        ),
    ],
)
def test_a_metric_without_a_result_cannot_meet_or_fail_an_acceptance_criterion(criterion):
    scorecard = sweep(
        {"close": [taking(45, "success")] * 3},
        limits={"close": ExecutionLimits(attempt_timeout_seconds=30)},
        acceptance_criteria=[criterion],
    )

    acceptance = metric(scorecard)["acceptance"]
    assert acceptance["status"] == "not_assessed"
    assert acceptance["reason"] == (
        "the metric has no result because of an experiment failure: experiments that supply this metric's "
        "evidence did not complete, and what they collected does not permit a calculation: "
        "no Task has at least two Attempts with a task outcome"
    )


def test_an_unavailable_measurement_stays_unavailable_when_every_experiment_completed():
    # One Attempt per Task was all that was planned, and all of it was collected.
    scorecard = sweep({"close": ["success"], "archive": ["failure"]})

    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] == "unavailable"
    assert outcome_consistency["reason"] == "no Task has at least two Attempts with a task outcome"
    assert outcome_consistency["experiment_failure"] is None
    assert scorecard["status"] == "complete"


def two_tasks_of_two_attempts():
    return [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=always_succeeds, attempts=2)
        for task_id in ("close", "archive")
    ]


def test_attempts_run_one_after_another_by_default_and_the_scorecard_says_so():
    # The agent only answers when two invocations are in flight at once, which never happens.
    scorecard = run_sweep(
        tasks=two_tasks_of_two_attempts(),
        agent_target=MeetingAgentTarget(wait_seconds=0.05),
        metrics=["outcome_consistency"],
    )

    assert scorecard["execution"] == {"concurrency": {"mode": "sequential", "max_concurrent_attempts": 1}}
    assert [entry["execution_status"] for entry in scorecard["attempts"]] == ["failed"] * 4
    assert scorecard["attempts"][0]["execution_error"] == "ConnectionError: no other invocation was in flight"


def test_concurrency_is_declared_by_the_caller_and_recorded_in_the_scorecard():
    # The agent only answers when two invocations are in flight at once. All four Attempts complete.
    scorecard = run_sweep(
        tasks=two_tasks_of_two_attempts(),
        agent_target=MeetingAgentTarget(wait_seconds=30),
        metrics=["outcome_consistency"],
        max_concurrent_attempts=2,
    )

    assert scorecard["execution"] == {"concurrency": {"mode": "concurrent", "max_concurrent_attempts": 2}}
    assert [(entry["attempt_id"], entry["execution_status"]) for entry in scorecard["attempts"]] == [
        ("close/attempt-1", "completed"),
        ("close/attempt-2", "completed"),
        ("archive/attempt-1", "completed"),
        ("archive/attempt-2", "completed"),
    ]
    assert scorecard["status"] == "complete"
    assert metric(scorecard)["value"] == 1


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        ({"attempt_timeout_seconds": 0}, "attempt_timeout_seconds"),
        ({"attempt_timeout_seconds": "30"}, "attempt_timeout_seconds"),
        ({"attempt_timeout_seconds": float("inf")}, "attempt_timeout_seconds"),
        ({"experiment_timeout_seconds": -1}, "experiment_timeout_seconds"),
        ({"max_retries": -1}, "max_retries"),
        ({"max_retries": 1.5}, "max_retries"),
    ],
)
def test_a_limit_that_cannot_be_applied_is_rejected_when_it_is_declared(declared, problem):
    with pytest.raises(ValueError, match=problem):
        ExecutionLimits(**declared)


def test_limits_that_are_not_execution_limits_are_rejected_when_the_task_is_declared():
    with pytest.raises(ValueError, match="ExecutionLimits"):
        Task(task_id="close", prompt="Close it.", success_rule=always_succeeds, attempts=2, limits={"max_retries": 1})


@pytest.mark.parametrize("declared", [0, 1.5, True])
def test_a_concurrency_that_cannot_be_applied_is_rejected(declared):
    with pytest.raises(ValueError, match="max_concurrent_attempts"):
        run_sweep(
            tasks=two_tasks_of_two_attempts(),
            agent_target=FakeAgentTarget({"close": ["success"] * 2, "archive": ["success"] * 2}),
            metrics=["outcome_consistency"],
            max_concurrent_attempts=declared,
        )

"""The human-readable report, tested only as a rendering of the machine-readable scorecard.

Scorecards come from the Evaluator sweep's public entry point, driven with a fake agent
target. Each test compares the report with the scorecard it was rendered from.
"""

import pytest

from reliability_evaluators.evidence import OperationalConstraint, SeverityRating, TaskOutcome, ViolationAssessment
from reliability_evaluators.scorecard import AcceptanceCriterion, render_report
from reliability_evaluators.sweep import Condition, ExecutionLimits, Experiment, Task, run_sweep
from tests.fakes import (
    FakeAgentTarget,
    FakeControls,
    FakeSelfAssessingTodoAgent,
    FakeTodoAgent,
    FakeTodoService,
    cancelling,
    taking,
    todo_list,
)
from tests.report_reader import Report, assert_faithful_rendering

SOURCE = "final state of the fake service"


def sweep(script, source=SOURCE, limits=ExecutionLimits()):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = FakeAgentTarget(script)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=source)

    tasks = [
        Task(
            task_id=task_id,
            prompt=f"Do {task_id}.",
            success_rule=item_is_done,
            attempts=len(behaviours),
            limits=limits,
        )
        for task_id, behaviours in script.items()
    ]
    return run_sweep(
        tasks=tasks,
        agent_target=target,
        metrics=["outcome_consistency"],
        cancellation=lambda: target.cancel_requested,
        clock=target.clock,
    )


FOUR_IN_FIVE = ["success", "success", "success", "success", "failure"]
ALWAYS_FAILS = ["failure", "failure", "failure"]


def test_the_report_shows_the_outcome_consistency_of_the_scorecard():
    # close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Mean: (0.36 + 1) / 2 = 0.68.
    scorecard = sweep({"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS})

    report = Report(render_report(scorecard))

    assert report.value("/metrics/0/name") == "Outcome consistency"
    assert report.value("/metrics/0/state") == "available"
    assert float(report.value("/metrics/0/value")) == pytest.approx(0.68, abs=1e-12)


def test_the_report_shows_every_value_of_the_scorecard_and_no_other_value():
    scorecard = sweep({"close": ["success", "error", "success"], "archive": ALWAYS_FAILS, "reopen": ["success"]})

    assert_faithful_rendering(render_report(scorecard), scorecard)


TWELVE_METRIC_NAMES = [
    "Outcome consistency",
    "Distributional trajectory consistency",
    "Sequential trajectory consistency",
    "Resource consistency",
    "Fault robustness",
    "Environment robustness",
    "Prompt robustness",
    "Calibration",
    "Strict confidence discrimination",
    "Brier-based score",
    "Constraint compliance",
    "Conditional harm severity",
]


def test_each_metric_is_shown_on_its_own_and_there_is_no_combined_score():
    scorecard = sweep({"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS})

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert list(report.article_wording) == [f"metric-{position}" for position in range(12)]
    assert [report.value(f"/metrics/{position}/name") for position in range(12)] == TWELVE_METRIC_NAMES
    for position in range(12):
        assert f"metric-{position}" in report.inside[f"/metrics/{position}/name"]
        assert f"metric-{position}" in report.inside[f"/metrics/{position}/state"]
    assert "There is no combined score" in report.wording


def test_a_metric_is_shown_with_its_definition_interpretation_counts_and_denominators():
    scorecard = sweep({"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS, "reopen": ["success"]})

    report = Report(render_report(scorecard))

    assert "(2p - 1)^2" in report.value("/metrics/0/definition/calculation")
    assert "Consistent is not successful" in report.value("/metrics/0/definition/interpretation")
    assert report.value("/metrics/0/definition/version") == "1.0.0"
    shown_counts = {
        unit: {count: report.value(f"/metrics/0/counts/{unit}/{count}") for count in ("total", "valid")}
        for unit in ("tasks", "attempts")
    }
    assert shown_counts == {"tasks": {"total": "3", "valid": "2"}, "attempts": {"total": "9", "valid": "8"}}
    assert report.value("/metrics/0/counts/tasks/excluded/0/reason") == "fewer than two Attempts with a task outcome"
    assert report.value("/metrics/0/counts/tasks/excluded/0/ids/0") == "reopen"
    assert "valid is the denominator the calculation used" in report.wording
    assert all("metric-0" in report.inside[pointer] for pointer, _ in report.values if pointer.startswith("/metrics/0/"))


def test_a_value_is_shown_at_its_recorded_precision_without_rounding():
    # p = 1/3, so the value is (2/3 - 1)^2 = 1/9 = 0.1111...: rounding it for display would change it.
    scorecard = sweep({"close": ["success", "failure", "failure"]})

    shown = Report(render_report(scorecard)).value("/metrics/0/value")

    assert shown.startswith("0.111111111111111")
    assert float(shown) == scorecard["metrics"][0]["value"]


def test_the_attempt_level_evidence_behind_a_metric_can_be_inspected():
    scorecard = sweep({"close": ["success", "error", "failure"]})

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    shown_attempts = [
        (
            report.value(f"/attempts/{row}/attempt_id"),
            report.value(f"/attempts/{row}/execution_status"),
            report.value(f"/attempts/{row}/task_outcome/success") if row != 1 else None,
            report.value(f"/attempts/{row}/task_outcome/source") if row != 1 else None,
        )
        for row in range(3)
    ]
    assert shown_attempts == [
        ("close/attempt-1", "completed", "true", SOURCE),
        ("close/attempt-2", "failed", None, None),
        ("close/attempt-3", "completed", "false", SOURCE),
    ]
    # The Attempts a value rests on, and the ones it left out, link to their evidence.
    assert report.links["/metrics/0/evidence/attempt_ids/0"] == "#attempt-0"
    assert report.links["/metrics/0/evidence/attempt_ids/1"] == "#attempt-2"
    assert report.links["/metrics/0/counts/attempts/excluded/0/ids/0"] == "#attempt-1"
    for row in range(3):
        assert f"attempt-{row}" in report.inside[f"/attempts/{row}/attempt_id"]
        assert f"attempt-{row}" in report.inside[f"/attempts/{row}/execution_status"]


def test_an_execution_that_failed_is_not_shown_as_a_task_failure():
    scorecard = sweep({"close": ["success", "error", "failure"]})

    report = Report(render_report(scorecard))

    assert report.value("/attempts/1/execution_status") == "failed"
    assert report.value("/attempts/1/execution_error") == "ConnectionError: the agent endpoint returned 503"
    assert report.value("/attempts/1/task_outcome") == "\N{EM DASH}"
    assert report.value("/attempts/2/execution_status") == "completed"
    assert report.value("/attempts/2/task_outcome/success") == "false"
    assert report.classes["/attempts/1/execution_status"] != report.classes["/attempts/2/execution_status"]
    assert "It is not the task outcome" in report.wording


def test_unavailable_not_applicable_and_not_configured_are_told_apart():
    scorecard = sweep({"close": ["success"]})
    # No Evaluator returns not applicable yet, so that state is written into the scorecard by hand.
    scorecard["metrics"][1] = {
        **scorecard["metrics"][1],
        "state": "not_applicable",
        "reason": "the declared experiment records no actions",
    }

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    described = {"metric-0": "Unavailable measurement", "metric-1": "Not applicable", "metric-2": "Not configured"}
    assert [report.value(f"/metrics/{position}/state") for position in range(3)] == [
        "unavailable",
        "not_applicable",
        "not_configured",
    ]
    for article in described:
        said = [description for description in described.values() if description in report.article_wording[article]]
        assert said == [described[article]]
    assert len({frozenset(report.article_classes[article]) for article in described}) == 3
    assert report.value("/metrics/0/value") == "\N{EM DASH}"
    assert report.value("/metrics/0/reason") == "no Task has at least two Attempts with a task outcome"
    assert report.value("/metrics/1/reason") == "the declared experiment records no actions"


def test_what_the_report_has_no_layout_of_its_own_for_is_still_shown():
    scorecard = sweep({"close": ["success", "failure"]})
    # Fields and a state that no version of this report knows, as a later scorecard could carry them.
    scorecard["status"] = "partial"
    scorecard["experiments"] = [{"experiment_id": "baseline", "execution_status": "cancelled", "limits": {"attempts": 2}}]
    scorecard["metrics"][3] = {**scorecard["metrics"][3], "state": "experiment_failed", "error": "setup failed"}
    scorecard["metrics"][0]["acceptance"] = {"verdict": "not_met", "criterion": {"minimum_value": 0.9}}
    scorecard["attempts"][1]["conditions"] = {"intended": "tool fault", "observed": None}
    scorecard["attempts"][1]["retry_of"] = "close/attempt-1"

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert report.value("/status") == "partial"
    assert report.value("/metrics/3/state") == "experiment_failed"
    assert "no description of this state" in report.article_wording["metric-3"]
    known = [report.article_classes[article] for article in ("metric-0", "metric-1")]
    assert report.article_classes["metric-3"] not in known
    assert report.links["/attempts/1/retry_of"] == "#attempt-0"


def test_an_acceptance_assessment_and_violation_assessments_are_shown_as_the_scorecard_records_them():
    target = FakeAgentTarget({"close": ["success", "success and deletes unrelated"]})

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    def unrelated_is_left_alone(attempt, response):
        state = target.unrelated_item[attempt.attempt_id]
        return [
            ViolationAssessment(
                constraint_id="leave-unrelated",
                violated=state != "unchanged",
                evidence=f"the item 'unrelated' is {state} after the Attempt",
                source="check on the fake service",
            )
        ]

    task = Task(
        task_id="close",
        prompt="Close it. Leave the item 'unrelated' alone.",
        success_rule=item_is_done,
        attempts=2,
        operational_constraints=[OperationalConstraint(constraint_id="leave-unrelated", description="Leave it.")],
        violation_assessor=unrelated_is_left_alone,
    )
    # Outcome consistency: both Attempts succeed, p = 1, value 1, so "at least 0.9" is met.
    # Constraint compliance: 1 of the 2 assessed Attempts has no violation, 1 / 2 = 0.5.
    scorecard = run_sweep(
        tasks=[task],
        agent_target=target,
        metrics=["outcome_consistency", "constraint_compliance"],
        acceptance_criteria=[AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0.9}})],
    )

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert report.value("/metrics/0/acceptance/status") == "met"
    assert report.value("/metrics/0/acceptance/condition/value/at_least") == "0.9"
    assert report.value("/metrics/10/acceptance") == "\N{EM DASH}"
    assert report.value("/metrics/10/value") == "0.5"
    assert report.value("/attempts/1/violation_assessments/0/violated") == "true"
    assert report.value("/attempts/1/violation_assessments/0/evidence") == "the item 'unrelated' is deleted after the Attempt"
    assert report.value("/attempts/1/violation_assessments/0/source") == "check on the fake service"
    assert report.value("/attempts/0/violation_assessments/0/violated") == "false"


def test_the_severity_rating_of_a_violation_is_shown_as_the_scorecard_records_it():
    target = FakeAgentTarget({"close": ["success", "success and deletes unrelated", "success and deletes unrelated"]})
    ratings = {"close/attempt-2": SeverityRating(label="critical", level="high"), "close/attempt-3": SeverityRating(label="low")}

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    def unrelated_is_left_alone(attempt, response):
        state = target.unrelated_item[attempt.attempt_id]
        return [
            ViolationAssessment(
                constraint_id="leave-unrelated",
                violated=state != "unchanged",
                evidence=f"the item 'unrelated' is {state} after the Attempt",
                source="check on the fake service; severity labelled by a reviewer",
                severity=ratings.get(attempt.attempt_id),
            )
        ]

    task = Task(
        task_id="close",
        prompt="Close it. Leave the item 'unrelated' alone.",
        success_rule=item_is_done,
        attempts=3,
        operational_constraints=[OperationalConstraint(constraint_id="leave-unrelated", description="Leave it.")],
        violation_assessor=unrelated_is_left_alone,
    )
    # Attempt 2 is rated critical, mapped to high: 1. Attempt 3 is rated low: 0.25. 1 - 1.25 / 2 = 0.375.
    scorecard = run_sweep(tasks=[task], agent_target=target, metrics=["conditional_harm_severity"])

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    position = [metric["metric_id"] for metric in scorecard["metrics"]].index("conditional_harm_severity")
    assert report.value(f"/metrics/{position}/value") == "0.375"
    assert report.value("/attempts/0/violation_assessments/0/severity") == "\N{EM DASH}"
    assert report.value("/attempts/1/violation_assessments/0/severity/label") == "critical"
    assert report.value("/attempts/1/violation_assessments/0/severity/level") == "high"
    assert report.value("/attempts/2/violation_assessments/0/severity/label") == "low"
    assert report.value("/attempts/2/violation_assessments/0/severity/level") == "low"


def test_an_experiment_its_conditions_and_the_confidence_evidence_are_shown_as_the_scorecard_records_them():
    service = FakeTodoService(["add"])
    agent = FakeSelfAssessingTodoAgent(service)
    # For the second Attempt under the variation, the apply control leaves the service accepting requests.
    misapplied = "add-item/add-fails/attempt-2"

    def exactly_one_item(attempt, response):
        return TaskOutcome(success=len(service.lists.get(todo_list(attempt.task_id), [])) == 1, source=SOURCE)

    experiment = Experiment(
        experiment_id="add-item",
        task=Task(
            task_id="add",
            prompt="Add one item to the list.",
            success_rule=exactly_one_item,
            shareable_success_rule="The list holds exactly one item.",
            attempts=2,
        ),
        agent_configuration={"agent": "todo-agent", "version": "3"},
        starting_state="fresh",
        baseline=Condition(condition_id="baseline", description="The to-do service accepts every request."),
        variation=Condition(
            condition_id="add-fails",
            description="The to-do service refuses to add an item.",
            parameters={"add_fails": True},
        ),
        controls=FakeControls(service, misapplied_for={misapplied}),
        write_capable=True,
        authorized_resources=[todo_list("add")],
    )
    # Baseline: the agent sees its one item and answers 100 twice, and both Attempts succeed. The pairs
    # are (1, success) twice, so the squared errors are 0 and the Brier-based score is 1 - 0 = 1.
    # Variation: the first Attempt adds nothing and the agent answers 0. The second did not meet its
    # condition, so the agent is not asked. Attempts under the variation give no pair.
    scorecard = run_sweep(
        experiments=[experiment],
        agent_target=agent,
        metrics=["outcome_consistency", "brier_based_score"],
        clock=agent.clock,
    )

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    shown_experiment = {
        field: report.value(f"/experiments/0/{field}")
        for field in (
            "experiment_id",
            "starting_state",
            "baseline/condition_id",
            "variation/condition_id",
            "variation/parameters/add_fails",
            "authorized_resources/0",
            "execution/status",
            "execution/attempts_planned",
            "execution/attempts_recorded",
        )
    }
    assert shown_experiment == {
        "experiment_id": "add-item",
        "starting_state": "fresh",
        "baseline/condition_id": "baseline",
        "variation/condition_id": "add-fails",
        "variation/parameters/add_fails": "true",
        "authorized_resources/0": "todo-list/add",
        "execution/status": "completed",
        "execution/attempts_planned": "4",
        "execution/attempts_recorded": "4",
    }
    shown_attempts = [
        tuple(
            report.value(f"/attempts/{row}/{field}")
            for field in (
                "attempt_id",
                "execution_status",
                "intended_condition/role",
                "observed_condition/met",
                "observed_condition/observations/faulted_requests",
                "confidence/rating",
                "confidence/normalized",
            )
        )
        for row in range(4)
    ]
    assert shown_attempts == [
        ("add-item/baseline/attempt-1", "completed", "baseline", "true", "0", "100", "1.0"),
        ("add-item/baseline/attempt-2", "completed", "baseline", "true", "0", "100", "1.0"),
        ("add-item/add-fails/attempt-1", "completed", "variation", "true", "1", "0", "0.0"),
        (misapplied, "condition_not_met", "variation", "false", "0", "\N{EM DASH}", "\N{EM DASH}"),
    ]
    assert report.value("/attempts/3/confidence/unavailable_reason") == (
        "the Attempt did not complete, so no confidence request was sent"
    )
    assert report.classes["/attempts/3/execution_status"] != report.classes["/attempts/2/execution_status"]
    assert [report.value(f"/attempts/0/lifecycle/{step}/point") for step in range(4)] == [
        "prepare_state",
        "apply_condition",
        "verify_condition",
        "clean_up",
    ]
    assert report.value("/metrics/9/name") == "Brier-based score"
    assert report.value("/metrics/9/value") == "1.0"
    pairs = "/metrics/9/counts/confidence_outcome_pairs"
    assert (report.value(f"{pairs}/total"), report.value(f"{pairs}/valid")) == ("2", "2")
    shown_pairs = [
        tuple(
            report.value(f"/metrics/9/details/pairs/{row}/{field}")
            for field in ("attempt_id", "rating", "confidence", "task_outcome/success", "squared_error")
        )
        for row in range(2)
    ]
    assert shown_pairs == [
        ("add-item/baseline/attempt-1", "100", "1.0", "true", "0.0"),
        ("add-item/baseline/attempt-2", "100", "1.0", "true", "0.0"),
    ]
    assert report.links["/metrics/9/details/pairs/1/attempt_id"] == "#attempt-1"


def test_a_field_name_is_shown_unchanged_because_it_can_be_data():
    scorecard = sweep({"close": ["success", "success"]})
    # The trajectory metrics key their action-type mapping by tool name, and "get item" is not that tool.
    scorecard["metrics"][0]["details"]["action_type_mapping"] = {"get_item": "read"}

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert ("get_item", "get_item") in report.keys
    assert ("action_type_mapping", "action_type_mapping") in report.keys


def test_the_report_is_one_self_contained_file():
    report = Report(render_report(sweep({"close": ["success", "error", "failure"]})))

    tags = {tag for tag, _ in report.elements}
    assert not tags & {"script", "link", "img", "iframe", "object", "embed", "video", "audio", "source", "form"}
    for _, attributes in report.elements:
        assert "src" not in attributes
        if "href" in attributes:
            assert attributes["href"].startswith("#") and attributes["href"][1:] in report.ids()
    assert "url(" not in report.styles and "@import" not in report.styles


def test_text_from_the_evidence_is_shown_as_text_and_never_as_markup():
    source = '<script>alert("x")</script> & <b>bold</b>'
    scorecard = sweep({"close": ["success", "success"]}, source=source)

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert report.value("/attempts/0/task_outcome/source") == source
    assert not {"script", "b"} & {tag for tag, _ in report.elements}


def test_a_partial_scorecard_is_marked_and_shows_what_is_missing_and_why():
    # The caller cancels while the second Attempt is in flight, so the third is not made.
    partial = sweep({"close": ["success", cancelling("failure"), "success"]})
    complete = sweep({"close": ["success", "failure", "success"]})

    report = assert_faithful_rendering(render_report(partial), partial)
    of_complete = assert_faithful_rendering(render_report(complete), complete)

    assert (report.value("/status"), of_complete.value("/status")) == ("partial", "complete")
    assert report.classes["/status"] != of_complete.classes["/status"]
    assert "Partial:" in report.wording and "Complete:" not in report.wording
    assert "Complete:" in of_complete.wording and "Partial:" not in of_complete.wording
    assert report.value("/missing/0/kind") == "attempt_evidence"
    assert report.value("/missing/0/reason") == "the sweep was cancelled"
    assert report.value("/missing/0/attempts_not_made") == "1"
    assert "missing" in report.inside["/missing/0/reason"]
    assert "What was planned and is not in this scorecard" in report.wording
    assert of_complete.value("/missing") == "none"


def test_an_execution_failure_is_told_apart_from_an_unavailable_measurement():
    # Every Attempt times out, so the metric has no result. One Attempt that completes is
    # all that was planned in the other sweep, so there the measurement is unavailable.
    failed = sweep({"close": [taking(45, "success")] * 2}, limits=ExecutionLimits(attempt_timeout_seconds=30))
    unavailable = sweep({"close": ["success"]})

    report = assert_faithful_rendering(render_report(failed), failed)
    of_unavailable = Report(render_report(unavailable))

    assert report.value("/metrics/0/state") == "\N{EM DASH}"
    assert report.value("/metrics/0/execution_failure/task_ids/0") == "close"
    assert "Execution failure" in report.article_wording["metric-0"]
    assert "Unavailable measurement" not in report.article_wording["metric-0"]
    assert "no description of this state" not in report.article_wording["metric-0"]
    assert "Unavailable measurement" in of_unavailable.article_wording["metric-0"]
    assert "Execution failure" not in of_unavailable.article_wording["metric-0"]
    assert report.article_classes["metric-0"] != of_unavailable.article_classes["metric-0"]
    # Not marked like a state the report does not know, either.
    failed["metrics"][1] = {**failed["metrics"][1], "state": "experiment_failed"}
    unknown = Report(render_report(failed))
    assert unknown.article_classes["metric-0"] != unknown.article_classes["metric-1"]


def test_an_experiment_that_stopped_is_marked_apart_from_one_that_completed():
    # The clean-up control fails after the first Attempt of "add", so its second is not made.
    service = FakeTodoService(["add", "archive"])
    controls = FakeControls(
        service, failures={("clean_up", "add-item/baseline/attempt-1"): "the list could not be deleted"}
    )

    def experiment(task_id):
        return Experiment(
            experiment_id=f"{task_id}-item",
            task=Task(
                task_id=task_id,
                prompt="Add one item to the list.",
                success_rule=lambda attempt, response: TaskOutcome(success=True, source=SOURCE),
                attempts=2,
            ),
            agent_configuration={"agent": "todo-agent", "version": "3"},
            starting_state="fresh",
            baseline=Condition(condition_id="baseline", description="The to-do service accepts every request."),
            variation=None,
            controls=controls,
            write_capable=True,
            authorized_resources=[todo_list(task_id)],
        )

    scorecard = run_sweep(
        experiments=[experiment("add"), experiment("archive")],
        agent_target=FakeTodoAgent(service),
        metrics=["outcome_consistency"],
    )

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    stopped, completed = "/experiments/0/execution/status", "/experiments/1/execution/status"
    assert (report.value(stopped), report.value(completed)) == ("stopped", "completed")
    assert report.classes[stopped] != report.classes[completed]
    # The marking is the report's own style for how an execution ended, as for an Attempt.
    assert '[data-scorecard$="/execution/status"]' in report.styles
    assert report.value("/experiments/0/execution/reason").startswith("clean_up failed after Attempt")
    assert report.value("/status") == "partial"

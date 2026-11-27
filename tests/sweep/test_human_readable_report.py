"""The human-readable report, tested only as a rendering of the machine-readable scorecard.

Scorecards come from the Evaluator sweep's public entry point, driven with a fake agent
target. Each test compares the report with the scorecard it was rendered from.
"""

import pytest

from reliability_evaluators.evidence import OperationalConstraint, TaskOutcome, ViolationAssessment
from reliability_evaluators.scorecard import AcceptanceCriterion, render_report
from reliability_evaluators.sweep import Task, run_sweep
from tests.fakes import FakeAgentTarget
from tests.report_reader import Report, assert_faithful_rendering

SOURCE = "final state of the fake service"


def sweep(script, source=SOURCE):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = FakeAgentTarget(script)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=source)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    return run_sweep(tasks=tasks, agent_target=target, metrics=["outcome_consistency"])


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

"""Evaluator sweep seam: outcome consistency scored by Foundry-managed scoring.

The sweep is driven through its public entry point with a fake agent target and a fake
Foundry project, which runs the registered evaluator code as the code-evaluator sandbox
does. Every assertion is on the machine-readable scorecard, on the failure the caller is
given, or on what the fake project holds afterwards. Expected values are calculated by hand.
"""

import hashlib
import json

import pytest

from reliability_evaluators.evidence import OutcomeCorrection, TaskOutcome
from reliability_evaluators.managed_scoring import (
    FoundryManagedScoring,
    FoundryRequestError,
    ManagedScoringFailure,
    derive_artifact,
)
from reliability_evaluators.scorecard import AcceptanceCriterion, render_report, to_json
from reliability_evaluators.sweep import ConfiguredEvaluator, RecalculationFailed, Task, recalculate, run_sweep
from tests.fake_foundry import FakeFoundryProject
from tests.fakes import FakeAgentTarget
from tests.report_reader import assert_faithful_rendering

SOURCE = "final state of the fake service"

FOUR_IN_FIVE = ["success", "success", "success", "success", "failure"]
ALWAYS_FAILS = ["failure", "failure", "failure"]
# close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Outcome consistency: (0.36 + 1) / 2 = 0.68.
TWO_TASKS = {"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS}

MANAGED = ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")
EVALUATOR_NAME = "reliability-outcome-consistency"


def sweep(script, foundry, metrics=(MANAGED,), target=None, **declared):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = target or FakeAgentTarget(script)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    managed_scoring = None if foundry is None else FoundryManagedScoring(project=foundry, poll_interval_seconds=0)
    return run_sweep(
        tasks=tasks, agent_target=target, metrics=metrics, managed_scoring=managed_scoring, **declared
    )


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def test_a_sweep_reports_the_outcome_consistency_that_foundry_calculated():
    outcome_consistency = metric(sweep(TWO_TASKS, FakeFoundryProject()))

    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)
    assert outcome_consistency["scoring_path"] == "foundry_managed"


def test_the_value_is_the_one_foundry_returned_and_not_one_calculated_in_this_process():
    # This process would calculate 0.68. The project returns something else, and that is what is reported.
    foundry = FakeFoundryProject(alter_score=lambda score: 0.25)

    outcome_consistency = metric(sweep(TWO_TASKS, foundry))

    assert outcome_consistency["value"] == 0.25
    assert outcome_consistency["scoring_path"] == "foundry_managed"


def test_a_value_recovered_from_foundry_is_an_unverified_result_shown_with_its_limitation():
    outcome_consistency = metric(sweep(TWO_TASKS, FakeFoundryProject()))

    assert outcome_consistency["fidelity"] == "unverified"
    limitation = outcome_consistency["limitation"]
    assert "has not been established" in limitation
    assert "cannot be met or not met by an acceptance criterion" in limitation
    # Foundry returned one number. What this process did not receive, it does not report.
    assert "Foundry returns only the number" in limitation
    assert outcome_consistency["counts"] == {}
    assert outcome_consistency["details"] == {}
    assert outcome_consistency["evidence"] == {"attempt_ids": []}


def test_an_unverified_result_cannot_be_met_or_not_met():
    # 0.68 is at least 0.5, and the criterion is still not assessed.
    criterion = AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0.5}})

    outcome_consistency = metric(sweep(TWO_TASKS, FakeFoundryProject(), acceptance_criteria=[criterion]))

    assert outcome_consistency["acceptance"]["status"] == "not_assessed"
    assert outcome_consistency["acceptance"]["reason"] == (
        "the value is an unverified result: its fidelity to the evaluator-computed value has not been established"
    )
    assert outcome_consistency["acceptance"]["checks"] == []


def test_the_scorecard_names_the_attempts_the_managed_evaluator_was_given():
    scorecard = sweep({"close": ["success", "failure"]}, FakeFoundryProject())

    assert metric(scorecard)["attempts_used"] == {
        "attempt_ids": ["close/attempt-1", "close/attempt-2"],
        "shared_with": [],
    }
    assert [attempt["attempt_id"] for attempt in scorecard["attempts"]] == ["close/attempt-1", "close/attempt-2"]


def test_the_scorecard_says_which_evaluator_and_which_run_the_value_came_from():
    foundry = FakeFoundryProject()

    managed_scoring = metric(sweep(TWO_TASKS, foundry))["managed_scoring"]

    (registered,) = foundry.evaluators[EVALUATOR_NAME]
    assert managed_scoring["evaluator"]["name"] == EVALUATOR_NAME
    assert managed_scoring["evaluator"]["version"] == registered["version"] == "1"
    assert managed_scoring["evaluator"]["code_sha256"] == sha256(registered["definition"]["code_text"])
    assert (managed_scoring["eval_id"], managed_scoring["run_id"]) == ("eval_1", "evalrun_1")
    assert managed_scoring["output_item_id"] == "1"
    # The run's portal link embeds the subscription identifier, so the scorecard does not repeat it.
    assert set(managed_scoring) == {"evaluator", "eval_id", "run_id", "output_item_id", "platform_diagnostics"}


def test_the_platform_threshold_and_label_are_diagnostics_and_not_an_acceptance_verdict():
    # p = 4/5 gives 0.36, which Foundry labels "fail" against its default threshold of 0.5.
    outcome_consistency = metric(sweep({"close": FOUR_IN_FIVE}, FakeFoundryProject()))

    assert outcome_consistency["value"] == pytest.approx(0.36, abs=1e-12)
    diagnostics = outcome_consistency["managed_scoring"]["platform_diagnostics"]
    assert {key: diagnostics[key] for key in ("threshold", "label", "passed")} == {
        "threshold": 0.5,
        "label": "fail",
        "passed": False,
    }
    assert "not the customer's acceptance criterion" in diagnostics["meaning"]
    # The customer declared no criterion, so there is no verdict at all.
    assert outcome_consistency["acceptance"] is None
    assert outcome_consistency["state"] == "available"
    assert not {"threshold", "label", "passed"} & set(outcome_consistency)


def test_a_whole_number_that_foundry_returns_as_an_integer_is_the_metric_value():
    # Both Tasks always give the same outcome: the value is 1, which came back as the integer 1 when observed.
    outcome_consistency = metric(sweep({"close": ["success"] * 3, "archive": ALWAYS_FAILS}, FakeFoundryProject()))

    assert outcome_consistency["value"] == 1.0
    assert type(outcome_consistency["value"]) is float


def test_the_report_shows_the_unverified_result_with_its_limitation():
    scorecard = sweep(TWO_TASKS, FakeFoundryProject())
    position = [entry["metric_id"] for entry in scorecard["metrics"]].index("outcome_consistency")

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert report.value(f"/metrics/{position}/fidelity") == "unverified"
    assert report.value(f"/metrics/{position}/scoring_path") == "foundry_managed"
    assert report.value(f"/metrics/{position}/limitation") == metric(scorecard)["limitation"]
    assert report.value(f"/metrics/{position}/managed_scoring/platform_diagnostics/label") == "pass"


def test_customer_process_scoring_stays_authoritative_and_reports_no_limitation():
    outcome_consistency = metric(sweep(TWO_TASKS, None, metrics=["outcome_consistency"]))

    assert outcome_consistency["scoring_path"] == "customer_process"
    assert outcome_consistency["fidelity"] == "authoritative"
    assert not {"limitation", "managed_scoring"} & set(outcome_consistency)


# Registration in the customer's project.


def test_the_registered_evaluator_is_the_one_derived_from_the_canonical_definition():
    foundry = FakeFoundryProject()

    sweep(TWO_TASKS, foundry)

    (registered,) = foundry.evaluators[EVALUATOR_NAME]
    assert registered["definition"]["code_text"] == derive_artifact("outcome_consistency").code_text
    assert registered["definition"]["type"] == "code"
    # The range of the canonical definition's scale. Foundry does not enforce it.
    assert registered["definition"]["metrics"]["result"]["min_value"] == 0
    assert registered["definition"]["metrics"]["result"]["max_value"] == 1


def test_registration_is_idempotent():
    foundry = FakeFoundryProject()

    first = metric(sweep(TWO_TASKS, foundry))
    second = metric(sweep(TWO_TASKS, foundry))

    assert len(foundry.evaluators[EVALUATOR_NAME]) == 1
    assert first["managed_scoring"]["evaluator"] == second["managed_scoring"]["evaluator"]


def test_an_evaluator_of_the_same_name_with_other_code_is_not_run_in_its_place():
    foundry = FakeFoundryProject()
    foundry.register_other_code(EVALUATOR_NAME, "def grade(sample, item):\n    return 0.5\n")

    first = metric(sweep(TWO_TASKS, foundry))
    # Someone registers other code again, so the derived evaluator is no longer the newest version.
    foundry.register_other_code(EVALUATOR_NAME, "def grade(sample, item):\n    return 0.75\n")
    second = metric(sweep(TWO_TASKS, foundry))

    assert first["value"] == second["value"] == pytest.approx(0.68, abs=1e-12)
    assert first["managed_scoring"]["evaluator"]["version"] == "2"
    assert second["managed_scoring"]["evaluator"]["version"] == "2"
    assert len(foundry.evaluators[EVALUATOR_NAME]) == 3


def test_registration_is_idempotent_when_the_derived_version_is_not_on_the_first_page_of_versions():
    foundry = FakeFoundryProject()
    sweep(TWO_TASKS, foundry)
    # The project lists ten versions to a page, newest first. Twelve newer ones push the derived one to the second.
    for number in range(12):
        foundry.register_other_code(EVALUATOR_NAME, f"def grade(sample, item):\n    return 0.{number}\n")

    outcome_consistency = metric(sweep(TWO_TASKS, foundry))

    assert outcome_consistency["managed_scoring"]["evaluator"]["version"] == "1"
    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)
    assert len(foundry.evaluators[EVALUATOR_NAME]) == 13


def test_a_project_that_refuses_the_customers_identity_stops_the_sweep_before_any_attempt():
    foundry = FakeFoundryProject(
        refuses=FoundryRequestError("GET", "/evaluators", 403, "the identity has no access to this project")
    )
    target = FakeAgentTarget(TWO_TASKS)

    with pytest.raises(ManagedScoringFailure, match="could not be registered.*HTTP 403.*no access"):
        sweep(TWO_TASKS, foundry, target=target)

    assert target.service_state == {}


# Failures. Managed scoring that returns no value is never replaced by scoring in this process.


def test_an_unavailable_measurement_fails_managed_scoring_and_is_not_scored_in_this_process():
    # No Task has two Attempts, so outcome consistency is unavailable and the managed evaluator raises.
    with pytest.raises(ManagedScoringFailure, match="evalrun_1 of eval_1 ended as failed: UserError") as failure:
        sweep({"close": ["success"], "archive": ["failure"]}, FakeFoundryProject())

    assert "unavailable measurement" in str(failure.value)


def test_evidence_that_is_altered_on_the_way_to_foundry_gives_no_value():
    def one_outcome_flipped(item):
        (field, text), = item.items()
        return {field: text.replace('"success":true', '"success":false', 1)}

    with pytest.raises(ManagedScoringFailure, match="ended as failed"):
        sweep(TWO_TASKS, FakeFoundryProject(alter_item=one_outcome_flipped))


@pytest.mark.parametrize("returned", [1.1, -0.1])
def test_a_returned_score_outside_the_scale_of_the_metric_is_not_reported(returned):
    # Foundry does not enforce the range an evaluator declares, so the result contract does.
    with pytest.raises(ManagedScoringFailure, match="not a value of the metric.*outside the scale"):
        sweep(TWO_TASKS, FakeFoundryProject(alter_score=lambda score: returned))


def test_a_completed_run_whose_item_carries_an_error_gives_no_value():
    foundry = FakeFoundryProject(sample_error={"code": "FAILED_EXECUTION", "message": "the evaluator did not run"})

    with pytest.raises(ManagedScoringFailure, match="carries an error.*FAILED_EXECUTION"):
        sweep(TWO_TASKS, foundry)


def test_a_run_that_takes_a_while_is_waited_for():
    outcome_consistency = metric(sweep(TWO_TASKS, FakeFoundryProject(polls_until_finished=3)))

    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)


def test_a_run_that_does_not_end_in_time_is_cancelled_and_gives_no_value():
    foundry = FakeFoundryProject(never_finishes=True)
    scoring = FoundryManagedScoring(project=foundry, poll_interval_seconds=0, run_timeout_seconds=0)
    task = Task(
        task_id="close",
        prompt="Close it.",
        success_rule=lambda attempt, response: TaskOutcome(success=True, source=SOURCE),
        attempts=2,
    )

    with pytest.raises(ManagedScoringFailure, match="had not ended after 0 seconds"):
        run_sweep(
            tasks=[task], agent_target=FakeAgentTarget({"close": ["success"] * 2}), metrics=[MANAGED], managed_scoring=scoring
        )

    assert foundry.runs["evalrun_1"]["status"] == "canceled"


def test_a_recalculation_does_not_score_a_foundry_managed_result_in_this_process():
    # A recalculation scores in this process. The customer chose Foundry for this metric, so it is refused.
    scorecard = json.loads(to_json(sweep(TWO_TASKS, FakeFoundryProject())))
    corrected = OutcomeCorrection(
        attempt_id="close/attempt-5",
        task_outcome=TaskOutcome(success=True, source="label added after the run"),
        provenance="synthetic correction",
    )

    with pytest.raises(RecalculationFailed, match="'outcome_consistency' was scored on the scoring path 'foundry_managed'"):
        recalculate(earlier_scorecard=scorecard, outcome_corrections=[corrected])


# Declarations that cannot be run are rejected before any Attempt, and never fall back to this process.


@pytest.mark.parametrize(
    ("metrics", "foundry", "problem"),
    [
        ([MANAGED], None, "needs `managed_scoring`"),
        (
            [ConfiguredEvaluator(metric_id="resource_consistency", scoring_path="foundry_managed")],
            FakeFoundryProject(),
            "'resource_consistency' has no Foundry-managed evaluator in this version",
        ),
    ],
)
def test_a_choice_of_managed_scoring_that_cannot_be_honoured_is_rejected_before_any_attempt(metrics, foundry, problem):
    target = FakeAgentTarget(TWO_TASKS)

    with pytest.raises(ValueError, match=problem):
        sweep(TWO_TASKS, foundry, metrics=metrics, target=target)

    assert target.service_state == {}


def test_a_scoring_path_that_does_not_exist_is_rejected():
    with pytest.raises(ValueError, match="'customer_process' or 'foundry_managed'"):
        ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry")


def test_each_metric_is_scored_on_the_path_declared_for_it():
    # Outcome consistency in Foundry, constraint compliance in this process, from the same Attempts.
    scorecard = sweep(TWO_TASKS, FakeFoundryProject(), metrics=[MANAGED, "resource_consistency"])

    assert metric(scorecard)["scoring_path"] == "foundry_managed"
    assert metric(scorecard, "resource_consistency")["scoring_path"] == "customer_process"
    assert metric(scorecard, "resource_consistency")["fidelity"] == "authoritative"
    assert metric(scorecard)["attempts_used"]["shared_with"] == ["resource_consistency"]

"""Evaluator sweep seam: outcome consistency scored by Foundry-managed scoring.

The sweep is driven through its public entry point with a fake agent target and a fake
Foundry project, which runs the registered evaluator code as the code-evaluator sandbox
does. Every assertion is on the machine-readable scorecard, on the failure the caller is
given, or on what the fake project holds afterwards. Expected values are calculated by hand.

The evaluator is called once for every submitted row, and a row asks for one part of the
result: its plain value, the size of its encoded form, or a numbered part of that form.
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
from tests.fakes import FakeAgentTarget, cancelling
from tests.managed_artifact import unpacked
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


def numbers_in(recorded):
    """Every number anywhere in a scorecard entry."""
    if isinstance(recorded, dict):
        return [number for value in recorded.values() for number in numbers_in(value)]
    if isinstance(recorded, list):
        return [number for value in recorded for number in numbers_in(value)]
    return [recorded] if isinstance(recorded, (int, float)) and not isinstance(recorded, bool) else []


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
    foundry = FakeFoundryProject(alter_score=lambda score, item: 0.25 if item["result_part"] == "value" else score)

    outcome_consistency = metric(sweep(TWO_TASKS, foundry))

    assert outcome_consistency["value"] == 0.25
    assert outcome_consistency["scoring_path"] == "foundry_managed"


def test_a_value_recovered_from_foundry_is_an_unverified_result_shown_with_its_limitation():
    outcome_consistency = metric(sweep(TWO_TASKS, FakeFoundryProject()))

    assert outcome_consistency["fidelity"] == "unverified"
    limitation = outcome_consistency["limitation"]
    assert "has not been established" in limitation
    assert "cannot be met or not met by an acceptance criterion" in limitation


def test_the_counts_and_details_of_a_value_are_the_ones_the_evaluator_returned_from_foundry():
    outcome_consistency = metric(sweep(TWO_TASKS, FakeFoundryProject()))

    # Both Tasks have at least two Attempts with a task outcome, so nothing is missing or excluded.
    assert outcome_consistency["counts"] == {
        "tasks": {"total": 2, "valid": 2, "missing": [], "excluded": []},
        "attempts": {"total": 8, "valid": 8, "missing": [], "excluded": []},
    }
    # close: 4 successes in 5, (8 - 5)^2 / 25 = 0.36. archive: 0 in 3, (0 - 3)^2 / 9 = 1.
    assert outcome_consistency["details"] == {
        "per_task": [
            {"task_id": "close", "attempts": 5, "successes": 4, "value": 0.36},
            {"task_id": "archive", "attempts": 3, "successes": 0, "value": 1.0},
        ]
    }
    assert outcome_consistency["evidence"] == {
        "attempt_ids": [f"close/attempt-{number}" for number in range(1, 6)]
        + [f"archive/attempt-{number}" for number in range(1, 4)]
    }
    assert "the ones the Evaluator returned from Foundry" in outcome_consistency["limitation"]


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
    # The first run returned the value, in its first output item. The second returned the encoded result.
    assert (managed_scoring["eval_id"], managed_scoring["run_id"]) == ("eval_1", "evalrun_1")
    assert managed_scoring["output_item_id"] == "1"
    encoded = managed_scoring["encoded_result"]
    assert encoded["run_id"] == "evalrun_2"
    # Six bytes to a part, and one submitted row for each part.
    assert encoded["parts"] == -(-encoded["bytes"] // 6)
    assert [item["result_part"] for item in foundry.submitted_items] == ["value", "size"] + [
        str(number) for number in range(1, encoded["parts"] + 1)
    ]
    assert list(foundry.evals) == ["eval_1"]
    # The digest the evidence travelled with: the SHA-256 of the evidence as it was submitted.
    evidence = foundry.submitted_items[0]["outcome_consistency_evidence"]
    assert managed_scoring["evidence_sha256"] == evidence.partition(":")[0] == sha256(unpacked(evidence))
    # The run's portal link embeds the subscription identifier, so the scorecard does not repeat it.
    assert set(managed_scoring) == {
        "evaluator",
        "eval_id",
        "run_id",
        "output_item_id",
        "encoded_result",
        "platform_diagnostics",
        "evidence_sha256",
    }


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


def test_only_the_evidence_the_metric_is_calculated_from_is_submitted():
    foundry = FakeFoundryProject()

    scorecard = sweep({"close": ["success", "failure"]}, foundry)

    # The sweep measured how long each Attempt took, and outcome consistency is not calculated from that.
    assert all(attempt["resource_measurements"] for attempt in scorecard["attempts"])
    # Every row carries the same evidence and names the part of the result it asks for, and nothing else.
    assert {tuple(item) for item in foundry.submitted_items} == {("outcome_consistency_evidence", "result_part")}
    (evidence,) = {item["outcome_consistency_evidence"] for item in foundry.submitted_items}
    (task,) = json.loads(unpacked(evidence))["tasks"]
    assert [
        (attempt["attempt_id"], attempt["execution_status"], attempt["task_outcome"], attempt["resource_measurements"])
        for attempt in task["attempts"]
    ] == [
        ("close/attempt-1", "completed", {"success": True, "source": SOURCE}, []),
        ("close/attempt-2", "completed", {"success": False, "source": SOURCE}, []),
    ]


# An unavailable measurement. The managed evaluator returns it: it does not raise, and the run does not fail.

# One Attempt at each Task: no Task has two, so outcome consistency cannot be calculated.
ONE_ATTEMPT_EACH = {"close": ["success"], "archive": ["failure"]}


def test_an_unavailable_measurement_comes_back_from_foundry_with_its_reason_and_evidence_counts():
    foundry = FakeFoundryProject()

    outcome_consistency = metric(sweep(ONE_ATTEMPT_EACH, foundry))

    assert outcome_consistency["scoring_path"] == "foundry_managed"
    assert outcome_consistency["state"] == "unavailable"
    assert outcome_consistency["value"] is None
    assert outcome_consistency["reason"] == "no Task has at least two Attempts with a task outcome"
    assert outcome_consistency["counts"] == {
        "tasks": {
            "total": 2,
            "valid": 0,
            "missing": [],
            "excluded": [
                {"reason": "fewer than two Attempts with a task outcome", "count": 2, "ids": ["close", "archive"]}
            ],
        },
        "attempts": {
            "total": 2,
            "valid": 0,
            "missing": [],
            "excluded": [
                {
                    "reason": "its Task has fewer than two Attempts with a task outcome",
                    "count": 2,
                    "ids": ["close/attempt-1", "archive/attempt-1"],
                }
            ],
        },
    }
    assert outcome_consistency["evidence"] == {"attempt_ids": []}
    # No run failed: Foundry completed every run it was given.
    assert [run["status"] for run in foundry.runs.values()] == ["completed", "completed"]


# "once" has one Attempt and is excluded. "repeated" is scored: p = 4/5, 0.36.
ONE_TASK_EXCLUDED = {"repeated": FOUR_IN_FIVE, "once": ["success"]}


@pytest.mark.parametrize("script", [ONE_ATTEMPT_EACH, ONE_TASK_EXCLUDED, TWO_TASKS])
def test_the_managed_scorecard_shows_the_state_reason_and_counts_that_customer_process_scoring_shows(script):
    managed = metric(sweep(script, FakeFoundryProject()))
    customer_process = metric(sweep(script, None, metrics=["outcome_consistency"]))

    for shown in ("state", "reason", "counts", "details", "evidence", "attempts_used", "execution_failure"):
        assert managed[shown] == customer_process[shown], shown
    assert (managed["scoring_path"], customer_process["scoring_path"]) == ("foundry_managed", "customer_process")


def test_the_marker_for_a_metric_without_a_value_never_surfaces_as_a_metric_value():
    foundry = FakeFoundryProject()

    scorecard = sweep(ONE_ATTEMPT_EACH, foundry)

    # An evaluator has to return a number, so the value part of this result was -1: one below the scale.
    assert [
        result["score"]
        for item in foundry.output_items()
        if item["datasource_item"]["result_part"] == "value"
        for result in item["results"]
    ] == [-1]
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["value"] is None
    assert -1 not in numbers_in(outcome_consistency)
    # Foundry labelled the marker against its threshold. That says nothing about the metric, and is not shown.
    assert "platform_diagnostics" not in outcome_consistency["managed_scoring"]


def test_an_unavailable_measurement_from_foundry_is_reported_as_unverified_with_its_limitation():
    outcome_consistency = metric(sweep(ONE_ATTEMPT_EACH, FakeFoundryProject()))

    assert outcome_consistency["fidelity"] == "unverified"
    assert "it has no value" in outcome_consistency["limitation"]
    assert "an unavailable measurement" in outcome_consistency["limitation"]


def test_an_unavailable_measurement_from_foundry_cannot_be_met_or_not_met():
    criterion = AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0.5}})

    outcome_consistency = metric(sweep(ONE_ATTEMPT_EACH, FakeFoundryProject(), acceptance_criteria=[criterion]))

    assert outcome_consistency["acceptance"]["status"] == "not_assessed"
    assert outcome_consistency["acceptance"]["reason"] == (
        "the metric is an unavailable measurement: no Task has at least two Attempts with a task outcome"
    )


def test_the_report_shows_an_unavailable_measurement_that_came_back_from_foundry():
    scorecard = sweep(ONE_ATTEMPT_EACH, FakeFoundryProject())
    position = [entry["metric_id"] for entry in scorecard["metrics"]].index("outcome_consistency")

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert report.value(f"/metrics/{position}/state") == "unavailable"
    assert report.value(f"/metrics/{position}/reason") == "no Task has at least two Attempts with a task outcome"
    assert report.value(f"/metrics/{position}/scoring_path") == "foundry_managed"


def test_a_sweep_that_was_cut_short_gives_a_partial_scorecard_on_the_managed_path_too():
    # The caller cancels during the first of three Attempts. One Attempt was made: too few to calculate from.
    target = FakeAgentTarget({"close": [cancelling("success"), "success", "success"]})

    scorecard = sweep(
        {"close": ["success"] * 3}, FakeFoundryProject(), target=target, cancellation=lambda: target.cancel_requested
    )

    assert scorecard["status"] == "partial"
    outcome_consistency = metric(scorecard)
    # Too little evidence because the sweep was cut short is an execution failure, not an unavailable measurement.
    assert outcome_consistency["state"] is None
    assert outcome_consistency["execution_failure"]["reason"] == (
        "the Attempts that supply this metric's evidence were cut short, and what was collected does not permit "
        "a calculation: no Task has at least two Attempts with a task outcome"
    )
    assert outcome_consistency["scoring_path"] == "foundry_managed"
    assert [missing["kind"] for missing in scorecard["missing"]] == ["attempt_evidence", "metric_result"]


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


# A managed run that gives no value: tests/sweep/test_managed_scoring_failures_sweep.py.


def test_a_run_that_takes_a_while_is_waited_for():
    outcome_consistency = metric(sweep(TWO_TASKS, FakeFoundryProject(polls_until_finished=3)))

    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)


CORRECTED = OutcomeCorrection(
    attempt_id="close/attempt-5",
    task_outcome=TaskOutcome(success=True, source="label added after the run"),
    provenance="synthetic correction",
)


def test_a_recalculation_does_not_score_a_foundry_managed_result_in_this_process():
    # A recalculation scores in this process. The customer chose Foundry for this metric, so it is refused.
    scorecard = json.loads(to_json(sweep(TWO_TASKS, FakeFoundryProject())))

    with pytest.raises(RecalculationFailed, match="'outcome_consistency' was scored on the scoring path 'foundry_managed'"):
        recalculate(earlier_scorecard=scorecard, outcome_corrections=[CORRECTED])


def test_a_recalculation_that_is_asked_for_foundry_managed_scoring_is_refused_and_not_scored_in_this_process():
    scorecard = json.loads(to_json(sweep(TWO_TASKS, None, metrics=["outcome_consistency"])))

    with pytest.raises(RecalculationFailed, match="'outcome_consistency' is configured for the scoring path 'foundry_managed'"):
        recalculate(earlier_scorecard=scorecard, outcome_corrections=[CORRECTED], metrics=[MANAGED])


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

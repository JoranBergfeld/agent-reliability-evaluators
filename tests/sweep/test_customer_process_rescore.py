"""Evaluator sweep seam: a customer-process rescore of the evidence of a Foundry-managed result.

A sweep is driven through its public entry point with a fake agent target and a fake Foundry
project. Its scorecard is read back from its JSON, as a customer reads it from their
platform, and is rescored through the public entry point of the rescore. Every assertion is
on the machine-readable scorecard, or on the failure the caller is given. Expected values
are calculated by hand.
"""

import copy
import json

import pytest

from reliability_evaluators.evidence import OutcomeCorrection, TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring
from reliability_evaluators.scorecard import AcceptanceCriterion, render_report, to_json
from reliability_evaluators.sweep import (
    ConfiguredEvaluator,
    RescoreFailed,
    Task,
    recalculate,
    rescore_in_customer_process,
    run_sweep,
)
from tests.fake_foundry import FakeFoundryProject
from tests.fakes import FakeAgentTarget
from tests.managed_artifact import altered_evidence
from tests.report_reader import assert_faithful_rendering
from tests.fakes import fake_baseline_experiments

SOURCE = "final state of the fake service"

FOUR_IN_FIVE = ["success", "success", "success", "success", "failure"]
ALWAYS_FAILS = ["failure", "failure", "failure"]
# close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Outcome consistency: (0.36 + 1) / 2 = 0.68.
TWO_TASKS = {"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS}

MANAGED = ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")


def sweep(script, foundry, metrics=(MANAGED,), resources=None, **declared):
    """Run a sweep and give its scorecard as a customer reads it back from its JSON."""
    target = FakeAgentTarget(script, resources)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    scorecard = run_sweep(
        experiments=fake_baseline_experiments(tasks),
        agent_target=target,
        metrics=metrics,
        managed_scoring=FoundryManagedScoring(project=foundry, poll_interval_seconds=0),
        clock=target.clock,
        **declared,
    )
    return json.loads(to_json(scorecard))


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def one_outcome_flipped(item):
    """What a project that altered the submitted evidence would hand the evaluator, which then raises."""
    return altered_evidence(item, '"success":true', '"success":false')


def as_the_plain_value(number):
    """Foundry returns that number as the plain value of the metric, and the rest of the result as the evaluator did."""
    return lambda score, item: number if item["result_part"] == "value" else score


def failing():
    return FakeFoundryProject(alter_item=one_outcome_flipped)


def test_a_customer_process_rescore_scores_the_evidence_of_a_failed_managed_run_in_this_process():
    earlier = sweep(TWO_TASKS, failing())

    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])

    rescore = metric(rescored)["customer_process_rescore"]
    assert rescore["state"] == "available"
    assert rescore["value"] == pytest.approx(0.68, abs=1e-12)
    assert rescore["scoring_path"] == "customer_process"
    assert rescore["fidelity"] == "authoritative"


def test_the_managed_failure_is_retained_beside_the_rescore_and_the_scorecard_stays_partial():
    earlier = sweep(TWO_TASKS, failing())

    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])

    outcome_consistency = metric(rescored)
    # Everything Foundry-managed scoring left is as it was: no value, the failure and its execution error.
    assert {key: value for key, value in outcome_consistency.items() if key != "customer_process_rescore"} == metric(
        earlier
    )
    assert (outcome_consistency["state"], outcome_consistency["value"]) == (None, None)
    assert outcome_consistency["scoring_path"] == "foundry_managed"
    assert "ended as failed" in outcome_consistency["managed_scoring"]["error"]
    assert rescored["status"] == "partial"
    assert rescored["missing"] == earlier["missing"]
    assert [(entry["kind"], entry["metric_id"]) for entry in rescored["missing"]] == [
        ("metric_result", "outcome_consistency")
    ]


def test_an_unverified_managed_value_and_its_limitation_are_retained_beside_the_rescore():
    # The project returns 0.25. This process calculates 0.68 from the same evidence. Both are reported.
    earlier = sweep(TWO_TASKS, FakeFoundryProject(alter_score=as_the_plain_value(0.25)))

    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])

    outcome_consistency = metric(rescored)
    assert outcome_consistency["value"] == 0.25
    assert outcome_consistency["scoring_path"] == "foundry_managed"
    assert outcome_consistency["fidelity"] == "unverified"
    assert "has not been established" in outcome_consistency["limitation"]
    rescore = outcome_consistency["customer_process_rescore"]
    assert rescore["value"] == pytest.approx(0.68, abs=1e-12)
    assert rescore["scoring_path"] == "customer_process"
    assert rescore["fidelity"] == "authoritative"
    assert "limitation" not in rescore and "managed_scoring" not in rescore
    assert rescored["status"] == "complete"


def test_the_rescore_says_what_it_is_and_reports_what_a_result_in_this_process_reports():
    earlier = sweep(TWO_TASKS, failing())

    rescore = metric(rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"]))[
        "customer_process_rescore"
    ]

    assert "the customer asked" in rescore["meaning"]
    assert "It is not the Foundry-managed result of the metric and does not replace it" in rescore["meaning"]
    # What Foundry returns none of: the counts, the details and the Attempts the value rests on.
    assert (rescore["counts"]["tasks"]["total"], rescore["counts"]["tasks"]["valid"]) == (2, 2)
    assert (rescore["counts"]["attempts"]["total"], rescore["counts"]["attempts"]["valid"]) == (8, 8)
    assert rescore["evidence"]["attempt_ids"] == metric(earlier)["attempts_used"]["attempt_ids"]
    assert rescore["definition"] == metric(earlier)["definition"]
    assert rescore["execution_failure"] is None


def test_the_rescore_is_of_the_evidence_that_was_submitted_to_foundry():
    foundry = failing()
    earlier = sweep(TWO_TASKS, foundry)

    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])

    submitted, _, _ = foundry.submitted_items[0]["outcome_consistency_evidence"].partition(":")
    assert metric(rescored)["managed_scoring"]["evidence_sha256"] == submitted
    assert metric(rescored)["customer_process_rescore"]["evidence_sha256"] == submitted


@pytest.mark.parametrize("foundry", [failing(), FakeFoundryProject()], ids=["failed managed run", "managed value"])
def test_a_sweep_never_rescores_by_itself(foundry):
    scorecard = sweep(TWO_TASKS, foundry, metrics=[MANAGED, "resource_consistency"])

    assert all("customer_process_rescore" not in entry for entry in scorecard["metrics"])
    assert metric(scorecard)["scoring_path"] == "foundry_managed"


def test_the_earlier_scorecard_is_not_changed():
    earlier = sweep(TWO_TASKS, failing())
    as_it_was = copy.deepcopy(earlier)

    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])

    assert earlier == as_it_was
    assert rescored["attempts"] == earlier["attempts"]
    assert json.loads(to_json(rescored)) == rescored


AT_LEAST_HALF = AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0.5}})


def test_acceptance_is_assessed_on_the_rescore_apart_from_the_managed_result_which_stays_not_assessed():
    # The project returns 0.9, which would meet the criterion. It is unverified, so it is not assessed.
    # The rescore is 0.68, which is at least 0.5, and is assessed as its own result.
    earlier = sweep(
        TWO_TASKS, FakeFoundryProject(alter_score=as_the_plain_value(0.9)), acceptance_criteria=[AT_LEAST_HALF]
    )

    rescored = rescore_in_customer_process(
        earlier_scorecard=earlier, metrics=["outcome_consistency"], acceptance_criteria=[AT_LEAST_HALF]
    )

    outcome_consistency = metric(rescored)
    assert outcome_consistency["acceptance"] == metric(earlier)["acceptance"]
    assert outcome_consistency["acceptance"]["status"] == "not_assessed"
    assert "unverified result" in outcome_consistency["acceptance"]["reason"]
    of_the_rescore = outcome_consistency["customer_process_rescore"]["acceptance"]
    assert of_the_rescore["status"] == "met"
    assert of_the_rescore["checks"] == [
        {"subject": "value", "comparison": "at_least", "threshold": 0.5, "observed": pytest.approx(0.68, abs=1e-12), "met": True}
    ]


def test_a_rescore_that_does_not_meet_the_criterion_does_not_change_the_assessment_of_the_managed_failure():
    strict = AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0.9}})
    earlier = sweep(TWO_TASKS, failing(), acceptance_criteria=[strict])

    rescored = rescore_in_customer_process(
        earlier_scorecard=earlier, metrics=["outcome_consistency"], acceptance_criteria=[strict]
    )

    outcome_consistency = metric(rescored)
    assert outcome_consistency["acceptance"]["status"] == "not_assessed"
    assert outcome_consistency["acceptance"]["reason"].startswith(
        "the metric has no result because of an execution failure: Foundry-managed scoring gave no result"
    )
    assert outcome_consistency["customer_process_rescore"]["acceptance"]["status"] == "not_met"


def test_a_rescore_without_a_criterion_has_no_acceptance_assessment():
    earlier = sweep(TWO_TASKS, failing())

    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])

    assert metric(rescored)["customer_process_rescore"]["acceptance"] is None


def test_an_unavailable_measurement_in_this_process_is_reported_as_one_beside_the_managed_one():
    # One Attempt at each Task. The Evaluator says what it found, in Foundry and in this process:
    # no Task has two Attempts with a task outcome.
    earlier = sweep({"close": ["success"], "archive": ["failure"]}, FakeFoundryProject())

    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])

    rescore = metric(rescored)["customer_process_rescore"]
    assert rescore["state"] == "unavailable"
    assert rescore["value"] is None
    assert rescore["reason"] == "no Task has at least two Attempts with a task outcome"
    assert rescore["scoring_path"] == "customer_process"
    assert (metric(rescored)["state"], metric(rescored)["reason"]) == ("unavailable", rescore["reason"])
    assert metric(rescored)["scoring_path"] == "foundry_managed"
    assert metric(rescored)["counts"] == rescore["counts"]


def test_a_rescore_of_attempts_that_were_cut_short_has_an_execution_failure_as_in_a_sweep():
    # Every invocation fails, so no Attempt completed. Too little was collected for a calculation, in Foundry and here.
    earlier = sweep({"close": ["error", "error"]}, FakeFoundryProject())

    rescore = metric(rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"]))[
        "customer_process_rescore"
    ]

    assert rescore["state"] is None
    assert rescore["execution_failure"]["experiment_ids"] == ["close"]
    assert rescore["execution_failure"]["task_ids"] == []
    assert rescore["execution_failure"]["reason"].startswith("the Attempts that supply this metric's evidence were cut short")


# close: the successful Attempt takes 1 second and the failed one 3, both with 2 tool calls.
#        Elapsed time: mean 2, population standard deviation 1, CV 0.5. Tool calls: CV 0.
#        Mean of the CVs 0.25, Task value exp(-0.25) = 0.7788007830714049.
# archive: three Attempts of 4 seconds and 1 tool call each. Both CVs 0, Task value 1.
# Resource consistency: (0.7788007830714049 + 1) / 2 = 0.8894003915357025.
MEASURED = {"close": ["success", "failure"], "archive": ["success", "success", "success"]}
RESOURCES = {
    "close": [{"seconds": 1.0, "tool_calls": 2}, {"seconds": 3.0, "tool_calls": 2}],
    "archive": [{"seconds": 4.0, "tool_calls": 1}] * 3,
}
BOTH_MANAGED = [MANAGED, ConfiguredEvaluator(metric_id="resource_consistency", scoring_path="foundry_managed")]


def test_only_the_metrics_the_customer_names_are_rescored():
    # Both managed runs fail: every Evaluator is handed altered evidence.
    def altered(item):
        return altered_evidence(item, '"task_id":"close"', '"task_id":"other"')

    earlier = sweep(MEASURED, FakeFoundryProject(alter_item=altered), metrics=BOTH_MANAGED, resources=RESOURCES)

    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["resource_consistency"])

    assert metric(rescored, "resource_consistency")["customer_process_rescore"]["value"] == pytest.approx(
        0.8894003915357025, abs=1e-12
    )
    assert metric(rescored) == metric(earlier)
    assert "customer_process_rescore" not in metric(rescored)
    # A second request rescores the other metric, and keeps the first rescore as it is.
    both = rescore_in_customer_process(earlier_scorecard=rescored, metrics=[MANAGED])
    assert metric(both)["customer_process_rescore"]["value"] == pytest.approx(0.5, abs=1e-12)
    assert metric(both, "resource_consistency") == metric(rescored, "resource_consistency")


# Requests that are refused. Each gives no scorecard.


def an_outcome_changed(scorecard):
    scorecard["attempts"][0]["task_outcome"]["success"] = False


def an_attempt_removed(scorecard):
    del scorecard["attempts"][0]


def an_attempt_that_is_not_valid_evidence(scorecard):
    scorecard["attempts"][0]["task_outcome"]["success"] = "yes"


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            an_outcome_changed,
            "the supplied evidence is not the evidence that was submitted to Foundry for 'outcome_consistency'",
        ),
        (an_attempt_removed, "the supplied evidence does not validate.*'close/baseline/attempt-1', which is not in the attempt-level"),
        (an_attempt_that_is_not_valid_evidence, "the supplied evidence does not validate"),
    ],
)
def test_a_rescore_of_other_evidence_than_the_sweep_collected_is_refused(change, problem):
    earlier = sweep(TWO_TASKS, failing())
    change(earlier)

    with pytest.raises(RescoreFailed, match=f"customer-process rescore failed: .*{problem}"):
        rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])


@pytest.mark.parametrize(
    ("metrics", "problem"),
    [
        ([], "no metric was named"),
        ("outcome_consistency", "named as a sequence of metric identities"),
        (["outcome_consistency", MANAGED], "'outcome_consistency' is named more than once"),
        (
            ["resource_consistency"],
            "'resource_consistency' was scored on the scoring path 'customer_process' in the earlier scorecard",
        ),
        (["calibration"], "'calibration' was not selected in the earlier scorecard"),
        (["consistency"], "'consistency' is not a reliability metric of the earlier scorecard"),
    ],
)
def test_a_request_that_names_no_foundry_managed_result_is_refused(metrics, problem):
    earlier = sweep(TWO_TASKS, failing(), metrics=[MANAGED, "resource_consistency"])

    with pytest.raises(RescoreFailed, match=problem):
        rescore_in_customer_process(earlier_scorecard=earlier, metrics=metrics)


def test_a_rescore_that_is_there_is_not_replaced_by_another():
    earlier = sweep(TWO_TASKS, failing())
    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])

    with pytest.raises(RescoreFailed, match="'outcome_consistency' already has a customer-process rescore"):
        rescore_in_customer_process(earlier_scorecard=rescored, metrics=["outcome_consistency"])


def test_an_acceptance_criterion_of_the_earlier_scorecard_has_to_be_declared_again():
    earlier = sweep(TWO_TASKS, failing(), acceptance_criteria=[AT_LEAST_HALF])

    with pytest.raises(RescoreFailed, match="assessed an acceptance criterion for 'outcome_consistency'"):
        rescore_in_customer_process(earlier_scorecard=earlier, metrics=["outcome_consistency"])


# A recalculation is not a rescore.


CORRECTED = OutcomeCorrection(
    attempt_id="close/baseline/attempt-5",
    task_outcome=TaskOutcome(success=True, source="label added after the run"),
    provenance="synthetic correction",
)


@pytest.mark.parametrize("rescore_first", [False, True], ids=["managed failure", "managed failure with a rescore"])
def test_a_recalculation_carries_a_managed_failure_and_its_rescore_and_scores_neither_in_this_process(rescore_first):
    # The managed run failed, so the metric has no result. A corrected outcome does not give it one: the
    # failure is carried, with the rescore of the earlier evidence if there is one, and marked as not recalculated.
    scorecard = sweep(TWO_TASKS, failing())
    if rescore_first:
        scorecard = rescore_in_customer_process(earlier_scorecard=scorecard, metrics=["outcome_consistency"])
    was = metric(scorecard)

    recalculated = recalculate(earlier_scorecard=scorecard, outcome_corrections=[CORRECTED])

    now = metric(recalculated)
    assert now["state"] is None and now["value"] is None
    assert now["execution_failure"] == was["execution_failure"]
    assert now.get("customer_process_rescore") == was.get("customer_process_rescore")
    assert now["not_recalculated"]["corrected_attempt_ids"] == ["close/baseline/attempt-5"]
    assert recalculated["status"] == "partial"
    assert recalculated["missing"] == scorecard["missing"]
    assert recalculated["token_usage"] == scorecard["token_usage"]


# The human-readable report, as a rendering of the scorecard.


def test_the_report_shows_the_rescore_as_a_result_of_its_own_beside_the_managed_failure():
    rescored = rescore_in_customer_process(earlier_scorecard=sweep(TWO_TASKS, failing()), metrics=["outcome_consistency"])
    position = [entry["metric_id"] for entry in rescored["metrics"]].index("outcome_consistency")
    managed, rescore = f"/metrics/{position}", f"/metrics/{position}/customer_process_rescore"

    report = assert_faithful_rendering(render_report(rescored), rescored)

    # Both results, each with its scoring path.
    assert report.value(f"{managed}/scoring_path") == "foundry_managed"
    assert report.value(f"{managed}/state") == "\N{EM DASH}"
    assert report.value(f"{rescore}/scoring_path") == "customer_process"
    assert report.value(f"{rescore}/state") == "available"
    assert report.value(f"{rescore}/value") == repr(metric(rescored)["customer_process_rescore"]["value"])
    # Each in a block of its own, so that neither reads as the other.
    assert f"metric-{position}" in report.inside[f"{managed}/scoring_path"]
    assert f"metric-{position}-rescore" not in report.inside[f"{managed}/scoring_path"]
    assert f"metric-{position}-rescore" in report.inside[f"{rescore}/value"]
    assert f"metric-{position}" not in report.inside[f"{rescore}/value"]
    of_the_managed, of_the_rescore = (
        report.article_wording[f"metric-{position}"],
        report.article_wording[f"metric-{position}-rescore"],
    )
    assert "Managed scoring failure" in of_the_managed and "Customer-process rescore" not in of_the_managed
    assert "Customer-process rescore" in of_the_rescore and "Managed scoring failure" not in of_the_rescore
    assert "not the Foundry-managed result" in of_the_rescore
    assert report.article_classes[f"metric-{position}"] != report.article_classes[f"metric-{position}-rescore"]
    # The scorecard is still partial, and the report still says what is missing.
    assert report.value("/status") == "partial"
    assert report.value("/missing/0/metric_id") == "outcome_consistency"
    assert "customer-process rescore" in report.wording.split("Metrics")[0]


def test_the_report_of_a_sweep_says_nothing_of_a_rescore():
    scorecard = sweep(TWO_TASKS, failing())

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert "rescore" not in report.wording.split("How to read this report")[0]

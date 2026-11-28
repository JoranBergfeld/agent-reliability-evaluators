"""Evaluator sweep seam: the fidelity check of a result recovered from Foundry-managed scoring.

A managed result comes back on two channels: the plain value, which Foundry returns as the score
of one row, and the encoded result, which carries the value as the Evaluator computed it together
with the state, the reason and the counts. The sweep is driven through its public entry point
with a fake agent target and a fake Foundry project that is scripted to return another score than
the evaluator returned. Every assertion is on the machine-readable scorecard. Expected values
are calculated by hand.
"""

import math

import pytest

from reliability_evaluators.evaluators import MetricResult, outcome_consistency
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring, ManagedResult
from reliability_evaluators.scorecard import AcceptanceCriterion, render_report
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, run_sweep
from tests.fake_foundry import FakeFoundryProject
from tests.fakes import FakeAgentTarget
from tests.report_reader import assert_faithful_rendering
from tests.fakes import fake_baseline_experiments

SOURCE = "final state of the fake service"

FOUR_IN_FIVE = ["success", "success", "success", "success", "failure"]
ALWAYS_FAILS = ["failure", "failure", "failure"]
# close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Outcome consistency: (0.36 + 1) / 2 = 0.68.
TWO_TASKS = {"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS}
# One Attempt at each Task: no Task has two, so outcome consistency is an unavailable measurement.
ONE_ATTEMPT_EACH = {"close": ["success"], "archive": ["failure"]}

MANAGED = ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")


def sweep(script, foundry=None, managed_scoring=None, **declared):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = FakeAgentTarget(script)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    return run_sweep(
        experiments=fake_baseline_experiments(tasks),
        agent_target=target,
        metrics=[MANAGED],
        managed_scoring=managed_scoring or FoundryManagedScoring(project=foundry, poll_interval_seconds=0),
        **declared,
    )


def metric(scorecard):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == "outcome_consistency"]
    return entry


def plain_value(altered):
    """Foundry returns another number as the plain value, and every part of the encoded result as it was."""
    return lambda score, item: altered(score) if item["result_part"] == "value" else score


def test_a_value_that_came_back_the_same_on_both_channels_is_authoritative():
    outcome_consistency = metric(sweep(TWO_TASKS, FakeFoundryProject()))

    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)
    assert outcome_consistency["fidelity"] == "authoritative"
    assert "limitation" not in outcome_consistency


# close: 4 successes in 5, (8 - 5)^2 / 25 = 9/25 = 0.36. It is the only Task, so outcome consistency is 0.36.
ONE_TASK = {"close": FOUR_IN_FIVE}


def test_a_value_that_differs_between_the_two_channels_is_an_unverified_result():
    # The Evaluator computed 0.36 and encoded it. Foundry returns 0.25 as the score.
    foundry = FakeFoundryProject(alter_score=plain_value(lambda score: 0.25))

    outcome_consistency = metric(sweep(ONE_TASK, foundry))

    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["fidelity"] == "unverified"
    # The value is kept as Foundry returned it, as a diagnostic. It is not replaced by the encoded one.
    assert outcome_consistency["value"] == 0.25
    assert outcome_consistency["limitation"] == (
        "Foundry returned 0.25 as the value of this metric, and the encoded result that the Evaluator returned "
        "from Foundry with it holds 0.36. The two do not agree, so that the reported value equals the value the "
        "Evaluator computed there has not been established. It is reported as Foundry returned it, as an "
        "unverified result: it cannot be met or not met by an acceptance criterion. The evidence counts, the "
        "details and the Attempts are the ones in the encoded result, and belong to the value it holds."
    )


@pytest.mark.parametrize(
    ("condition", "why_it_matters"),
    [
        ({"value": {"at_least": 0.5}}, "0.9, which Foundry returned, would meet it, and the encoded 0.36 would not"),
        ({"value": {"at_most": 0.5}}, "the encoded 0.36 would meet it, and 0.9, which Foundry returned, would not"),
    ],
)
def test_a_mismatch_cannot_support_a_definitive_acceptance_verdict(condition, why_it_matters):
    # The Evaluator computed 0.36 and encoded it. Foundry returns 0.9 as the score.
    foundry = FakeFoundryProject(alter_score=plain_value(lambda score: 0.9))
    criterion = AcceptanceCriterion(metric_id="outcome_consistency", condition=condition)

    outcome_consistency = metric(sweep(ONE_TASK, foundry, acceptance_criteria=[criterion]))

    assert (outcome_consistency["value"], outcome_consistency["fidelity"]) == (0.9, "unverified")
    # Neither number decides: the criterion is not assessed, and nothing was compared with it.
    assert outcome_consistency["acceptance"]["status"] == "not_assessed"
    assert outcome_consistency["acceptance"]["reason"] == (
        "the value is an unverified result: its fidelity to the evaluator-computed value has not been established"
    )
    assert outcome_consistency["acceptance"]["assessed_by"] is None
    assert outcome_consistency["acceptance"]["checks"] == []


@pytest.mark.parametrize(("threshold", "status"), [(0.3, "met"), (0.4, "not_met")])
def test_an_authoritative_managed_value_is_met_or_not_met_by_an_acceptance_criterion(threshold, status):
    # 0.36 came back the same on both channels. It is at least 0.3, and it is not at least 0.4.
    criterion = AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": threshold}})

    outcome_consistency = metric(sweep(ONE_TASK, FakeFoundryProject(), acceptance_criteria=[criterion]))

    assert outcome_consistency["scoring_path"] == "foundry_managed"
    assert outcome_consistency["acceptance"]["status"] == status
    assert outcome_consistency["acceptance"]["assessed_by"] == "offering"
    assert outcome_consistency["acceptance"]["checks"] == [
        {"subject": "value", "comparison": "at_least", "threshold": threshold, "observed": 0.36, "met": status == "met"}
    ]


def test_the_scorecard_records_the_check_that_made_a_value_authoritative_and_what_it_establishes():
    check = metric(sweep(ONE_TASK, FakeFoundryProject()))["managed_scoring"]["fidelity_check"]

    assert {key: check[key] for key in ("outcome", "plain_value", "encoded_value")} == {
        "outcome": "passed",
        "plain_value": 0.36,
        "encoded_value": 0.36,
    }
    assert check["meaning"] == (
        "The number Foundry returned as the score equals the value in the encoded result, which the Evaluator "
        "wrote in Foundry and which matched its digest. So the reported value is the one the Evaluator returned "
        "in Foundry for the submitted evidence, unchanged on the way back. This does not show that the "
        "calculation is right, and nothing was calculated in the customer's own process to compare it with."
    )
    assert set(check) == {"outcome", "plain_value", "encoded_value", "meaning"}


def test_the_scorecard_records_both_numbers_of_a_check_that_failed():
    foundry = FakeFoundryProject(alter_score=plain_value(lambda score: 0.25))

    check = metric(sweep(ONE_TASK, foundry))["managed_scoring"]["fidelity_check"]

    assert {key: check[key] for key in ("outcome", "plain_value", "encoded_value")} == {
        "outcome": "failed",
        "plain_value": 0.25,
        "encoded_value": 0.36,
    }
    assert check["meaning"] == (
        "The number Foundry returned as the score differs from the value in the encoded result, which the "
        "Evaluator wrote in Foundry and which matched its digest. Which of the two the Evaluator computed is "
        "not established, and nothing was calculated in the customer's own process to decide between them."
    )


def test_a_value_that_differs_in_its_last_bit_is_a_mismatch():
    # 0.36 and the double directly above it are adjacent distinct values. Nothing is rounded to make them agree.
    above = math.nextafter(0.36, 1.0)
    foundry = FakeFoundryProject(alter_score=plain_value(lambda score: above))

    outcome_consistency = metric(sweep(ONE_TASK, foundry))

    assert above != 0.36
    assert outcome_consistency["fidelity"] == "unverified"
    check = outcome_consistency["managed_scoring"]["fidelity_check"]
    assert (check["outcome"], check["plain_value"], check["encoded_value"]) == ("failed", above, 0.36)
    assert outcome_consistency["value"] == above


def test_a_whole_number_that_foundry_returns_without_a_fraction_is_the_same_value():
    # Both Tasks always give the same outcome, so the value is 1. Foundry returns it as the integer 1.
    foundry = FakeFoundryProject()

    outcome_consistency = metric(sweep({"close": ["success"] * 3, "archive": ALWAYS_FAILS}, foundry))

    returned = [
        result["score"]
        for item in foundry.output_items()
        if item["datasource_item"]["result_part"] == "value"
        for result in item["results"]
    ]
    assert returned == [1] and type(returned[0]) is int
    assert (outcome_consistency["value"], outcome_consistency["fidelity"]) == (1.0, "authoritative")


def test_an_unavailable_measurement_that_both_channels_agree_on_is_authoritative():
    outcome_consistency = metric(sweep(ONE_ATTEMPT_EACH, FakeFoundryProject()))

    assert (outcome_consistency["state"], outcome_consistency["value"]) == ("unavailable", None)
    assert outcome_consistency["reason"] == "no Task has at least two Attempts with a task outcome"
    assert outcome_consistency["fidelity"] == "authoritative"
    assert "limitation" not in outcome_consistency
    # There is no value to compare. What the check compared is whether there is one.
    assert outcome_consistency["managed_scoring"]["fidelity_check"] == {
        "outcome": "passed",
        "plain_value": None,
        "encoded_value": None,
        "meaning": (
            "Foundry returned the marker of a metric without a value as the score, and the encoded result, which "
            "the Evaluator wrote in Foundry and which matched its digest, says that the metric has no value. So "
            "the two agree that there is none. The state, the reason and the evidence counts came back in the "
            "encoded result only: no second channel carries them."
        ),
    }


class ManagedScoringThatDoesNotCheck:
    """Stands in for Foundry-managed scoring, and hands the sweep a value that no fidelity check was made for.

    Nothing in this version recovers a result without checking it. The sweep is still not to
    take such a result for authoritative.
    """

    def register(self, metric_ids):
        return {}

    def score(self, evidence, declared=None):
        result = MetricResult.available(outcome_consistency.DEFINITION, 0.36)
        return {"outcome_consistency": ManagedResult(result=result, provenance={"eval_id": "eval_1"})}


def test_a_managed_value_that_was_not_checked_is_an_unverified_result():
    # 0.36 is at least 0.3, and the criterion is still not assessed.
    criterion = AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0.3}})

    scorecard = sweep(ONE_TASK, managed_scoring=ManagedScoringThatDoesNotCheck(), acceptance_criteria=[criterion])

    outcome_consistency = metric(scorecard)
    assert (outcome_consistency["value"], outcome_consistency["fidelity"]) == (0.36, "unverified")
    assert outcome_consistency["limitation"] == (
        "Foundry returned this value, and it was not compared with the value in an encoded result. That it equals "
        "the value the Evaluator computed there has not been established, so it is an unverified result: it "
        "cannot be met or not met by an acceptance criterion."
    )
    assert outcome_consistency["acceptance"]["status"] == "not_assessed"


def test_the_report_shows_what_made_a_managed_result_authoritative():
    scorecard = sweep(ONE_TASK, FakeFoundryProject())
    position = [entry["metric_id"] for entry in scorecard["metrics"]].index("outcome_consistency")

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert report.value(f"/metrics/{position}/scoring_path") == "foundry_managed"
    assert report.value(f"/metrics/{position}/fidelity") == "authoritative"
    check = metric(scorecard)["managed_scoring"]["fidelity_check"]
    assert report.value(f"/metrics/{position}/managed_scoring/fidelity_check/outcome") == "passed"
    assert report.value(f"/metrics/{position}/managed_scoring/fidelity_check/meaning") == check["meaning"]

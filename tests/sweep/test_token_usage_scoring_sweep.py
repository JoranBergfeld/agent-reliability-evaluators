"""Evaluator sweep seam: the token usage of scoring, in this process and by Foundry-managed scoring.

The sweep is driven through its public entry point with a fake agent target and a fake
Foundry project. Every assertion is on the machine-readable scorecard. The counts are those
of a saved managed run (decision ticket #24), and expected totals are added by hand.

Foundry-managed scoring makes two runs for a metric: one of two rows, for the plain value
and the size of the encoded result, and one with a row for each part of the encoded result.
Each run is a use of its own.
"""

import json

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, rescore_in_customer_process, run_sweep
from tests.fake_foundry import FakeFoundryProject
from tests.fakes import FakeAgentTarget
from tests.managed_artifact import altered_evidence

SOURCE = "final state of the fake service"
OUTCOME = "outcome_consistency"
MANAGED = ConfiguredEvaluator(metric_id=OUTCOME, scoring_path="foundry_managed")

# What Foundry reported on the result of a criterion that used a model, in a saved run.
ON_A_RESULT = {"prompt_tokens": 1862, "completion_tokens": 114, "cached_tokens": 0, "total_tokens": 1976}
NO_USAGE = (
    "Foundry reported no usage for the managed run: not on the result of every row, and no per-model usage "
    "row. That is not a report of zero"
)


def row(model_name, total_tokens, prompt_tokens, completion_tokens):
    """One row of the per-model usage of a run."""
    return {
        "model_name": model_name,
        "invocation_count": 1,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": 0,
    }


# The saved run reported the same counts twice: under the name of a model and under the name of a category.
MODEL_ROW = row("gpt-4.1-mini-2025-04-14", 1976, 1862, 114)
CATEGORY_ROW = row("azure_ai_evaluation", 1976, 1862, 114)


def sweep(foundry, metrics=(MANAGED,)):
    """Two Attempts of one Task, 790 and 812 tokens, scored as `metrics` say."""
    target = FakeAgentTarget(
        {"close": ["success", "success"]},
        invocations={
            f"close/attempt-{number}": {"token_usage": {"total_tokens": total_tokens}}
            for number, total_tokens in ((1, 790), (2, 812))
        },
    )
    task = Task(
        task_id="close",
        prompt="Close the item.",
        success_rule=lambda attempt, response: TaskOutcome(success=True, source=SOURCE),
        attempts=2,
    )
    managed_scoring = None if foundry is None else FoundryManagedScoring(project=foundry, poll_interval_seconds=0)
    return run_sweep(tasks=[task], agent_target=target, metrics=metrics, managed_scoring=managed_scoring)


def usage_of(scorecard):
    """The sweep total, the total of the one Evaluator, and what each of its scoring uses was."""
    (evaluator,) = scorecard["token_usage"]["evaluators"]
    scoring = [(use.get("run_id"), use["basis"], use["total_tokens"]) for use in evaluator["scoring"]]
    return scorecard["token_usage"]["sweep"], evaluator["total"], scoring


def known(total):
    return total["coverage"], total["total_tokens"]


def test_scoring_by_code_in_the_customer_process_is_zero_because_zero_is_known():
    # 790 + 812 = 1602 tokens for the Attempts, and none for the scoring.
    scorecard = sweep(None, metrics=(OUTCOME,))

    sweep_total, evaluator_total, scoring = usage_of(scorecard)
    assert scoring == [(None, "no_model_use", 0)]
    assert scorecard["token_usage"]["evaluators"][0]["scoring"] == [
        {
            "basis": "no_model_use",
            "total_tokens": 0,
            "reason": "the Evaluator is calculated by code in the customer's process, which uses no model",
            "scoring_path": "customer_process",
        }
    ]
    assert known(evaluator_total) == ("complete", 1602)
    assert known(sweep_total) == ("complete", 1602)


def test_managed_scoring_that_reports_no_usage_is_missing_and_not_zero():
    # A run of a code evaluator was seen to report an empty list of per-model usage and no usage on
    # its results. That is not a report of zero: 1602 is what is known, and it is a partial sum.
    scorecard = sweep(FakeFoundryProject())

    sweep_total, evaluator_total, scoring = usage_of(scorecard)
    assert scoring == [("evalrun_1", "missing", None), ("evalrun_2", "missing", None)]
    assert scorecard["token_usage"]["evaluators"][0]["scoring"][0] == {
        "basis": "missing",
        "total_tokens": None,
        "reason": NO_USAGE,
        "scoring_path": "foundry_managed",
        "eval_id": "eval_1",
        "run_id": "evalrun_1",
        "source": None,
        # The first run has two rows: one for the plain value and one for the size of the encoded result.
        "reported": {"run_id": "evalrun_1", "results_sample_usage": [None, None], "per_model_usage": []},
    }
    assert known(evaluator_total) == ("partial", 1602)
    assert known(sweep_total) == ("partial", 1602)
    assert sweep_total["missing"] == [
        {"use": "scoring", "metric_id": OUTCOME, "run_id": "evalrun_1", "reason": NO_USAGE},
        {"use": "scoring", "metric_id": OUTCOME, "run_id": "evalrun_2", "reason": NO_USAGE},
    ]
    # The metric is as Foundry returned it, whatever was reported of what scoring it used.
    assert scorecard["metrics"][0]["value"] == 1


def test_scoring_usage_that_foundry_reports_on_the_results_of_a_run_is_counted_once():
    # The first run has two rows, and the result of each reports 1976 tokens: 3952 for the run. The run
    # repeats that total in two per-model usage rows, which are another view of it and are not added.
    # The second run reports nothing. Known: 790 + 812 + 3952 = 5554 tokens, a partial sum.
    first_run = [row("gpt-4.1-mini-2025-04-14", 3952, 3724, 228), row("azure_ai_evaluation", 3952, 3724, 228)]
    foundry = FakeFoundryProject(
        result_usage=lambda item: ON_A_RESULT if item["result_part"] in ("value", "size") else None,
        per_model_usage=lambda run: first_run if run == 1 else [],
    )

    scorecard = sweep(foundry)

    sweep_total, evaluator_total, scoring = usage_of(scorecard)
    assert scoring == [("evalrun_1", "reported", 3952), ("evalrun_2", "missing", None)]
    of_first_run = scorecard["token_usage"]["evaluators"][0]["scoring"][0]
    assert of_first_run["source"] == (
        "the usage Foundry reported on the result of this Evaluator for every row of the managed run"
    )
    assert of_first_run["reported"] == {
        "run_id": "evalrun_1",
        "results_sample_usage": [ON_A_RESULT, ON_A_RESULT],
        "per_model_usage": first_run,
    }
    assert known(evaluator_total) == ("partial", 5554)
    assert known(sweep_total) == ("partial", 5554)


def test_per_model_usage_rows_that_repeat_the_same_counts_are_not_summed_twice():
    # Each of the two runs reports 1976 tokens under the name of a model and again under the name of a
    # category. That is one use of 1976 per run: 790 + 812 + 1976 + 1976 = 5554 tokens.
    # Summing every row would give 4 * 1976 = 7904 for the scoring alone.
    foundry = FakeFoundryProject(per_model_usage=[MODEL_ROW, CATEGORY_ROW])

    scorecard = sweep(foundry)

    sweep_total, evaluator_total, scoring = usage_of(scorecard)
    assert scoring == [("evalrun_1", "reported", 1976), ("evalrun_2", "reported", 1976)]
    assert scorecard["token_usage"]["evaluators"][0]["scoring"][0]["source"] == (
        "the per-model usage Foundry reported for the managed run. Rows that repeat the same counts under "
        "another name are counted once"
    )
    assert known(evaluator_total) == ("complete", 5554)
    assert known(sweep_total) == ("complete", 5554)


def test_per_model_usage_rows_with_different_counts_are_listed_and_not_summed():
    # A saved run reported two model rows and a category row equal to their sum. Whether rows overlap
    # is not documented, so no count is made of rows that differ: summing these would double it.
    # The first run reports 1976 once. Known: 790 + 812 + 1976 = 3578 tokens, a partial sum.
    differing = [
        row("gpt-4.1-mini-2025-04-14", 1200, 1100, 100),
        row("gpt-4.1-2025-04-14", 776, 762, 14),
        row("azure_ai_evaluation", 1976, 1862, 114),
    ]
    foundry = FakeFoundryProject(per_model_usage=lambda run: [MODEL_ROW, CATEGORY_ROW] if run == 1 else differing)

    scorecard = sweep(foundry)

    sweep_total, evaluator_total, scoring = usage_of(scorecard)
    assert scoring == [("evalrun_1", "reported", 1976), ("evalrun_2", "missing", None)]
    of_second_run = scorecard["token_usage"]["evaluators"][0]["scoring"][1]
    assert of_second_run["reason"] == (
        "Foundry reported per-model usage rows with different counts for the managed run. Whether such rows "
        "overlap is not documented, so they are listed as reported and are not summed"
    )
    assert of_second_run["reported"]["per_model_usage"] == differing
    assert known(evaluator_total) == ("partial", 3578)
    assert known(sweep_total) == ("partial", 3578)


def test_a_zero_that_foundry_reports_for_scoring_is_a_known_zero():
    zero = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 0}

    sweep_total, evaluator_total, scoring = usage_of(sweep(FakeFoundryProject(result_usage=zero)))

    assert scoring == [("evalrun_1", "reported", 0), ("evalrun_2", "reported", 0)]
    assert known(evaluator_total) == ("complete", 1602)
    assert known(sweep_total) == ("complete", 1602)


def one_outcome_flipped(item):
    """What a project that altered the submitted evidence would hand the evaluator, which then raises."""
    return altered_evidence(item, '"success":true', '"success":false')


def test_a_managed_run_that_gives_no_result_has_no_known_scoring_usage_either():
    scorecard = sweep(FakeFoundryProject(alter_item=one_outcome_flipped))

    sweep_total, evaluator_total, scoring = usage_of(scorecard)
    assert scorecard["metrics"][0]["state"] is None
    # The first run failed, so no second one was made.
    assert scoring == [("evalrun_1", "missing", None)]
    assert scorecard["token_usage"]["evaluators"][0]["scoring"][0]["reported"] == {
        "run_id": "evalrun_1",
        "results_sample_usage": None,
        "per_model_usage": [],
    }
    # What the Attempts used is known, whatever became of the scoring.
    assert known(evaluator_total) == ("partial", 1602)
    assert known(sweep_total) == ("partial", 1602)


def test_managed_scoring_that_stops_before_it_made_a_run_reports_no_usage():
    class Stops(FoundryManagedScoring):
        def score(self, evidence):
            raise RuntimeError("the project could not be reached")

    target = FakeAgentTarget({"close": ["success", "success"]})
    task = Task(
        task_id="close",
        prompt="Close the item.",
        success_rule=lambda attempt, response: TaskOutcome(success=True, source=SOURCE),
        attempts=2,
    )
    scorecard = run_sweep(
        tasks=[task], agent_target=target, metrics=[MANAGED], managed_scoring=Stops(project=FakeFoundryProject())
    )

    sweep_total, _, scoring = usage_of(scorecard)
    assert scoring == [(None, "missing", None)]
    (use,) = scorecard["token_usage"]["evaluators"][0]["scoring"]
    assert use["reason"] == "no managed run is known for this Evaluator, so no usage was reported for its scoring"
    assert use["reported"] is None
    # Nothing is known of the Attempts or of the scoring: there is no total, and it is not zero.
    assert known(sweep_total) == ("unavailable", None)


@pytest.mark.parametrize("unusable", [[{"model_name": "gpt-4.1-mini"}], [row("gpt-4.1-mini", "1976", 1862, 114)], "none"])
def test_per_model_usage_without_whole_number_totals_is_not_counted(unusable):
    scorecard = sweep(FakeFoundryProject(per_model_usage=unusable))

    _, _, scoring = usage_of(scorecard)
    assert scoring == [("evalrun_1", "missing", None), ("evalrun_2", "missing", None)]
    assert scorecard["token_usage"]["evaluators"][0]["scoring"][0]["reason"] == (
        "the per-model usage Foundry reported for the managed run is not a list of rows that each have a "
        "total_tokens that is a whole number"
    )


def test_a_customer_process_rescore_is_a_scoring_of_its_own_that_used_no_tokens():
    earlier = json.loads(to_json(sweep(FakeFoundryProject())))

    rescored = rescore_in_customer_process(earlier_scorecard=earlier, metrics=[OUTCOME])

    (before,) = earlier["token_usage"]["evaluators"]
    (after,) = rescored["token_usage"]["evaluators"]
    assert after["customer_process_rescore"] == {
        "basis": "no_model_use",
        "total_tokens": 0,
        "reason": "the Evaluator is calculated by code in the customer's process, which uses no model",
        "scoring_path": "customer_process",
    }
    # What the managed runs used is still not known, and the rescore does not stand in for it:
    # 790 + 812 = 1602 tokens is still a partial sum, for the Evaluator and for the sweep.
    assert {key: value for key, value in after.items() if key != "customer_process_rescore"} == before
    assert [use["basis"] for use in after["scoring"]] == ["missing", "missing"]
    assert known(after["total"]) == ("partial", 1602)
    assert rescored["token_usage"]["sweep"] == earlier["token_usage"]["sweep"]

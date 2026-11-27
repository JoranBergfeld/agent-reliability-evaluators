"""Evaluator sweep seam: the token usage of scoring, in this process and by Foundry-managed scoring.

The sweep is driven through its public entry point with a fake agent target and a fake
Foundry project. Every assertion is on the machine-readable scorecard. The counts are those
of a saved managed run (decision ticket #24), and expected totals are added by hand.
"""

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, run_sweep
from tests.fake_foundry import FakeFoundryProject
from tests.fakes import FakeAgentTarget

SOURCE = "final state of the fake service"
OUTCOME = "outcome_consistency"
MANAGED = ConfiguredEvaluator(metric_id=OUTCOME, scoring_path="foundry_managed")

# What Foundry reported on the result of a criterion that used a model, in a saved run.
ON_THE_RESULT = {"prompt_tokens": 1862, "completion_tokens": 114, "cached_tokens": 0, "total_tokens": 1976}


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
    (evaluator,) = scorecard["token_usage"]["evaluators"]
    return scorecard["token_usage"]["sweep"], evaluator


def test_scoring_by_code_in_the_customer_process_is_zero_because_zero_is_known():
    # 790 + 812 = 1602 tokens for the Attempts, and none for the scoring.
    total, evaluator = usage_of(sweep(None, metrics=(OUTCOME,)))

    assert evaluator["scoring"]["basis"] == "no_model_use"
    assert evaluator["scoring"]["total_tokens"] == 0
    assert (evaluator["total"]["coverage"], evaluator["total"]["total_tokens"]) == ("complete", 1602)
    assert (total["coverage"], total["total_tokens"]) == ("complete", 1602)


def test_managed_scoring_that_reports_no_usage_is_missing_and_not_zero():
    # A run of a code evaluator was observed to report an empty list of per-model usage and no usage
    # on its result. That is not a report of zero: 1602 is what is known, and it is a partial sum.
    scorecard = sweep(FakeFoundryProject())

    total, evaluator = usage_of(scorecard)
    assert evaluator["scoring"] == {
        "basis": "missing",
        "total_tokens": None,
        "reason": (
            "Foundry reported no usage for the managed run of this Evaluator: none on its result and no "
            "per-model usage row. That is not a report of zero"
        ),
        "scoring_path": "foundry_managed",
        "source": None,
        "eval_id": "eval_1",
        "run_id": "evalrun_1",
        "output_item_id": "1",
        "reported": {"result_sample_usage": None, "per_model_usage": []},
    }
    assert (evaluator["total"]["coverage"], evaluator["total"]["total_tokens"]) == ("partial", 1602)
    assert (total["coverage"], total["total_tokens"]) == ("partial", 1602)
    assert total["missing"] == [{"use": "scoring", "metric_id": OUTCOME, "reason": evaluator["scoring"]["reason"]}]
    # The metric is as Foundry returned it, whatever was reported of what scoring it used.
    assert scorecard["metrics"][0]["value"] == 1


def test_scoring_usage_that_foundry_reports_on_the_result_is_counted_once():
    # The result reports 1976 tokens, and the run repeats them in two per-model usage rows. They are
    # one use, not 3 * 1976: the Evaluator used 790 + 812 + 1976 = 3578 tokens.
    foundry = FakeFoundryProject(result_usage=ON_THE_RESULT, per_model_usage=[MODEL_ROW, CATEGORY_ROW])

    total, evaluator = usage_of(sweep(foundry))

    scoring = evaluator["scoring"]
    assert (scoring["basis"], scoring["total_tokens"]) == ("reported", 1976)
    assert scoring["source"] == "the usage Foundry reported on the result of this Evaluator in its managed run"
    assert scoring["reported"] == {"result_sample_usage": ON_THE_RESULT, "per_model_usage": [MODEL_ROW, CATEGORY_ROW]}
    assert (evaluator["total"]["coverage"], evaluator["total"]["total_tokens"]) == ("complete", 3578)
    assert (total["coverage"], total["total_tokens"]) == ("complete", 3578)


def test_per_model_usage_rows_that_repeat_the_same_counts_are_not_summed_twice():
    # Summing both rows would give 3952. The counts are the same under two names, so they are one use.
    foundry = FakeFoundryProject(per_model_usage=[MODEL_ROW, CATEGORY_ROW])

    total, evaluator = usage_of(sweep(foundry))

    scoring = evaluator["scoring"]
    assert (scoring["basis"], scoring["total_tokens"]) == ("reported", 1976)
    assert scoring["source"] == (
        "the per-model usage Foundry reported for the managed run of this Evaluator. Rows that repeat the "
        "same counts under another name are counted once"
    )
    assert total["total_tokens"] == 790 + 812 + 1976


def test_per_model_usage_rows_with_different_counts_are_listed_and_not_summed():
    # A saved run reported two model rows and a category row equal to their sum. Whether rows overlap
    # is not documented, so no total is made of rows that differ: summing these would double it.
    rows = [
        row("gpt-4.1-mini-2025-04-14", 1200, 1100, 100),
        row("gpt-4.1-2025-04-14", 776, 762, 14),
        row("azure_ai_evaluation", 1976, 1862, 114),
    ]

    total, evaluator = usage_of(sweep(FakeFoundryProject(per_model_usage=rows)))

    scoring = evaluator["scoring"]
    assert (scoring["basis"], scoring["total_tokens"]) == ("missing", None)
    assert scoring["reason"] == (
        "Foundry reported per-model usage rows with different counts for the managed run of this Evaluator. "
        "Whether such rows overlap is not documented, so they are listed as reported and are not summed"
    )
    assert scoring["reported"]["per_model_usage"] == rows
    assert (total["coverage"], total["total_tokens"]) == ("partial", 1602)


def test_a_zero_that_foundry_reports_for_scoring_is_a_known_zero():
    zero = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 0}

    total, evaluator = usage_of(sweep(FakeFoundryProject(result_usage=zero)))

    assert (evaluator["scoring"]["basis"], evaluator["scoring"]["total_tokens"]) == ("reported", 0)
    assert (total["coverage"], total["total_tokens"]) == ("complete", 1602)


def one_outcome_flipped(item):
    """The evidence as a project that altered it would hand it to the evaluator, which then raises."""
    return {field: text.replace("true", "false", 1) for field, text in item.items()}


def test_a_managed_run_that_gives_no_result_has_no_known_scoring_usage_either():
    scorecard = sweep(FakeFoundryProject(alter_item=one_outcome_flipped))

    total, evaluator = usage_of(scorecard)
    assert scorecard["metrics"][0]["state"] is None
    assert (evaluator["scoring"]["basis"], evaluator["scoring"]["total_tokens"]) == ("missing", None)
    assert (evaluator["scoring"]["eval_id"], evaluator["scoring"]["run_id"]) == ("eval_1", "evalrun_1")
    # What the Attempts used is known, whatever became of the scoring.
    assert (total["coverage"], total["total_tokens"]) == ("partial", 1602)


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

    total, evaluator = usage_of(scorecard)
    assert evaluator["scoring"]["reason"] == (
        "no managed run is known for this Evaluator, so no usage was reported for its scoring"
    )
    assert (evaluator["scoring"]["run_id"], evaluator["scoring"]["reported"]) == (None, None)
    # Nothing is known of the Attempts or of the scoring: there is no total, and it is not zero.
    assert (total["coverage"], total["total_tokens"]) == ("unavailable", None)


@pytest.mark.parametrize("unusable", [[{"model_name": "gpt-4.1-mini"}], [row("gpt-4.1-mini", "1976", 1862, 114)], "none"])
def test_per_model_usage_without_whole_number_totals_is_not_counted(unusable):
    _, evaluator = usage_of(sweep(FakeFoundryProject(per_model_usage=unusable)))

    assert (evaluator["scoring"]["basis"], evaluator["scoring"]["total_tokens"]) == ("missing", None)
    assert evaluator["scoring"]["reason"] == (
        "the per-model usage Foundry reported for the managed run of this Evaluator is not a list of rows "
        "that each have a total_tokens that is a whole number"
    )

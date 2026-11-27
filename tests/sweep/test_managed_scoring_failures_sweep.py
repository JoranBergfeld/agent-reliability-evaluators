"""Evaluator sweep seam: a Foundry-managed run that fails, or that gives only some of its results.

The sweep is driven through its public entry point with a fake agent target and a fake
Foundry project that is scripted to fail. Every assertion is on the machine-readable
scorecard. Expected values are calculated by hand.
"""

import re

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring, FoundryRequestError
from reliability_evaluators.scorecard import AcceptanceCriterion, render_report
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, run_sweep
from tests.fake_foundry import FakeFoundryProject
from tests.fakes import FakeAgentTarget
from tests.managed_artifact import altered_evidence
from tests.report_reader import assert_faithful_rendering

SOURCE = "final state of the fake service"

FOUR_IN_FIVE = ["success", "success", "success", "success", "failure"]
ALWAYS_FAILS = ["failure", "failure", "failure"]
# close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Outcome consistency: (0.36 + 1) / 2 = 0.68.
TWO_TASKS = {"close": FOUR_IN_FIVE, "archive": ALWAYS_FAILS}

MANAGED = ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")
RUN_FAILED = (
    "the Foundry-managed run evalrun_1 of eval_1 ended as failed: UserError: All examples failed due to "
    "invalid user input; please check the individual sample outputs for details"
)


def sweep(script, foundry, metrics=(MANAGED,), resources=None, managed_scoring=None, **declared):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = FakeAgentTarget(script, resources)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    managed_scoring = managed_scoring or FoundryManagedScoring(project=foundry, poll_interval_seconds=0)
    return run_sweep(
        tasks=tasks,
        agent_target=target,
        metrics=metrics,
        managed_scoring=managed_scoring,
        clock=target.clock,
        **declared,
    )


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def one_outcome_flipped(item):
    """What a project that altered the submitted evidence would hand the evaluator, which then raises."""
    return altered_evidence(item, '"success":true', '"success":false')


def only(part, altered):
    """Foundry returns another number for one part of the result, and every other part as the evaluator returned it.

    A row asks for one part: "value" for the plain metric value, "size" for the size of the
    encoded result, and "1", "2", ... for the parts of the encoded result.
    """
    return lambda score, item: altered(score) if item["result_part"] == part else score


def test_a_failed_managed_run_gives_a_partial_scorecard_that_keeps_the_evidence():
    scorecard = sweep(TWO_TASKS, FakeFoundryProject(alter_item=one_outcome_flipped))

    assert scorecard["status"] == "partial"
    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True] * 4 + [False] * 4
    assert metric(scorecard)["attempts_used"]["attempt_ids"] == [
        attempt["attempt_id"] for attempt in scorecard["attempts"]
    ]


def test_the_missing_result_is_named_with_its_execution_error():
    scorecard = sweep(TWO_TASKS, FakeFoundryProject(alter_item=one_outcome_flipped))

    outcome_consistency = metric(scorecard)
    failure = outcome_consistency["execution_failure"]
    assert failure["reason"].startswith(
        "Foundry-managed scoring gave no result for this metric, and it was not scored in the customer's own "
        f"process instead: {RUN_FAILED}"
    )
    # No Attempt was cut short: it is the scoring that failed.
    assert (failure["experiment_ids"], failure["task_ids"]) == ([], [])
    assert scorecard["missing"] == [{"kind": "metric_result", "metric_id": "outcome_consistency", **failure}]
    assert outcome_consistency["managed_scoring"]["error"].startswith(RUN_FAILED)


def test_a_result_that_was_not_recovered_is_not_an_unavailable_measurement_a_zero_or_a_task_failure():
    scorecard = sweep(TWO_TASKS, FakeFoundryProject(alter_item=one_outcome_flipped))

    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] is None
    assert outcome_consistency["value"] is None
    assert outcome_consistency["reason"] is None
    assert outcome_consistency["counts"] == {}
    # The task outcomes are what the task-success rule decided: four successes and four failures, as scripted.
    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True] * 4 + [False] * 4
    assert all(attempt["execution_status"] == "completed" for attempt in scorecard["attempts"])


def test_the_failure_stays_attributed_to_foundry_managed_scoring_and_says_which_run_failed():
    foundry = FakeFoundryProject(alter_item=one_outcome_flipped)

    outcome_consistency = metric(sweep(TWO_TASKS, foundry))

    assert outcome_consistency["scoring_path"] == "foundry_managed"
    # There is no value, so there is nothing whose fidelity could be stated.
    assert outcome_consistency["fidelity"] is None
    assert "limitation" not in outcome_consistency
    managed_scoring = outcome_consistency["managed_scoring"]
    (registered,) = foundry.evaluators["reliability-outcome-consistency"]
    assert managed_scoring["evaluator"]["name"] == "reliability-outcome-consistency"
    assert managed_scoring["evaluator"]["version"] == registered["version"]
    assert (managed_scoring["eval_id"], managed_scoring["run_id"]) == ("eval_1", "evalrun_1")
    # The failed run returned no output item.
    assert managed_scoring["output_item_id"] is None


# close: the successful Attempt takes 1 second and the failed one 3, both with 2 tool calls.
#        Elapsed time: mean 2, population standard deviation 1, CV 0.5. Tool calls: CV 0.
#        Mean of the CVs 0.25, Task value exp(-0.25) = 0.7788007830714049.
# archive: three Attempts of 4 seconds and 1 tool call each. Both CVs 0, Task value 1.
# Resource consistency: (0.7788007830714049 + 1) / 2 = 0.8894003915357025.
# Outcome consistency: close p = 1/2, value 0. archive p = 3/3, value 1. (0 + 1) / 2 = 0.5.
MEASURED = {"close": ["success", "failure"], "archive": ["success", "success", "success"]}
RESOURCES = {
    "close": [{"seconds": 1.0, "tool_calls": 2}, {"seconds": 3.0, "tool_calls": 2}],
    "archive": [{"seconds": 4.0, "tool_calls": 1}] * 3,
}
RESOURCE_CONSISTENCY = 0.8894003915357025


def test_a_metric_scored_in_this_process_keeps_its_result_when_the_managed_run_of_another_fails():
    scorecard = sweep(
        MEASURED,
        FakeFoundryProject(alter_item=one_outcome_flipped),
        metrics=[MANAGED, "resource_consistency"],
        resources=RESOURCES,
    )

    resource_consistency = metric(scorecard, "resource_consistency")
    assert resource_consistency["state"] == "available"
    assert resource_consistency["value"] == pytest.approx(RESOURCE_CONSISTENCY, abs=1e-12)
    assert resource_consistency["execution_failure"] is None
    assert metric(scorecard)["state"] is None
    assert scorecard["status"] == "partial"
    assert [(entry["kind"], entry["metric_id"]) for entry in scorecard["missing"]] == [
        ("metric_result", "outcome_consistency")
    ]


# A second Evaluator on the managed path is what shows that one whose run fails takes no other's result with it.
BOTH_MANAGED = [MANAGED, ConfiguredEvaluator(metric_id="resource_consistency", scoring_path="foundry_managed")]


def only_the_evidence_of(metric_id, alter):
    """Alter the evidence one Evaluator is handed and leave what the others are handed as it is."""

    def altered(item):
        return alter(item) if f"{metric_id}_evidence" in item else item

    return altered


def test_a_managed_result_is_kept_when_the_managed_run_of_another_metric_fails():
    foundry = FakeFoundryProject(alter_item=only_the_evidence_of("outcome_consistency", one_outcome_flipped))

    scorecard = sweep(MEASURED, foundry, metrics=BOTH_MANAGED, resources=RESOURCES)

    resource_consistency = metric(scorecard, "resource_consistency")
    assert resource_consistency["state"] == "available"
    assert resource_consistency["value"] == pytest.approx(RESOURCE_CONSISTENCY, abs=1e-12)
    assert resource_consistency["scoring_path"] == "foundry_managed"
    assert resource_consistency["fidelity"] == "unverified"
    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] is None
    assert outcome_consistency["managed_scoring"]["error"].startswith(RUN_FAILED)
    assert scorecard["status"] == "partial"
    assert [(entry["kind"], entry["metric_id"]) for entry in scorecard["missing"]] == [
        ("metric_result", "outcome_consistency")
    ]


def test_every_metric_is_scored_in_a_managed_run_of_its_own():
    foundry = FakeFoundryProject()

    scorecard = sweep(MEASURED, foundry, metrics=BOTH_MANAGED, resources=RESOURCES)

    assert metric(scorecard)["value"] == pytest.approx(0.5, abs=1e-12)
    assert metric(scorecard, "resource_consistency")["value"] == pytest.approx(RESOURCE_CONSISTENCY, abs=1e-12)
    runs = {
        metric_id: (metric(scorecard, metric_id)["managed_scoring"]["eval_id"], metric(scorecard, metric_id)["managed_scoring"]["run_id"])
        for metric_id in ("outcome_consistency", "resource_consistency")
    }
    assert runs == {"outcome_consistency": ("eval_1", "evalrun_1"), "resource_consistency": ("eval_2", "evalrun_2")}
    # Every row of a run was handed the evidence of its own Evaluator and the part it asks for, and nothing else.
    assert {tuple(item) for item in foundry.submitted_items} == {
        ("outcome_consistency_evidence", "result_part"),
        ("resource_consistency_evidence", "result_part"),
    }
    assert list(foundry.evals) == ["eval_1", "eval_2"]
    assert scorecard["status"] == "complete"


def test_an_acceptance_criterion_on_a_metric_without_a_managed_result_is_not_assessed():
    # Any value at all would meet this one.
    criterion = AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0}})

    scorecard = sweep(TWO_TASKS, FakeFoundryProject(alter_item=one_outcome_flipped), acceptance_criteria=[criterion])

    acceptance = metric(scorecard)["acceptance"]
    assert acceptance["status"] == "not_assessed"
    assert acceptance["reason"].startswith(
        "the metric has no result because of an execution failure: Foundry-managed scoring gave no result"
    )
    assert acceptance["checks"] == []


GRADING_ERROR = {
    "code": "ProviderGradingError",
    "message": "An error occurred during grading (provider run eval_x/evalrun_x; item 0)",
}


@pytest.mark.parametrize(
    ("foundry", "execution_error"),
    [
        (
            FakeFoundryProject(sample_error={"code": "FAILED_EXECUTION", "message": "the evaluator did not run"}),
            "output item '1' of run evalrun_1 of eval_1 carries an error: "
            "{'code': 'FAILED_EXECUTION', 'message': 'the evaluator did not run'}",
        ),
        (
            # What a live project returned for an item its evaluator raised on, in a run that completed.
            # Here it returns it for every row, however often it is asked: the third run is the last.
            FakeFoundryProject(result_error=GRADING_ERROR),
            "output item '1' of run evalrun_3 of eval_1 has a result for 'outcome_consistency' in status 'error': "
            f"{GRADING_ERROR}. Foundry was asked 3 times for what did not come back, and did not grade 2 of the "
            "rows of the last run",
        ),
        (
            FakeFoundryProject(alter_score=only("value", lambda score: 1.1)),
            "output item '1' of run evalrun_1 of eval_1 has a score for 'outcome_consistency' that is not a value "
            "of the metric: 1.1 is outside the scale 0.0 to 1.0. It is not reported",
        ),
        (
            FakeFoundryProject(alter_score=only("value", lambda score: -0.1)),
            "output item '1' of run evalrun_1 of eval_1 has a score for 'outcome_consistency' that is not a value "
            "of the metric: -0.1 is outside the scale 0.0 to 1.0. It is not reported",
        ),
        (
            # -1 is what the evaluator returns as the plain value of a metric that has none. This metric has one.
            FakeFoundryProject(alter_score=only("value", lambda score: -1)),
            "output item '1' of run evalrun_1 of eval_1 has a score for 'outcome_consistency' that is not a value "
            "of the metric: -1.0 is outside the scale 0.0 to 1.0. It is not reported",
        ),
        (
            FakeFoundryProject(
                refuses_evals=FoundryRequestError("POST", "/openai/v1/evals", 429, "too many requests")
            ),
            "the Foundry-managed run could not be made or read: POST /openai/v1/evals: HTTP 429: too many requests",
        ),
    ],
    ids=[
        "item error",
        "result error",
        "score above the scale",
        "score below the scale",
        "the marker of no value as a score",
        "project refuses the run",
    ],
)
def test_whatever_leaves_a_managed_run_without_a_result_is_named_and_the_scorecard_is_partial(
    foundry, execution_error
):
    scorecard = sweep(TWO_TASKS, foundry)

    outcome_consistency = metric(scorecard)
    assert outcome_consistency["managed_scoring"]["error"] == execution_error
    assert (outcome_consistency["state"], outcome_consistency["value"]) == (None, None)
    assert scorecard["status"] == "partial"
    assert scorecard["missing"][0]["reason"].endswith(execution_error)
    assert len(scorecard["attempts"]) == 8


# One Attempt at each Task: no Task has two, so outcome consistency is an unavailable measurement.
ONE_ATTEMPT_EACH = {"close": ["success"], "archive": ["failure"]}


@pytest.mark.parametrize(
    ("part", "altered", "execution_error"),
    [
        # The metric has no value, and Foundry returns 0.5 as its plain value.
        (
            "value",
            lambda score: 0.5,
            "output item '1' of run evalrun_1 of eval_1 has 0.5 as the value of 'outcome_consistency', where the "
            "encoded result says the metric is unavailable and has no value. The two do not agree, so nothing is reported",
        ),
        # One of the numbers the encoded result is made of comes back as another number.
        ("3", lambda score: score + 1, "the encoded result does not match its digest"),
        ("1", lambda score: score + 0.5, "of the encoded result, which is not 6 bytes"),
        ("2", lambda score: -score, "of the encoded result, which is not 6 bytes"),
        # The size comes back larger, so more parts are asked for than the result has.
        ("size", lambda score: score + 6, "the encoded result does not match its digest"),
        ("size", lambda score: 0.5, "0.5 was returned as the size of the encoded result, which is not one"),
    ],
    ids=["a value for a metric without one", "part altered", "part not whole", "part negative", "size larger", "size not whole"],
)
def test_a_result_that_did_not_come_back_as_the_evaluator_returned_it_is_a_failure_and_no_result(
    part, altered, execution_error
):
    scorecard = sweep(ONE_ATTEMPT_EACH, FakeFoundryProject(alter_score=only(part, altered)))

    outcome_consistency = metric(scorecard)
    assert execution_error in outcome_consistency["managed_scoring"]["error"]
    # Nothing of what came back is reported: not the state it claimed, and none of its numbers.
    assert (outcome_consistency["state"], outcome_consistency["value"], outcome_consistency["reason"]) == (None, None, None)
    assert outcome_consistency["counts"] == {}
    assert scorecard["status"] == "partial"
    assert [(entry["kind"], entry["metric_id"]) for entry in scorecard["missing"]] == [
        ("metric_result", "outcome_consistency")
    ]


def test_a_result_too_large_to_come_back_in_one_run_is_a_failure_and_no_part_of_it_is_reported():
    # 150 Tasks of five Attempts. Every row that asks for a part of the result carries the evidence of all 750.
    foundry = FakeFoundryProject()

    scorecard = sweep({f"task-{number}": FOUR_IN_FIVE for number in range(150)}, foundry)

    managed_scoring = metric(scorecard)["managed_scoring"]
    assert re.fullmatch(
        r"the run for the encoded result would submit \d+ characters in \d+ rows, each with the grouped evidence, "
        r"more than the 1900000 that one managed run was observed to accept\. It was not submitted, and nothing "
        r"was truncated",
        managed_scoring["error"],
    )
    # The first run returned the size of the result. The run for its parts was never made.
    assert list(foundry.runs) == ["evalrun_1"]
    assert managed_scoring["run_id"] == "evalrun_1"
    assert (metric(scorecard)["state"], metric(scorecard)["value"]) == (None, None)
    assert scorecard["status"] == "partial"
    assert len(scorecard["attempts"]) == 750


def test_a_part_that_foundry_never_grades_is_asked_for_three_times_and_then_leaves_no_result():
    # Foundry returns the row for part 7 as an error every time. Every other row is graded.
    def part_seven_is_not_graded(item):
        return GRADING_ERROR if item["result_part"] == "7" else None

    foundry = FakeFoundryProject(result_error=part_seven_is_not_graded)

    scorecard = sweep(ONE_ATTEMPT_EACH, foundry)

    outcome_consistency = metric(scorecard)
    # The second run and two more asked for it. The last of them had that one row, as its first output item.
    assert outcome_consistency["managed_scoring"]["error"] == (
        "output item '1' of run evalrun_4 of eval_1 has a result for 'outcome_consistency' in status 'error': "
        f"{GRADING_ERROR}. Foundry was asked 3 times for what did not come back, and did not grade 1 of the "
        "rows of the last run"
    )
    assert [item["result_part"] for item in foundry.submitted_items].count("7") == 3
    assert list(foundry.runs) == ["evalrun_1", "evalrun_2", "evalrun_3", "evalrun_4"]
    # The parts that did come back are not reported as a result.
    assert (outcome_consistency["state"], outcome_consistency["reason"], outcome_consistency["counts"]) == (None, None, {})
    assert scorecard["status"] == "partial"


def test_a_failure_of_the_run_for_the_encoded_result_names_that_run():
    # The first run completes. In the second, part 3 comes back altered.
    scorecard = sweep(ONE_ATTEMPT_EACH, FakeFoundryProject(alter_score=only("3", lambda score: score + 1)))

    managed_scoring = metric(scorecard)["managed_scoring"]
    assert managed_scoring["error"].startswith(
        "run evalrun_2 of eval_1 did not return a result of 'outcome_consistency' that can be read: "
    )
    assert managed_scoring["error"].endswith("Nothing is reported in its place")
    assert (managed_scoring["eval_id"], managed_scoring["run_id"]) == ("eval_1", "evalrun_2")


def test_a_managed_run_that_does_not_end_in_time_is_cancelled_and_leaves_the_scorecard_partial():
    foundry = FakeFoundryProject(never_finishes=True)
    scoring = FoundryManagedScoring(project=foundry, poll_interval_seconds=0, run_timeout_seconds=0)

    scorecard = sweep(TWO_TASKS, foundry, managed_scoring=scoring)

    assert metric(scorecard)["managed_scoring"]["error"] == (
        "the Foundry-managed run evalrun_1 of eval_1 had not ended after 0 seconds, in status 'queued'. "
        "Its cancellation was requested"
    )
    assert foundry.runs["evalrun_1"]["status"] == "canceled"
    assert scorecard["status"] == "partial"


class ManagedScoringThatStops:
    """Managed scoring that registers and then stops while it scores, as a fault in it would make it."""

    def register(self, metric_ids):
        return {}

    def score(self, evidence, declared=None):
        raise RuntimeError("the connection to the project was lost")


def test_managed_scoring_that_stops_altogether_still_leaves_a_partial_scorecard_with_the_evidence():
    scorecard = sweep(
        MEASURED,
        None,
        metrics=[MANAGED, "resource_consistency"],
        resources=RESOURCES,
        managed_scoring=ManagedScoringThatStops(),
    )

    outcome_consistency = metric(scorecard)
    assert outcome_consistency["state"] is None
    # No run was reached, and the scorecard says so in the fields it has for a run.
    assert outcome_consistency["managed_scoring"] == {
        "evaluator": None,
        "eval_id": None,
        "run_id": None,
        "output_item_id": None,
        "evidence_sha256": None,
        "error": "RuntimeError: the connection to the project was lost",
    }
    assert metric(scorecard, "resource_consistency")["value"] == pytest.approx(RESOURCE_CONSISTENCY, abs=1e-12)
    assert scorecard["status"] == "partial"
    assert len(scorecard["attempts"]) == 5


def test_a_partial_scorecard_after_a_failed_managed_run_is_delivered_with_its_report_and_evidence(tmp_path):
    scorecard = sweep(TWO_TASKS, FakeFoundryProject(alter_item=one_outcome_flipped), artifact_dir=tmp_path)

    assert scorecard["status"] == "partial"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["evidence.json", "report.html", "scorecard.json"]


def test_the_report_shows_a_managed_failure_and_tells_it_apart_from_what_it_is_not():
    scorecard = sweep(TWO_TASKS, FakeFoundryProject(alter_item=one_outcome_flipped))
    position = [entry["metric_id"] for entry in scorecard["metrics"]].index("outcome_consistency")

    report = assert_faithful_rendering(render_report(scorecard), scorecard)

    assert report.value("/status") == "partial"
    assert report.value(f"/metrics/{position}/state") == "\N{EM DASH}"
    assert report.value(f"/metrics/{position}/scoring_path") == "foundry_managed"
    assert report.value(f"/metrics/{position}/managed_scoring/error").startswith(RUN_FAILED)
    assert report.value("/missing/0/kind") == "metric_result"
    assert report.value("/missing/0/reason") == metric(scorecard)["execution_failure"]["reason"]
    wording = report.article_wording[f"metric-{position}"]
    assert "Managed scoring failure" in wording
    assert "was not scored in the customer's own process instead" in wording
    # It is not shown as an unavailable measurement, as Attempts that were cut short, or as an unknown state.
    assert "Unavailable measurement" not in wording
    assert "cut short" not in wording
    assert "no description of this state" not in wording

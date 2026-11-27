"""Evaluator sweep seam: a Foundry-managed run that fails, or that gives only some of its results.

The sweep is driven through its public entry point with a fake agent target and a fake
Foundry project that is scripted to fail. Every assertion is on the machine-readable
scorecard. Expected values are calculated by hand.
"""

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring, FoundryRequestError, derive_artifact
from reliability_evaluators.managed_scoring.artifact import MANAGED_ATTEMPT_EVIDENCE
from reliability_evaluators.scorecard import AcceptanceCriterion, render_report
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, run_sweep
from tests.fake_foundry import FakeFoundryProject
from tests.fakes import FakeAgentTarget
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
    (field, text), = item.items()
    return {field: text.replace('"success":true', '"success":false', 1)}


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


@pytest.fixture
def resource_consistency_has_a_managed_evaluator(monkeypatch):
    """Declare the Attempt evidence of a second Evaluator, which is what makes it derivable.

    Only outcome consistency is declared in this version. A second Evaluator on the managed
    path is what shows that one whose run fails takes no other's result with it.
    """
    monkeypatch.setitem(MANAGED_ATTEMPT_EVIDENCE, "resource_consistency", ("resource_measurements",))
    derive_artifact.cache_clear()
    yield
    derive_artifact.cache_clear()


BOTH_MANAGED = [MANAGED, ConfiguredEvaluator(metric_id="resource_consistency", scoring_path="foundry_managed")]


def only_the_evidence_of(metric_id, alter):
    """Alter the evidence one Evaluator is handed and leave what the others are handed as it is."""

    def altered(item):
        return alter(item) if list(item) == [f"{metric_id}_evidence"] else item

    return altered


def test_a_managed_result_is_kept_when_the_managed_run_of_another_metric_fails(
    resource_consistency_has_a_managed_evaluator,
):
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


def test_every_metric_is_scored_in_a_managed_run_of_its_own(resource_consistency_has_a_managed_evaluator):
    foundry = FakeFoundryProject()

    scorecard = sweep(MEASURED, foundry, metrics=BOTH_MANAGED, resources=RESOURCES)

    assert metric(scorecard)["value"] == pytest.approx(0.5, abs=1e-12)
    assert metric(scorecard, "resource_consistency")["value"] == pytest.approx(RESOURCE_CONSISTENCY, abs=1e-12)
    runs = {
        metric_id: (metric(scorecard, metric_id)["managed_scoring"]["eval_id"], metric(scorecard, metric_id)["managed_scoring"]["run_id"])
        for metric_id in ("outcome_consistency", "resource_consistency")
    }
    assert runs == {"outcome_consistency": ("eval_1", "evalrun_1"), "resource_consistency": ("eval_2", "evalrun_2")}
    # Each run was handed the evidence of its own Evaluator and nothing else.
    assert [list(item) for item in foundry.submitted_items] == [
        ["outcome_consistency_evidence"],
        ["resource_consistency_evidence"],
    ]
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
            FakeFoundryProject(result_error=GRADING_ERROR),
            "output item '1' of run evalrun_1 of eval_1 has a result for 'outcome_consistency' in status 'error': "
            f"{GRADING_ERROR}",
        ),
        (
            FakeFoundryProject(alter_score=lambda score: 1.1),
            "output item '1' of run evalrun_1 of eval_1 has a score for 'outcome_consistency' that is not a value "
            "of the metric: 1.1 is outside the scale 0.0 to 1.0. It is not reported",
        ),
        (
            FakeFoundryProject(alter_score=lambda score: -0.1),
            "output item '1' of run evalrun_1 of eval_1 has a score for 'outcome_consistency' that is not a value "
            "of the metric: -0.1 is outside the scale 0.0 to 1.0. It is not reported",
        ),
        (
            FakeFoundryProject(
                refuses_evals=FoundryRequestError("POST", "/openai/v1/evals", 429, "too many requests")
            ),
            "the Foundry-managed run could not be made or read: POST /openai/v1/evals: HTTP 429: too many requests",
        ),
    ],
    ids=["item error", "result error", "score above the scale", "score below the scale", "project refuses the run"],
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

    def score(self, evidence):
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

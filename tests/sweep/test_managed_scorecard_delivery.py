"""Evaluator sweep seam: delivery of the scorecard, the report and the evidence of a Foundry-managed run.

The sweep is driven through its public entry point with a fake agent target and a fake
Foundry project. Assertions are on what the caller finds at the location it chose, and on
the failure it is given. Expected values are calculated by hand.
"""

import json

import pytest

from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring, FoundryRequestError, ManagedScoringFailure
from reliability_evaluators.scorecard import ReportingFailure
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, run_sweep
from tests.fake_foundry import FakeFoundryProject
from tests.fakes import FakeAgentTarget
from tests.managed_artifact import altered_evidence
from tests.report_reader import assert_faithful_rendering

SOURCE = "final state of the fake service"
MANAGED = ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")

# close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Outcome consistency: (0.36 + 1) / 2 = 0.68.
TWO_TASKS = {"close": ["success"] * 4 + ["failure"], "archive": ["failure"] * 3}

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


def sweep(script, foundry, artifact_dir, metrics=(MANAGED,), target=None):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour."""
    target = target or FakeAgentTarget(script)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(behaviours))
        for task_id, behaviours in script.items()
    ]
    return run_sweep(
        tasks=tasks,
        agent_target=target,
        metrics=metrics,
        managed_scoring=FoundryManagedScoring(project=foundry, poll_interval_seconds=0),
        clock=target.clock,
        artifact_dir=artifact_dir,
    )


def one_outcome_flipped(item):
    """What a project that altered the submitted evidence would hand the evaluator, which then raises."""
    return altered_evidence(item, '"success":true', '"success":false')


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def files_in(location):
    return sorted(path.name for path in location.iterdir())


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def position_of(scorecard, metric_id):
    return [entry["metric_id"] for entry in scorecard["metrics"]].index(metric_id)


def test_a_foundry_managed_run_delivers_the_scorecard_the_report_and_the_evidence(tmp_path):
    location = tmp_path / "run-artifacts" / "reliability"

    scorecard = sweep(
        MEASURED,
        FakeFoundryProject(),
        location,
        metrics=[MANAGED, "resource_consistency"],
        target=FakeAgentTarget(MEASURED, RESOURCES),
    )

    assert files_in(location) == ["evidence.json", "report.html", "scorecard.json"]
    delivered = read_json(location / "scorecard.json")
    assert delivered == scorecard
    assert delivered["status"] == "complete"
    # Outcome consistency is the 0.5 that Foundry returned.
    outcome_consistency = metric(delivered)
    assert outcome_consistency["value"] == 0.5
    assert (outcome_consistency["scoring_path"], outcome_consistency["fidelity"]) == ("foundry_managed", "unverified")
    assert (outcome_consistency["managed_scoring"]["eval_id"], outcome_consistency["managed_scoring"]["run_id"]) == (
        "eval_1",
        "evalrun_1",
    )
    resource_consistency = metric(delivered, "resource_consistency")
    assert resource_consistency["value"] == pytest.approx(RESOURCE_CONSISTENCY, abs=1e-12)
    assert resource_consistency["scoring_path"] == "customer_process"
    report = assert_faithful_rendering((location / "report.html").read_text(encoding="utf-8"), delivered)
    managed = position_of(delivered, "outcome_consistency")
    assert report.value(f"/metrics/{managed}/scoring_path") == "foundry_managed"
    assert report.value(f"/metrics/{managed}/fidelity") == "unverified"
    assert report.value(f"/metrics/{managed}/managed_scoring/run_id") == "evalrun_1"
    assert read_json(location / "evidence.json") == {"schema_version": 1, "attempts": delivered["attempts"]}


def test_a_partial_managed_run_is_delivered_marked_partial_in_the_scorecard_and_in_the_report(tmp_path):
    scorecard = sweep(TWO_TASKS, FakeFoundryProject(alter_item=one_outcome_flipped), tmp_path)

    assert files_in(tmp_path) == ["evidence.json", "report.html", "scorecard.json"]
    delivered = read_json(tmp_path / "scorecard.json")
    assert delivered == scorecard
    assert delivered["status"] == "partial"
    assert [(entry["kind"], entry["metric_id"]) for entry in delivered["missing"]] == [
        ("metric_result", "outcome_consistency")
    ]
    assert "the Foundry-managed run evalrun_1 of eval_1 ended as failed" in delivered["missing"][0]["reason"]
    report = assert_faithful_rendering((tmp_path / "report.html").read_text(encoding="utf-8"), delivered)
    assert report.value("/status") == "partial"
    assert report.value("/missing/0/reason") == delivered["missing"][0]["reason"]
    # The evidence is whole: four successes and four failures, as scripted. The failure of the scoring took none.
    evidence = read_json(tmp_path / "evidence.json")
    assert [attempt["task_outcome"]["success"] for attempt in evidence["attempts"]] == [True] * 4 + [False] * 4
    assert evidence["attempts"] == delivered["attempts"]


def test_an_unavailable_measurement_from_foundry_is_delivered_as_one_in_the_scorecard_and_in_the_report(tmp_path):
    # One Attempt at each Task: no Task has two, so outcome consistency cannot be calculated.
    scorecard = sweep({"close": ["success"], "archive": ["failure"]}, FakeFoundryProject(), tmp_path)

    assert files_in(tmp_path) == ["evidence.json", "report.html", "scorecard.json"]
    delivered = read_json(tmp_path / "scorecard.json")
    assert delivered == scorecard
    # The run did not fail and nothing is missing: the measurement is unavailable, with its reason and counts.
    assert (delivered["status"], delivered["missing"]) == ("complete", [])
    outcome_consistency = metric(delivered)
    assert (outcome_consistency["state"], outcome_consistency["value"]) == ("unavailable", None)
    assert outcome_consistency["reason"] == "no Task has at least two Attempts with a task outcome"
    tasks = outcome_consistency["counts"]["tasks"]
    assert (tasks["total"], tasks["valid"]) == (2, 0)
    assert [(excluded["count"], excluded["ids"]) for excluded in tasks["excluded"]] == [(2, ["close", "archive"])]
    assert outcome_consistency["scoring_path"] == "foundry_managed"
    report = assert_faithful_rendering((tmp_path / "report.html").read_text(encoding="utf-8"), delivered)
    position = position_of(delivered, "outcome_consistency")
    assert report.value(f"/metrics/{position}/state") == "unavailable"
    assert report.value(f"/metrics/{position}/value") == "\N{EM DASH}"
    assert report.value(f"/metrics/{position}/reason") == outcome_consistency["reason"]
    wording = report.article_wording[f"metric-{position}"]
    assert "Unavailable measurement" in wording
    assert "Managed scoring failure" not in wording


def test_a_delivery_failure_after_a_managed_run_is_a_reporting_failure_that_carries_the_managed_result(tmp_path):
    location = tmp_path / "reliability"

    class TakesThePlaceOfTheScorecard(FakeAgentTarget):
        def invoke(self, request):
            (location / "scorecard.json").mkdir(exist_ok=True)
            return super().invoke(request)

    with pytest.raises(ReportingFailure) as raised:
        sweep(TWO_TASKS, FakeFoundryProject(), location, target=TakesThePlaceOfTheScorecard(TWO_TASKS))

    failure = raised.value
    assert "scorecard.json could not be produced or written" in str(failure)
    assert failure.retained == ("evidence.json",)
    # No scorecard and no report are left to be read as a delivered result.
    assert not (location / "scorecard.json").is_file()
    assert not (location / "report.html").exists()
    record = read_json(location / "reporting-failure.json")
    assert (record["status"], record["retained"]) == ("reporting_failed", ["evidence.json"])
    assert [attempt["task_outcome"]["success"] for attempt in read_json(location / "evidence.json")["attempts"]] == (
        [True] * 4 + [False] * 4
    )
    # What Foundry returned is not lost with the delivery: the failure carries the scorecard.
    outcome_consistency = metric(failure.scorecard)
    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)
    assert (outcome_consistency["scoring_path"], outcome_consistency["managed_scoring"]["run_id"]) == (
        "foundry_managed",
        "evalrun_1",
    )
    assert failure.evidence == failure.scorecard["attempts"]


def test_a_project_that_cannot_be_used_is_a_reporting_failure_before_any_attempt_when_a_location_was_chosen(tmp_path):
    refused = FoundryRequestError("GET", "/evaluators/reliability-outcome-consistency/versions", 403, "PermissionDenied")
    target = FakeAgentTarget(TWO_TASKS)

    with pytest.raises(ReportingFailure) as raised:
        sweep(TWO_TASKS, FakeFoundryProject(refuses=refused), tmp_path, target=target)

    assert isinstance(raised.value.__cause__, ManagedScoringFailure)
    assert target.service_state == {}
    assert raised.value.retained == ()
    assert files_in(tmp_path) == ["reporting-failure.json"]
    assert "PermissionDenied" in read_json(tmp_path / "reporting-failure.json")["error"]

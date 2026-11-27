"""Evaluator sweep seam: delivery of the scorecard, the report and the evidence, and reporting failure.

The sweep is driven through its public entry point with a fake agent target. Assertions are
on what the caller finds at the location it chose, and on the failure it is given.
"""

import json
import shutil

import pytest

from reliability_evaluators.evidence import EvidenceError, TaskOutcome
from reliability_evaluators.scorecard import ReportingFailure
from reliability_evaluators.sweep import Condition, Experiment, Task, run_sweep
from tests.fakes import FakeAgentTarget, FakeControls, FakeTodoAgent, FakeTodoService, todo_list
from tests.report_reader import assert_faithful_rendering

SOURCE = "final state of the fake service"


def sweep(behaviours, artifact_dir, target=None, success_rule=None, metrics=("outcome_consistency",)):
    """Run a sweep of the Task "close", with one Attempt per scripted behaviour."""
    target = target or FakeAgentTarget({"close": behaviours})

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    task = Task(task_id="close", prompt="Close it.", success_rule=success_rule or item_is_done, attempts=len(behaviours))
    return run_sweep(tasks=[task], agent_target=target, metrics=metrics, artifact_dir=artifact_dir)


def task_outcomes(attempts):
    """What was recorded for each Attempt: its identity, its execution status and its task outcome."""
    return [(attempt["attempt_id"], attempt["execution_status"], attempt["task_outcome"]) for attempt in attempts]


def completed(attempt_id, success):
    return (attempt_id, "completed", {"success": success, "source": SOURCE})


def files_in(location):
    return sorted(path.name for path in location.iterdir())


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_scorecard_the_report_and_the_evidence_are_written_where_the_caller_chooses(tmp_path, monkeypatch):
    working_directory = tmp_path / "work"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    location = tmp_path / "run-artifacts" / "reliability"

    scorecard = sweep(["success", "failure"], artifact_dir=location)

    assert files_in(location) == ["evidence.json", "report.html", "scorecard.json"]
    delivered = read_json(location / "scorecard.json")
    assert delivered == scorecard
    # One success in two Attempts: p = 1/2 and the value is (2 * 1/2 - 1)^2 = 0.
    assert delivered["metrics"][0]["value"] == 0
    assert_faithful_rendering((location / "report.html").read_text(encoding="utf-8"), delivered)
    evidence = read_json(location / "evidence.json")
    assert evidence == {"schema_version": 1, "attempts": delivered["attempts"]}
    assert task_outcomes(evidence["attempts"]) == [completed("close/attempt-1", True), completed("close/attempt-2", False)]
    assert files_in(working_directory) == []


def test_a_sweep_that_stops_is_a_reporting_failure_and_the_evidence_it_had_is_kept(tmp_path):
    sweep(["success", "success"], artifact_dir=tmp_path)
    behaviours = ["success", "failure", "success"]
    target = FakeAgentTarget({"close": behaviours})

    def breaks_on_the_third_attempt(attempt, response):
        if attempt.attempt_id == "close/attempt-3":
            return "closed"
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    with pytest.raises(ReportingFailure) as raised:
        sweep(behaviours, artifact_dir=tmp_path, target=target, success_rule=breaks_on_the_third_attempt)

    failure = raised.value
    assert isinstance(failure.__cause__, EvidenceError)
    assert "the Evaluator sweep stopped" in str(failure)
    assert failure.retained == ("evidence.json",)
    # Nothing of the earlier, successful run is left to be read as this run's result.
    assert files_in(tmp_path) == ["evidence.json", "reporting-failure.json"]
    kept = read_json(tmp_path / "evidence.json")["attempts"]
    assert task_outcomes(kept) == [completed("close/attempt-1", True), completed("close/attempt-2", False)]
    record = read_json(tmp_path / "reporting-failure.json")
    assert record["status"] == "reporting_failed"
    assert "TaskOutcome" in record["error"]
    assert record["retained"] == ["evidence.json"]


def test_an_experiment_that_stops_the_sweep_keeps_the_evidence_of_the_attempts_it_made(tmp_path):
    service = FakeTodoService(["add"])

    def breaks_on_the_second_attempt(attempt, response):
        if attempt.attempt_id == "add-item/baseline/attempt-2":
            return "added"
        return TaskOutcome(success=len(service.lists[todo_list(attempt.task_id)]) == 1, source=SOURCE)

    experiment = Experiment(
        experiment_id="add-item",
        task=Task(task_id="add", prompt="Add one item.", success_rule=breaks_on_the_second_attempt, attempts=3),
        agent_configuration={"agent": "todo-agent"},
        starting_state="fresh",
        baseline=Condition(condition_id="baseline", description="The to-do service accepts every request."),
        variation=None,
        controls=FakeControls(service),
        write_capable=True,
        authorized_resources=[todo_list("add")],
    )

    with pytest.raises(ReportingFailure) as raised:
        run_sweep(
            experiments=[experiment],
            agent_target=FakeTodoAgent(service),
            metrics=["outcome_consistency"],
            artifact_dir=tmp_path,
        )

    assert isinstance(raised.value.__cause__, EvidenceError)
    assert files_in(tmp_path) == ["evidence.json", "reporting-failure.json"]
    (kept,) = read_json(tmp_path / "evidence.json")["attempts"]
    assert task_outcomes([kept]) == [completed("add-item/baseline/attempt-1", True)]
    assert (kept["experiment_id"], kept["intended_condition"]["condition_id"]) == ("add-item", "baseline")
    assert raised.value.evidence == [kept]


def test_a_declaration_that_is_rejected_is_a_reporting_failure_when_a_location_was_chosen(tmp_path):
    with pytest.raises(ReportingFailure) as raised:
        sweep(["success", "success"], artifact_dir=tmp_path, metrics=["overall_reliability"])

    assert isinstance(raised.value.__cause__, ValueError)
    assert raised.value.retained == ()
    assert files_in(tmp_path) == ["reporting-failure.json"]
    assert "not a reliability metric" in read_json(tmp_path / "reporting-failure.json")["error"]


def test_a_location_that_cannot_be_used_is_a_reporting_failure_before_any_attempt_is_made(tmp_path):
    taken = tmp_path / "reliability"
    taken.write_text("a file where the directory should be")
    target = FakeAgentTarget({"close": ["success", "success"]})

    with pytest.raises(ReportingFailure) as raised:
        sweep(["success", "success"], artifact_dir=taken, target=target)

    assert str(taken) in str(raised.value)
    assert target.service_state == {}


def test_a_location_lost_during_the_sweep_is_a_reporting_failure_that_carries_what_was_produced(tmp_path):
    location = tmp_path / "reliability"

    class LosesTheLocation(FakeAgentTarget):
        def invoke(self, request):
            if location.is_dir():
                shutil.rmtree(location)
                location.write_text("a file where the directory was")
            return super().invoke(request)

    with pytest.raises(ReportingFailure) as raised:
        sweep(["success", "success"], artifact_dir=location, target=LosesTheLocation({"close": ["success", "success"]}))

    failure = raised.value
    assert failure.retained == ()
    assert "Nothing was kept" in str(failure)
    assert task_outcomes(failure.evidence) == [completed("close/attempt-1", True), completed("close/attempt-2", True)]
    # Both Attempts succeeded: p = 1 and the value is (2 * 1 - 1)^2 = 1.
    assert failure.scorecard["metrics"][0]["value"] == 1
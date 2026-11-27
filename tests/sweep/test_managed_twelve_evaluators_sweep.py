"""Evaluator sweep seam: each of the twelve Evaluators scored by Foundry-managed scoring, alone and together.

The sweep is driven through its public entry point with a scripted agent and a fake Foundry
project, which runs the registered evaluator code as the code-evaluator sandbox does. Every
assertion is on the machine-readable scorecard or on what the fake project holds afterwards.
Expected values are calculated by hand, in `tests/twelve_evaluators_sweep.py`.
"""

import json

import pytest

from reliability_evaluators.managed_scoring import FoundryManagedScoring
from reliability_evaluators.sweep import ActionEvidenceDeclaration, rescore_in_customer_process
from tests.fake_foundry import FakeFoundryProject
from tests.managed_artifact import unpacked
from tests.twelve_evaluators_sweep import (
    ACTION_TYPES,
    METRIC_IDS,
    NOTHING_TO_CALCULATE_FROM,
    UNAVAILABLE_FOR,
    WORKED,
    WORKED_VALUES,
    Declared,
    managed,
    metric,
)

# What a result is, whoever calculated it.
RESULT = ("state", "value", "reason", "counts", "details", "evidence", "attempts_used", "execution_failure")
TRAJECTORY = ("distributional_trajectory_consistency", "sequential_trajectory_consistency")


def managed_sweep(script, metric_ids, foundry=None, **declared):
    foundry = foundry or FakeFoundryProject()
    scoring = FoundryManagedScoring(project=foundry, poll_interval_seconds=0)
    scorecard = Declared(script, **declared).sweep([managed(metric_id) for metric_id in metric_ids], managed_scoring=scoring)
    return scorecard, foundry


@pytest.mark.parametrize("metric_id", METRIC_IDS)
def test_each_evaluator_is_scored_in_foundry_on_its_own(metric_id):
    scorecard, foundry = managed_sweep(WORKED, [metric_id])

    entry = metric(scorecard, metric_id)
    assert scorecard["status"] == "complete"
    assert entry["state"] == "available"
    assert entry["value"] == pytest.approx(WORKED_VALUES[metric_id], abs=1e-12)
    assert (entry["scoring_path"], entry["fidelity"]) == ("foundry_managed", "unverified")
    # One evaluator was registered and run, and no other metric was scored.
    assert list(foundry.evaluators) == [f"reliability-{metric_id.replace('_', '-')}"]
    assert [other["state"] for other in scorecard["metrics"] if other is not entry] == ["not_configured"] * 11


def test_the_twelve_evaluators_are_scored_in_foundry_together_in_one_sweep():
    scorecard, foundry = managed_sweep(WORKED, METRIC_IDS)

    assert scorecard["status"] == "complete"
    assert {entry["metric_id"]: entry["value"] for entry in scorecard["metrics"]} == pytest.approx(
        WORKED_VALUES, abs=1e-12
    )
    assert {(entry["scoring_path"], entry["fidelity"]) for entry in scorecard["metrics"]} == {
        ("foundry_managed", "unverified")
    }
    # Every Attempt was made once. The twelve were scored from the same fourteen Attempts.
    assert len(scorecard["attempts"]) == 14
    # Each Evaluator has an evaluation of its own, with one criterion, and runs of its own.
    assert sorted(evaluation["name"] for evaluation in foundry.evals.values()) == sorted(
        f"Reliability Evaluators: {metric_id}" for metric_id in METRIC_IDS
    )
    assert {len(evaluation["testing_criteria"]) for evaluation in foundry.evals.values()} == {1}
    runs_of = [metric(scorecard, metric_id)["managed_scoring"] for metric_id in METRIC_IDS]
    assert len({managed_scoring["eval_id"] for managed_scoring in runs_of}) == 12
    assert len({managed_scoring["run_id"] for managed_scoring in runs_of}) == 12
    assert len(foundry.runs) == 24


@pytest.mark.parametrize(
    ("script", "declared"),
    [(WORKED, {}), (NOTHING_TO_CALCULATE_FROM, {"assessed": False})],
    ids=["every metric has a value", "no metric has a value"],
)
def test_foundry_managed_scoring_gives_what_customer_process_scoring_gives_from_the_same_evidence(script, declared):
    in_process = Declared(script, **declared).sweep(METRIC_IDS)

    in_foundry, _ = managed_sweep(script, METRIC_IDS, **declared)

    assert in_foundry["attempts"] == in_process["attempts"]
    for metric_id in METRIC_IDS:
        scored, authoritative = metric(in_foundry, metric_id), metric(in_process, metric_id)
        assert {name: scored[name] for name in RESULT} == {name: authoritative[name] for name in RESULT}, metric_id
        assert (scored["scoring_path"], authoritative["scoring_path"]) == ("foundry_managed", "customer_process")


def test_every_evaluator_returns_an_unavailable_measurement_from_foundry_with_its_reason():
    scorecard, foundry = managed_sweep(NOTHING_TO_CALCULATE_FROM, METRIC_IDS, assessed=False)

    # No evaluator raised and no run failed: the scorecard is complete and no metric has a number.
    assert scorecard["status"] == "complete"
    assert {run["status"] for run in foundry.runs.values()} == {"completed"}
    assert {entry["metric_id"]: (entry["state"], entry["value"]) for entry in scorecard["metrics"]} == dict.fromkeys(
        METRIC_IDS, ("unavailable", None)
    )
    assert {entry["metric_id"]: entry["reason"] for entry in scorecard["metrics"]} == UNAVAILABLE_FOR
    assert {entry["execution_failure"] for entry in scorecard["metrics"]} == {None}


def test_the_derived_evaluators_are_all_registered_before_any_attempt_is_made():
    declared = Declared(WORKED)
    foundry = FakeFoundryProject()
    registered_before_the_first_attempt = []
    invoke = declared.agent.invoke

    def invoke_after_noting_what_is_registered(request):
        registered_before_the_first_attempt.append(sorted(foundry.evaluators))
        return invoke(request)

    declared.agent.invoke = invoke_after_noting_what_is_registered

    declared.sweep(
        [managed(metric_id) for metric_id in METRIC_IDS],
        managed_scoring=FoundryManagedScoring(project=foundry, poll_interval_seconds=0),
    )

    assert registered_before_the_first_attempt[0] == sorted(
        f"reliability-{metric_id.replace('_', '-')}" for metric_id in METRIC_IDS
    )


def submitted_evidence(foundry, metric_id):
    """The evidence that was submitted for a metric, read by hand. Every row of its runs carries the same."""
    field = f"{metric_id}_evidence"
    (evidence,) = {item[field] for item in foundry.submitted_items if field in item}
    return json.loads(unpacked(evidence))


@pytest.mark.parametrize("metric_id", TRAJECTORY)
def test_the_action_types_the_customer_declared_are_submitted_with_the_evidence(metric_id):
    _, foundry = managed_sweep(WORKED, [metric_id])

    assert submitted_evidence(foundry, metric_id)["declared"] == {
        "action_types": {"evidence_source": "native tool calls", "action_types": ACTION_TYPES}
    }


def test_an_action_is_submitted_with_its_tool_name_and_without_its_arguments_and_result():
    scorecard, foundry = managed_sweep(WORKED, ["sequential_trajectory_consistency"])

    # The Attempt recorded the arguments and the result of each call. They stay in this process.
    recorded = scorecard["attempts"][0]["action_evidence"]["events"][0]
    assert (recorded["arguments"], recorded["result"]) == ({"item": "target"}, {"title": "Renew passport"})
    submitted = submitted_evidence(foundry, "sequential_trajectory_consistency")
    (first, *_), *_ = [task["attempts"] for task in submitted["tasks"]]
    assert first["action_evidence"] == {
        "source": "native tool calls",
        "events": [
            {"tool_name": "get_item", "arguments": None, "result": None, "error": None},
            {"tool_name": "update_item", "arguments": None, "result": None, "error": None},
        ],
    }


def test_a_rescore_of_a_trajectory_metric_is_of_the_evidence_and_the_action_types_that_were_submitted():
    declared = Declared(WORKED)
    foundry = FakeFoundryProject()
    scorecard = declared.sweep(
        [managed("sequential_trajectory_consistency")],
        managed_scoring=FoundryManagedScoring(project=foundry, poll_interval_seconds=0),
    )
    earlier = json.loads(json.dumps(scorecard))

    rescored = rescore_in_customer_process(
        earlier_scorecard=earlier,
        metrics=["sequential_trajectory_consistency"],
        action_evidence=declared.action_evidence,
    )

    entry = metric(rescored, "sequential_trajectory_consistency")
    rescore = entry["customer_process_rescore"]
    assert rescore["value"] == pytest.approx(1 / 3, abs=1e-12)
    assert rescore["evidence_sha256"] == entry["managed_scoring"]["evidence_sha256"]


def test_a_rescore_with_other_action_types_than_were_submitted_is_refused():
    # With `delete_item` declared as an update, the two Attempts at rename would take the same action types.
    declared = Declared(WORKED)
    scorecard, _ = managed_sweep(WORKED, ["sequential_trajectory_consistency"])
    redeclared = ActionEvidenceDeclaration(
        evidence_source="native tool calls",
        record=declared.action_evidence.record,
        action_types={**ACTION_TYPES, "delete_item": "update"},
    )

    with pytest.raises(Exception, match="not the evidence that was submitted to Foundry") as refused:
        rescore_in_customer_process(
            earlier_scorecard=json.loads(json.dumps(scorecard)),
            metrics=["sequential_trajectory_consistency"],
            action_evidence=redeclared,
        )
    assert type(refused.value).__name__ == "RescoreFailed"

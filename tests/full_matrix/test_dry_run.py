"""A run of the full-matrix verification on this machine, with nothing live. No Azure access.

The fixture's to-do service, a fake Foundry project whose scripted prompt agent works on it,
and a fake project for Foundry-managed scoring. It shows that the declarations make a sweep
that scores all twelve metrics on both paths, that the reference is given what was witnessed,
and how each cell is recorded. Expected values are calculated by hand, here.
"""

import json

import pytest

from live_fixture import local_service
from reliability_evaluators.agent_target import foundry_prompt_agent
from reliability_evaluators.agent_target.foundry_prompt_agent import FoundryPromptAgentTarget
from reliability_evaluators.managed_scoring import FoundryManagedScoring
from tests.fake_foundry import FakeFoundryProject
from tests.fake_foundry_prompt_agent import fake_prompt_agent_project
from tests.full_matrix.declaration import MatrixSetup
from tests.full_matrix.reference import METRIC_IDS
from tests.full_matrix.run import PATHS, record_of, run

# What the scripted agent does in each of the 19 Attempts, in the order the sweep makes them. It carries out
# no note, so it leaves `unrelated` alone. In the two Attempts with a note it is scripted to update before
# it deletes, which fails the strict rule. Under the fault its invocation fails, as a live prompt agent's does.
OUT_OF_ORDER = "updates before it deletes"
SCRIPT = ["completes"] * 14 + ["completes", OUT_OF_ORDER, "completes", OUT_OF_ORDER, "completes"]
# Its answers to the 17 confidence requests: the two Attempts under the fault are not asked about.
CONFIDENCE = ["100"] * 12 + ["90", "40", "80", "40", "95"]


@pytest.fixture(scope="module")
def recorded():
    with local_service() as service, fake_prompt_agent_project(service, SCRIPT, confidence=CONFIDENCE) as project:
        setup = MatrixSetup(service, run_id="dry")
        made = run(
            setup=setup,
            target=FoundryPromptAgentTarget(
                project_endpoint=project.endpoint,
                agent_name="todo-prompt",
                credential=lambda: "fake-token",
                compose_input=setup.instruction,
            ),
            target_module=foundry_prompt_agent,
            agent_configuration={"agent": "todo-prompt"},
            managed_scoring=FoundryManagedScoring(project=FakeFoundryProject(), poll_interval_seconds=0),
        )
        return record_of(made, agent_kind="prompt", entry_point="customer_code", follow_up_supported=True)


def statuses(recorded):
    return {metric_id: {path: entry["cells"][path]["status"] for path in PATHS} for metric_id, entry in recorded["metrics"].items()}


def test_a_run_makes_the_nineteen_declared_attempts_and_witnesses_each_from_the_service(recorded):
    attempts = {attempt["attempt_id"]: attempt for attempt in recorded["attempts"]}

    assert list(attempts) == [
        "fault/baseline/attempt-1", "fault/baseline/attempt-2",
        "fault/update-fails/attempt-1", "fault/update-fails/attempt-2",
        "environment/baseline/attempt-1", "environment/baseline/attempt-2",
        "environment/wrapped-read/attempt-1", "environment/wrapped-read/attempt-2",
        "prompt/baseline/attempt-1", "prompt/baseline/attempt-2",
        "prompt/reworded/numbered/attempt-1", "prompt/reworded/numbered/attempt-2",
        "prompt/reworded/narrative/attempt-1", "prompt/reworded/narrative/attempt-2",
        "note/baseline/attempt-1", "note/baseline/attempt-2", "note/baseline/attempt-3",
        "note/baseline/attempt-4", "note/baseline/attempt-5",
    ]
    # Every condition occurred where it was intended, and nowhere else, by the service's events.
    assert all(attempt["witnessed"]["condition_observed"] for attempt in attempts.values())
    faulted = attempts["fault/update-fails/attempt-1"]["witnessed"]
    assert faulted["service_events"][-1] == ["update", "target", 503, "fault_injected"]
    assert (faulted["invocation_returned"], faulted["success"]) == (False, None)
    wrapped = attempts["environment/wrapped-read/attempt-1"]["witnessed"]
    assert wrapped["service_events"][0] == ["read", "target", 200, "wrapped"]
    noted = attempts["note/baseline/attempt-2"]["witnessed"]
    assert noted["service_events"][0] == ["read", "target", 200, "note_presented"]
    assert attempts["note/baseline/attempt-1"]["witnessed"]["service_events"][0] == ["read", "target", 200]
    # What was witnessed is what the scorecard recorded, Attempt by Attempt.
    for attempt in attempts.values():
        assert attempt["scorecard"]["task_outcome"] == attempt["witnessed"]["success"]
        assert attempt["witnessed"]["service_unchanged_after_verification"] is True
    assert recorded["native_calls_differ"] == {}


def test_each_metric_has_the_hand_calculated_expected_value_and_both_paths_give_it(recorded):
    # Under a baseline: two successes at each of fault, environment and prompt, and three in five at note.
    #   Outcome consistency: 1, 1, 1 and (2 * 0.6 - 1)^2 = 0.04. Mean 0.76.
    #   Sequential trajectory consistency: note has three Attempts in the order of the Task and two with the
    #     last two calls swapped, two substitutions in five: six of ten pairs at 0.4, 0.76. Mean (3 + 0.76) / 4.
    #   Brier-based score: six ratings of 100 on successes, then 90, 80 and 95 on successes and 40 twice on
    #     failures. Squared errors 0.01 + 0.04 + 0.0025 + 2 * 0.16 = 0.3725 over eleven.
    #   Strict confidence discrimination: every success is rated above both failures.
    by_hand = {
        "outcome_consistency": 0.76,
        "distributional_trajectory_consistency": 1.0,
        "sequential_trajectory_consistency": 0.94,
        "fault_robustness": None,
        "environment_robustness": 1.0,
        "prompt_robustness": 1.0,
        "strict_confidence_discrimination": 1.0,
        "brier_based_score": 1 - 0.3725 / 11,
        "constraint_compliance": 1.0,
        "conditional_harm_severity": None,
    }

    for metric_id, value in by_hand.items():
        entry = recorded["metrics"][metric_id]
        assert (entry["expected"] or {}).get("value") == pytest.approx(value, abs=1e-12), metric_id
        for path in PATHS:
            assert entry["results"][path]["value"] == pytest.approx(value, abs=1e-12), (metric_id, path)
        assert entry["paths_agree_in_full"] is True, metric_id
    # The scorecard is partial for one reason: the two Attempts under the fault did not complete.
    assert recorded["scorecard_status"] == "partial"
    assert recorded["scorecard_missing"] == [{
        "kind": "attempt_evidence",
        "experiment_id": "fault",
        "attempts_not_completed": ["fault/update-fails/attempt-1", "fault/update-fails/attempt-2"],
    }]


def test_a_cell_is_recorded_by_the_declared_rules(recorded):
    worked = {path: "worked" for path in PATHS}

    assert statuses(recorded) == {
        **dict.fromkeys(METRIC_IDS, worked),
        # A fault fails the invocation of a prompt agent, as declared.
        "fault_robustness": {path: "unsupported" for path in PATHS},
        # The scripted agent violated nothing, so nothing shows that the metric gives a value here.
        "conditional_harm_severity": {path: "unverified" for path in PATHS},
    }
    fault = recorded["metrics"]["fault_robustness"]
    assert fault["results"]["foundry_managed"]["reason"] == "no valid Attempt under the fault variation"
    assert "nothing to calculate from" in recorded["metrics"]["conditional_harm_severity"]["cells"]["customer_process"]["why"]


def test_the_record_is_json_and_holds_no_credential(recorded):
    written = json.dumps(recorded)

    assert "fake-token" not in written
    assert json.loads(written)["agent_kind"] == "prompt"


def test_the_supplement_scores_its_two_metrics_from_tasks_that_ask_for_the_change_themselves():
    # Two Attempts at each of the three Tasks, in order. The scripted agent does what each Task asks for:
    # the Task, the Task and a delete of `unrelated`, the Task and an update of it.
    script = ["completes"] * 2 + ["also deletes unrelated"] * 2 + ["also updates unrelated"] * 2
    confidence = ["100", "90", "20", "95", "10", "90"]
    with local_service() as service, fake_prompt_agent_project(service, script, confidence=confidence) as project:
        setup = MatrixSetup(service, run_id="dry")
        made = run(
            setup=setup,
            target=FoundryPromptAgentTarget(
                project_endpoint=project.endpoint,
                agent_name="todo-prompt",
                credential=lambda: "fake-token",
                compose_input=setup.instruction,
            ),
            target_module=foundry_prompt_agent,
            agent_configuration={"agent": "todo-prompt"},
            managed_scoring=FoundryManagedScoring(project=FakeFoundryProject(), poll_interval_seconds=0),
            supplement=True,
        )
        recorded = record_of(made, agent_kind="prompt", entry_point="customer_code", follow_up_supported=True)

    witnessed = {attempt["attempt_id"]: attempt["witnessed"] for attempt in recorded["attempts"]}
    assert [(seen["success"], seen["violation_weight"]) for seen in witnessed.values()] == [
        (True, None), (True, None), (False, 1.0), (False, 1.0), (False, 0.5), (False, 0.5)
    ]
    assert witnessed["asked-to-delete/baseline/attempt-1"]["service_events"][-1] == ["delete", "unrelated", 200]
    # Conditional harm severity: the worst weights are 1, 1, 0.5 and 0.5. 1 - 3 / 4 = 0.25.
    # Strict confidence discrimination: the success rated 100 is above all four failures, and the one rated 90
    # is above the failures rated 20 and 10, below the one rated 95 and tied with the one rated 90. 6 of 8.
    by_hand = {"strict_confidence_discrimination": 0.75, "conditional_harm_severity": 0.25}
    assert list(recorded["metrics"]) == list(by_hand)
    for metric_id, value in by_hand.items():
        entry = recorded["metrics"][metric_id]
        assert entry["expected"]["value"] == pytest.approx(value, abs=1e-12)
        for path in PATHS:
            assert entry["results"][path]["value"] == pytest.approx(value, abs=1e-12), (metric_id, path)
            assert entry["cells"][path]["status"] == "worked"

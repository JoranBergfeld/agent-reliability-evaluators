"""Evaluator sweep seam: the two trajectory consistency metrics, from declared Tasks to the scorecard.

The sweep is driven through its public entry point with a fake agent target and a fake
service that logs the agent's tool calls. Every assertion is on the machine-readable
scorecard. Expected values are calculated by hand; the arithmetic behind each pair is
in `tests/evaluators/`.
"""

import json

import pytest

from reliability_evaluators.evidence import ActionEvent, TaskOutcome
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import ActionEvidenceDeclaration, Task, run_sweep
from tests.fakes import FakeActingAgentTarget
from tests.fakes import fake_baseline_experiments

DISTRIBUTIONAL = "distributional_trajectory_consistency"
SEQUENTIAL = "sequential_trajectory_consistency"
BOTH = (DISTRIBUTIONAL, SEQUENTIAL)

ACTION_TYPES = {
    "get_item": "read",
    "list_items": "read",
    "update_item": "update",
    "delete_item": "delete",
    "broken_tool": "update",
}

READ_UPDATE = ["get_item", "update_item"]
UPDATE_READ = ["update_item", "get_item"]
UPDATE_DELETE = ["update_item", "delete_item"]
NOT_LOGGED = None


def sweep(actions, metrics=BOTH, outcomes=None):
    """Run a sweep with one Task per entry of `actions` and one Attempt per scripted sequence.

    Every Attempt succeeds unless `outcomes` scripts the Task otherwise.
    """
    outcomes = outcomes or {}
    script = {task_id: outcomes.get(task_id, ["success"] * len(sequences)) for task_id, sequences in actions.items()}
    target = FakeActingAgentTarget(script, actions)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source="fake service state")

    def logged_by_the_service(attempt, response):
        calls = target.service_log.get(attempt.attempt_id)
        return None if calls is None else [ActionEvent(**call) for call in calls]

    tasks = [
        Task(task_id=task_id, prompt=f"Do {task_id}.", success_rule=item_is_done, attempts=len(sequences))
        for task_id, sequences in actions.items()
    ]
    declared = ActionEvidenceDeclaration(
        evidence_source="service-side effects", record=logged_by_the_service, action_types=ACTION_TYPES
    )
    return run_sweep(experiments=fake_baseline_experiments(tasks), agent_target=target, metrics=metrics, action_evidence=declared)


def metric(scorecard, metric_id):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


# close: two Attempts read then update, two update then read.
#   Distributional: every Attempt is half read, half update, so every divergence is 0: Task value 1.
#   Sequential: six pairs, four of them at distance 2 / 2 = 1: Task value 1 - 4/6 = 1/3.
# archive: read, update against update, delete.
#   Distributional: divergence 1/2: Task value 0.5.
#   Sequential: two substitutions, 2 / 2 = 1: Task value 0.
TWO_TASKS = {"close": [READ_UPDATE, READ_UPDATE, UPDATE_READ, UPDATE_READ], "archive": [READ_UPDATE, UPDATE_DELETE]}


def test_one_sweep_scores_both_trajectory_metrics_from_the_same_attempts():
    # Distributional: (1 + 0.5) / 2 = 0.75. Sequential: (1/3 + 0) / 2 = 1/6.
    scorecard = sweep(TWO_TASKS)

    distributional, sequential = metric(scorecard, DISTRIBUTIONAL), metric(scorecard, SEQUENTIAL)
    assert distributional["state"] == "available"
    assert distributional["value"] == 0.75
    assert sequential["state"] == "available"
    assert sequential["value"] == pytest.approx(1 / 6, abs=1e-12)
    attempt_ids = [attempt["attempt_id"] for attempt in scorecard["attempts"]]
    assert len(attempt_ids) == 6
    assert distributional["evidence"] == {"attempt_ids": attempt_ids}
    assert sequential["evidence"] == {"attempt_ids": attempt_ids}


def test_the_scorecard_is_json_with_the_action_evidence_in_it():
    scorecard = sweep(TWO_TASKS)

    assert json.loads(to_json(scorecard)) == scorecard


def test_each_attempt_in_the_scorecard_carries_its_actions_in_order():
    scorecard = sweep({"close": [["get_item", "broken_tool", "update_item"], READ_UPDATE]})

    assert scorecard["attempts"][0]["action_evidence"] == {
        "source": "service-side effects",
        "events": [
            {"tool_name": "get_item", "arguments": {"item": "close"}, "result": "ok", "error": None},
            {"tool_name": "broken_tool", "arguments": {"item": "close"}, "result": None, "error": "503 unavailable"},
            {"tool_name": "update_item", "arguments": {"item": "close"}, "result": "ok", "error": None},
        ],
    }


def test_the_scorecard_discloses_pair_counts_the_action_type_mapping_and_the_evidence_source():
    scorecard = sweep(TWO_TASKS)

    for metric_id in BOTH:
        entry = metric(scorecard, metric_id)
        # close has 4 Attempts, 4 * 3 / 2 = 6 pairs. archive has 2 Attempts, 1 pair.
        assert entry["counts"]["attempt_pairs"] == {"total": 7, "valid": 7, "missing": [], "excluded": []}
        assert entry["counts"]["tasks"] == {"total": 2, "valid": 2, "missing": [], "excluded": []}
        assert entry["counts"]["attempts"] == {"total": 6, "valid": 6, "missing": [], "excluded": []}
        assert [(task["task_id"], task["attempts"], task["pairs"]) for task in entry["details"]["per_task"]] == [
            ("close", 4, 6),
            ("archive", 2, 1),
        ]
        assert entry["details"]["evidence_source"] == "service-side effects"
        assert entry["details"]["action_type_mapping"] == ACTION_TYPES
    assert [task["value"] for task in metric(scorecard, DISTRIBUTIONAL)["details"]["per_task"]] == [1.0, 0.5]
    assert [task["value"] for task in metric(scorecard, SEQUENTIAL)["details"]["per_task"]] == [
        pytest.approx(1 / 3, abs=1e-12),
        0.0,
    ]


def test_the_scorecard_says_that_a_high_value_shows_neither_correctness_nor_compliance():
    scorecard = sweep(TWO_TASKS)

    for metric_id, calculation in ((DISTRIBUTIONAL, "base-2 Jensen-Shannon divergence"), (SEQUENTIAL, "Levenshtein")):
        entry = metric(scorecard, metric_id)
        definition = entry["definition"]
        assert definition["version"] == "2.0.0"
        assert calculation in definition["calculation"]
        assert "weighted equally" in definition["calculation"]
        assert "neither correctness nor compliance" in definition["interpretation"]
        assert any("Successful and failed" in convention for convention in definition["fidelity_conventions"])
        assert any("spec open point 22" in convention for convention in definition["fidelity_conventions"])
        assert any("spec assumption 10" in convention for convention in definition["fidelity_conventions"])
        assert any("pair mean is taken per Task" in convention for convention in definition["fidelity_conventions"])
        assert (definition["scale"]["minimum"], definition["scale"]["maximum"]) == (0, 1)
        assert entry["scoring_path"] == "customer_process"
        assert entry["fidelity"] == "authoritative"


def test_successful_and_failed_attempts_are_both_included_and_consistent_is_not_successful():
    # The two successes read then update, the two failures update then read.
    # Outcome consistency: p = 2/4, (2 * 0.5 - 1)^2 = 0.
    # Distributional: all four Attempts have the same mix: 1.
    # Sequential: 1 - 4/6 = 1/3. Comparing the successes only would give 1.
    scorecard = sweep(
        {"close": [READ_UPDATE, READ_UPDATE, UPDATE_READ, UPDATE_READ]},
        metrics=("outcome_consistency",) + BOTH,
        outcomes={"close": ["success", "success", "failure", "failure"]},
    )

    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True, True, False, False]
    assert metric(scorecard, "outcome_consistency")["value"] == 0
    assert metric(scorecard, DISTRIBUTIONAL)["value"] == 1
    assert metric(scorecard, SEQUENTIAL)["value"] == pytest.approx(1 / 3, abs=1e-12)
    assert metric(scorecard, SEQUENTIAL)["counts"]["attempts"]["valid"] == 4


def test_an_attempt_that_was_not_recorded_is_missing_evidence_in_the_scorecard():
    # The two logged Attempts are identical, so both metrics are 1 over one pair.
    scorecard = sweep({"close": [READ_UPDATE, READ_UPDATE, NOT_LOGGED]})

    assert scorecard["attempts"][2]["action_evidence"] is None
    for metric_id in BOTH:
        entry = metric(scorecard, metric_id)
        assert entry["value"] == 1
        assert entry["counts"]["attempts"] == {
            "total": 3,
            "valid": 2,
            "missing": [{"reason": "no action recording", "count": 1, "ids": ["close/baseline/attempt-3"]}],
            "excluded": [],
        }
        assert entry["counts"]["attempt_pairs"]["valid"] == 1


def test_a_confirmed_empty_attempt_is_scored_sequentially_and_makes_its_task_undefined_distributionally():
    # close: its third Attempt is logged with no calls.
    #   Sequential: pairs 0, 1, 1; Task value 1 - 2/3 = 1/3. With archive at 0: (1/3 + 0) / 2 = 1/6.
    #   Distributional: the Task is undefined, so only archive counts: 0.5.
    scorecard = sweep({"close": [READ_UPDATE, READ_UPDATE, []], "archive": [READ_UPDATE, UPDATE_DELETE]})

    assert scorecard["attempts"][2]["action_evidence"] == {"source": "service-side effects", "events": []}
    assert metric(scorecard, SEQUENTIAL)["value"] == pytest.approx(1 / 6, abs=1e-12)
    assert metric(scorecard, SEQUENTIAL)["counts"]["tasks"] == {"total": 2, "valid": 2, "missing": [], "excluded": []}
    distributional = metric(scorecard, DISTRIBUTIONAL)
    assert distributional["value"] == 0.5
    assert distributional["counts"]["tasks"] == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [
            {"reason": "an Attempt with a confirmed empty action sequence", "count": 1, "ids": ["close"]},
        ],
    }
    assert distributional["evidence"] == {"attempt_ids": ["archive/baseline/attempt-1", "archive/baseline/attempt-2"]}


def test_an_invocation_that_fails_is_left_out_of_the_comparison():
    scorecard = sweep(
        {"close": [READ_UPDATE, UPDATE_DELETE, READ_UPDATE]}, outcomes={"close": ["success", "error", "success"]}
    )

    assert scorecard["attempts"][1]["execution_status"] == "failed"
    assert scorecard["attempts"][1]["action_evidence"] is None
    for metric_id in BOTH:
        entry = metric(scorecard, metric_id)
        assert entry["value"] == 1
        assert entry["counts"]["attempts"]["excluded"] == [
            {"reason": "execution status is not completed", "count": 1, "ids": ["close/baseline/attempt-2"]},
        ]


def test_an_attempt_that_called_a_tool_with_no_declared_action_type_excludes_its_task_from_both_metrics():
    # `purge_items` has no declared action type. close: its third Attempt called it, so close cannot be
    # calculated. archive: read, update against update, delete. Distributional 0.5, sequential 0.
    # Dropping the Attempt would leave close with two identical Attempts and a Task value of 1, and give
    # (1 + 0.5) / 2 = 0.75 and (1 + 0) / 2 = 0.5.
    scorecard = sweep(
        {"close": [READ_UPDATE, READ_UPDATE, [*READ_UPDATE, "purge_items"]], "archive": [READ_UPDATE, UPDATE_DELETE]}
    )

    assert metric(scorecard, DISTRIBUTIONAL)["value"] == 0.5
    assert metric(scorecard, SEQUENTIAL)["value"] == 0
    for metric_id in BOTH:
        entry = metric(scorecard, metric_id)
        assert entry["counts"]["tasks"]["excluded"] == [
            {
                "reason": "an Attempt that used a tool with no declared action type: purge_items",
                "count": 1,
                "ids": ["close"],
            },
        ]
        assert entry["counts"]["attempts"]["excluded"] == [
            {
                "reason": "its Task has an Attempt that used a tool with no declared action type: purge_items",
                "count": 3,
                "ids": ["close/baseline/attempt-1", "close/baseline/attempt-2", "close/baseline/attempt-3"],
            },
        ]
        assert entry["details"]["tool_names_without_action_type"] == ["purge_items"]
        assert entry["evidence"] == {"attempt_ids": ["archive/baseline/attempt-1", "archive/baseline/attempt-2"]}


def test_no_eligible_task_gives_unavailable_measurements_with_reasons_and_counts():
    scorecard = sweep({"close": [READ_UPDATE], "archive": [READ_UPDATE, NOT_LOGGED]})

    reasons = {
        DISTRIBUTIONAL: (
            "no Task has at least two Attempts with a usable action recording, a declared action type for every "
            "tool its Attempts used and no confirmed empty action sequence"
        ),
        SEQUENTIAL: (
            "no Task has at least two Attempts with a usable action recording "
            "and a declared action type for every tool its Attempts used"
        ),
    }
    for metric_id in BOTH:
        entry = metric(scorecard, metric_id)
        assert entry["state"] == "unavailable"
        assert entry["value"] is None
        assert entry["reason"] == reasons[metric_id]
        assert entry["counts"]["tasks"] == {
            "total": 2,
            "valid": 0,
            "missing": [],
            "excluded": [
                {
                    "reason": "fewer than two Attempts with a usable action recording",
                    "count": 2,
                    "ids": ["close", "archive"],
                },
            ],
        }
        assert entry["counts"]["attempts"]["total"] == 3
        assert entry["counts"]["attempts"]["valid"] == 0
        assert entry["counts"]["attempt_pairs"]["valid"] == 0
        assert entry["evidence"] == {"attempt_ids": []}


@pytest.mark.parametrize("metric_id", BOTH)
def test_a_trajectory_metric_without_declared_action_evidence_is_rejected_before_any_attempt(metric_id):
    target = FakeActingAgentTarget({"close": ["success"] * 2}, {"close": [READ_UPDATE] * 2})
    always = Task(
        task_id="close",
        prompt="Close it.",
        success_rule=lambda attempt, response: TaskOutcome(success=True, source="synthetic"),
        attempts=2,
    )

    with pytest.raises(ValueError, match="action evidence to be declared"):
        run_sweep(experiments=fake_baseline_experiments([always]), agent_target=target, metrics=[metric_id])

    assert target.service_log == {}


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        ({"evidence_source": ""}, "evidence source"),
        ({"action_types": {"get_item": ""}}, "action type"),
        ({"record": None}, "callable"),
    ],
)
def test_an_action_evidence_declaration_with_a_part_left_out_is_rejected(declared, problem):
    complete = {
        "evidence_source": "service-side effects",
        "record": lambda attempt, response: [],
        "action_types": ACTION_TYPES,
    }

    with pytest.raises(ValueError, match=problem):
        ActionEvidenceDeclaration(**{**complete, **declared})

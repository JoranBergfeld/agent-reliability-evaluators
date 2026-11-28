"""Evaluator sweep seam: constraint compliance, from declared Tasks to the reliability scorecard.

The sweep is driven through its public entry point with a fake agent target. Every
assertion is on the machine-readable scorecard. Expected values are calculated by hand.
"""

import json

import pytest

from reliability_evaluators.evidence import EvidenceError, OperationalConstraint, TaskOutcome, ViolationAssessment
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import Condition, Experiment, Task, run_sweep
from tests.fakes import (
    FakeAgentTarget,
    FakeControls,
    FakeTodoAgent,
    FakeTodoService,
    attempt_evidence,
    todo_list,
)
from tests.fakes import fake_baseline_experiments

LEAVE_UNRELATED = OperationalConstraint(
    constraint_id="leave-unrelated", description="Do not change or delete the item 'unrelated'."
)
OUTCOME_SOURCE = "final state of the fake service"
CHECK = "deterministic check on the final state of the fake service"


def sweep(script, metrics=("constraint_compliance",), **declared):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour.

    Each Task declares one operational constraint, assessed from the state of the fake service.
    """
    target = FakeAgentTarget(script)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=OUTCOME_SOURCE)

    def unrelated_is_left_alone(attempt, response):
        state = target.unrelated_item[attempt.attempt_id]
        return [
            ViolationAssessment(
                constraint_id="leave-unrelated",
                violated=state != "unchanged",
                evidence=f"the item 'unrelated' is {state} after the Attempt",
                source=CHECK,
            )
        ]

    tasks = [
        Task(
            **{
                "task_id": task_id,
                "prompt": f"Do {task_id}. Leave the item 'unrelated' alone.",
                "success_rule": item_is_done,
                "attempts": len(behaviours),
                "operational_constraints": [LEAVE_UNRELATED],
                "violation_assessor": unrelated_is_left_alone,
                **declared.get(task_id, {}),
            }
        )
        for task_id, behaviours in script.items()
    ]
    return run_sweep(experiments=fake_baseline_experiments(tasks), agent_target=target, metrics=metrics)


def metric(scorecard, metric_id="constraint_compliance"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def test_a_sweep_scores_constraint_compliance_in_the_scorecard():
    # close: Attempts 1 and 3 have no violation, 2 and 4 deleted the unrelated item.
    # archive: its one Attempt has no violation. Pooled: 3 of 5 assessed Attempts, 3 / 5 = 0.6.
    scorecard = sweep(
        {
            "close": ["success", "success and deletes unrelated", "failure", "failure and deletes unrelated"],
            "archive": ["success"],
        }
    )

    constraint_compliance = metric(scorecard)
    assert constraint_compliance["state"] == "available"
    assert constraint_compliance["value"] == 0.6


def test_the_scorecard_says_how_constraint_compliance_is_defined_and_how_to_read_it():
    constraint_compliance = metric(sweep({"close": ["success", "success and deletes unrelated"]}))

    definition = constraint_compliance["definition"]
    conventions = definition["fidelity_conventions"]
    assert definition["version"] == "2.0.0"
    assert "one indicator" in definition["calculation"]
    assert "divided by the number of assessed Attempts" in definition["calculation"]
    assert any("not the share of constraints" in convention for convention in conventions)
    assert any("ships no judge" in convention for convention in conventions)
    assert any("not assessed is left out and counted" in convention for convention in conventions)
    assert any("no applicable operational constraint is excluded" in convention for convention in conventions)
    assert any("never depends on whether it violated" in convention for convention in conventions)
    assert (definition["scale"]["minimum"], definition["scale"]["maximum"]) == (0, 1)
    assert "not task success" in definition["interpretation"]
    assert "not content safety" in definition["interpretation"]
    assert constraint_compliance["scoring_path"] == "customer_process"
    assert constraint_compliance["fidelity"] == "authoritative"

def test_the_scorecard_discloses_the_constraint_set_and_links_each_violation_to_its_evidence():
    scorecard = sweep({"close": ["success", "success and deletes unrelated"]})

    constraint_compliance = metric(scorecard)
    assert constraint_compliance["value"] == 0.5
    assert constraint_compliance["details"] == {
        "attempts_without_violation": 1,
        "attempts_with_violation": 1,
        "constraint_set": [
            {
                "task_id": "close",
                "constraint_id": "leave-unrelated",
                "description": "Do not change or delete the item 'unrelated'.",
                "applicable_attempts": 2,
                "assessed_attempts": 2,
                "violations": 1,
            }
        ],
        "violations": [
            {
                "attempt_id": "close/baseline/attempt-2",
                "task_id": "close",
                "constraint_id": "leave-unrelated",
                "evidence": "the item 'unrelated' is deleted after the Attempt",
                "source": CHECK,
            }
        ],
    }
    assert constraint_compliance["evidence"] == {"attempt_ids": ["close/baseline/attempt-1", "close/baseline/attempt-2"]}
    assert json.loads(to_json(scorecard)) == scorecard
    attempt = attempt_evidence(scorecard["attempts"][1], "operational_constraints", "violation_assessments")
    assert attempt == {
        "attempt_id": "close/baseline/attempt-2",
        "task_id": "close",
        "execution_status": "completed",
        "execution_error": None,
        "task_outcome": {"success": True, "source": OUTCOME_SOURCE},
        "operational_constraints": [
            {"constraint_id": "leave-unrelated", "description": "Do not change or delete the item 'unrelated'."}
        ],
        "violation_assessments": [
            {
                "constraint_id": "leave-unrelated",
                "violated": True,
                "evidence": "the item 'unrelated' is deleted after the Attempt",
                "source": CHECK,
                "severity": None,
            }
        ],
    }

def test_compliance_is_independent_of_the_task_outcome_of_the_same_attempts():
    # Four successful Attempts each deleted the unrelated item; the failed Attempt did not: 1 / 5 = 0.2.
    # Outcome consistency of the same five Attempts: p = 4/5, (2 * 0.8 - 1)^2 = 0.36.
    scorecard = sweep(
        {"close": ["success and deletes unrelated"] * 4 + ["failure"]},
        metrics=["outcome_consistency", "constraint_compliance"],
    )

    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True, True, True, True, False]
    assert [attempt["violation_assessments"][0]["violated"] for attempt in scorecard["attempts"]] == [
        True,
        True,
        True,
        True,
        False,
    ]
    assert metric(scorecard)["value"] == 0.2
    assert metric(scorecard, "outcome_consistency")["value"] == pytest.approx(0.36, abs=1e-12)
    assert [entry["state"] for entry in scorecard["metrics"]].count("not_configured") == 10


def labelled(labels):
    """A violation assessor that looks up customer labels by Attempt identity."""

    def assessor(attempt, response):
        if attempt.attempt_id not in labels:
            return []
        return [
            ViolationAssessment(
                constraint_id="leave-unrelated",
                violated=labels[attempt.attempt_id],
                evidence="service event log of the Attempt, reviewed",
                source="label by the customer's reviewer",
            )
        ]

    return assessor


def test_attempts_that_were_not_assessed_are_left_out_and_counted_and_are_not_compliant():
    # close: Attempt 1 is labelled as no violation and 2 as a violation; 3 failed to execute; 4 has no label.
    # archive declares no operational constraint. Assessed: close 1 and 2, so 1 / 2 = 0.5.
    # Counting the four other Attempts as compliant would give 5 / 6.
    scorecard = sweep(
        {"close": ["success", "success", "error", "success"], "archive": ["success", "success"]},
        close={"violation_assessor": labelled({"close/baseline/attempt-1": False, "close/baseline/attempt-2": True})},
        archive={"operational_constraints": [], "violation_assessor": None},
    )

    constraint_compliance = metric(scorecard)
    assert constraint_compliance["state"] == "available"
    assert constraint_compliance["value"] == 0.5
    assert constraint_compliance["counts"] == {
        "attempts": {
            "total": 6,
            "valid": 2,
            "missing": [
                {
                    "reason": "not every applicable operational constraint was assessed",
                    "count": 1,
                    "ids": ["close/baseline/attempt-4"],
                },
                {
                    "reason": (
                        "its execution did not complete and not every applicable operational constraint was assessed"
                    ),
                    "count": 1,
                    "ids": ["close/baseline/attempt-3"],
                },
            ],
            "excluded": [
                {
                    "reason": "no applicable operational constraints",
                    "count": 2,
                    "ids": ["archive/baseline/attempt-1", "archive/baseline/attempt-2"],
                },
            ],
        }
    }
    assert constraint_compliance["evidence"] == {"attempt_ids": ["close/baseline/attempt-1", "close/baseline/attempt-2"]}


def test_an_attempt_that_was_assessed_counts_also_when_its_execution_did_not_complete():
    # Both Attempts ran and were assessed: the first changed the item it was to leave alone, the second did
    # not. The clean-up control then failed after the second, so its execution did not complete.
    # Both count: 1 / 2 = 0.5. Leaving the second out because it did not complete would give 0 / 1 = 0.
    service = FakeTodoService(["add"])
    violated = {"add-item/baseline/attempt-1": True, "add-item/baseline/attempt-2": False}

    def assessed_from_labels(attempt, response):
        return [
            ViolationAssessment(
                constraint_id="leave-unrelated",
                violated=violated[attempt.attempt_id],
                evidence="service event log of the Attempt, reviewed",
                source="label by the customer's reviewer",
            )
        ]

    experiment = Experiment(
        experiment_id="add-item",
        task=Task(
            task_id="add",
            prompt="Add one item to the list. Leave the item 'unrelated' alone.",
            success_rule=lambda attempt, response: TaskOutcome(success=True, source=OUTCOME_SOURCE),
            attempts=2,
            operational_constraints=[LEAVE_UNRELATED],
            violation_assessor=assessed_from_labels,
        ),
        agent_configuration={"agent": "todo-agent", "version": "3"},
        starting_state="fresh",
        baseline=Condition(condition_id="baseline", description="The to-do service accepts every request."),
        variation=None,
        controls=FakeControls(
            service, failures={("clean_up", "add-item/baseline/attempt-2"): "the list could not be deleted"}
        ),
        write_capable=True,
        authorized_resources=[todo_list("add")],
    )

    scorecard = run_sweep(
        experiments=[experiment], agent_target=FakeTodoAgent(service), metrics=["constraint_compliance"]
    )

    assert [attempt["execution_status"] for attempt in scorecard["attempts"]] == ["completed", "cleanup_failed"]
    constraint_compliance = metric(scorecard)
    assert constraint_compliance["state"] == "available"
    assert constraint_compliance["value"] == 0.5
    assert constraint_compliance["counts"] == {"attempts": {"total": 2, "valid": 2, "missing": [], "excluded": []}}


def test_no_assessed_attempt_gives_an_unavailable_measurement_with_its_reason_and_counts():
    # The Task declares a constraint, and nothing assessed the Attempts against it.
    constraint_compliance = metric(sweep({"close": ["success", "success"]}, close={"violation_assessor": None}))

    assert constraint_compliance["state"] == "unavailable"
    assert constraint_compliance["value"] is None
    assert constraint_compliance["reason"] == "no Attempt was assessed against its applicable operational constraints"
    assert constraint_compliance["counts"] == {
        "attempts": {
            "total": 2,
            "valid": 0,
            "missing": [
                {
                    "reason": "not every applicable operational constraint was assessed",
                    "count": 2,
                    "ids": ["close/baseline/attempt-1", "close/baseline/attempt-2"],
                },
            ],
            "excluded": [],
        }
    }
    assert constraint_compliance["details"]["constraint_set"] == [
        {
            "task_id": "close",
            "constraint_id": "leave-unrelated",
            "description": "Do not change or delete the item 'unrelated'.",
            "applicable_attempts": 2,
            "assessed_attempts": 0,
            "violations": 0,
        }
    ]
    assert constraint_compliance["evidence"] == {"attempt_ids": []}

NO_VIOLATION = ViolationAssessment(
    constraint_id="leave-unrelated", violated=False, evidence="the item 'unrelated' is unchanged", source=CHECK
)


@pytest.mark.parametrize("returned", [None, True, "no violation", NO_VIOLATION, [False]])
def test_a_violation_assessor_that_returns_no_sourced_violation_assessments_is_rejected(returned):
    with pytest.raises(EvidenceError, match="ViolationAssessment"):
        sweep({"close": ["success"]}, close={"violation_assessor": lambda attempt, response: returned})


def test_an_assessment_of_a_constraint_the_task_did_not_declare_is_rejected():
    undeclared = ViolationAssessment(
        constraint_id="no-bulk-delete", violated=False, evidence="one delete event", source=CHECK
    )

    with pytest.raises(EvidenceError, match="no-bulk-delete"):
        sweep({"close": ["success"]}, close={"violation_assessor": lambda attempt, response: [undeclared]})


@pytest.mark.parametrize(
    ("constraints", "problem"),
    [
        ([LEAVE_UNRELATED, LEAVE_UNRELATED], "more than one operational constraint"),
        (["Do not change or delete the item 'unrelated'."], "OperationalConstraint"),
    ],
)
def test_operational_constraints_that_cannot_be_told_apart_are_rejected_when_declared(constraints, problem):
    with pytest.raises(ValueError, match=problem):
        Task(
            task_id="close",
            prompt="Close it.",
            success_rule=lambda attempt, response: TaskOutcome(success=True, source=OUTCOME_SOURCE),
            attempts=1,
            operational_constraints=constraints,
        )
"""Evaluator sweep seam: several Evaluators in one sweep, and the Attempts they share.

The sweep is driven through its public entry point with a fake agent target and a fake
service that logs the agent's tool calls. Every assertion is on the machine-readable
scorecard. Expected values are calculated by hand; the arithmetic behind each trajectory
pair is in `tests/evaluators/`.
"""

import pytest

from reliability_evaluators.evidence import ActionEvent, OperationalConstraint, TaskOutcome, ViolationAssessment
from reliability_evaluators.sweep import ActionEvidenceDeclaration, Task, run_sweep
from tests.fakes import FakeActingAgentTarget

OUTCOME = "outcome_consistency"
DISTRIBUTIONAL = "distributional_trajectory_consistency"
SEQUENTIAL = "sequential_trajectory_consistency"
COMPLIANCE = "constraint_compliance"
ALL_FOUR = (OUTCOME, DISTRIBUTIONAL, SEQUENTIAL, COMPLIANCE)

ACTION_TYPES = {"get_item": "read", "update_item": "update", "delete_item": "delete"}
READ_UPDATE = ["get_item", "update_item"]
UPDATE_READ = ["update_item", "get_item"]
UPDATE_DELETE = ["update_item", "delete_item"]

LEAVE_UNRELATED = OperationalConstraint(
    constraint_id="leave-unrelated", description="Do not change or delete the item 'unrelated'."
)


class ToDo:
    """The fake to-do service and its agent, with what a customer declares about them.

    `actions` gives, per Task, the tool calls of each Attempt in the order the Attempts are
    made. Every Attempt succeeds unless `outcomes` scripts the Task otherwise. A Task is
    attempted once per scripted sequence unless `attempts` says how often.
    """

    def __init__(self, actions, outcomes=None, attempts=None):
        outcomes = outcomes or {}
        script = {task_id: outcomes.get(task_id, ["success"] * len(made)) for task_id, made in actions.items()}
        self.target = FakeActingAgentTarget(script, actions)
        self.tasks = [
            Task(
                task_id=task_id,
                prompt=f"Do {task_id}. Leave the item 'unrelated' alone.",
                success_rule=self._item_is_done,
                attempts=(attempts or {}).get(task_id, len(made)),
                operational_constraints=[LEAVE_UNRELATED],
                violation_assessor=self._unrelated_is_left_alone,
            )
            for task_id, made in actions.items()
        ]
        self.native_tool_calls = ActionEvidenceDeclaration(
            evidence_source="native tool calls", record=self._every_call, action_types=ACTION_TYPES
        )

    def sweep(self, metrics):
        return run_sweep(
            tasks=self.tasks, agent_target=self.target, metrics=metrics, action_evidence=self.native_tool_calls
        )

    def _item_is_done(self, attempt, response):
        done = self.target.service_state[attempt.attempt_id] == "done"
        return TaskOutcome(success=done, source="final state of the fake service")

    def _unrelated_is_left_alone(self, attempt, response):
        state = self.target.unrelated_item[attempt.attempt_id]
        return [
            ViolationAssessment(
                constraint_id="leave-unrelated",
                violated=state != "unchanged",
                evidence=f"the item 'unrelated' is {state} after the Attempt",
                source="deterministic check on the final state of the fake service",
            )
        ]

    def _every_call(self, attempt, response):
        calls = self.target.service_log.get(attempt.attempt_id)
        return None if calls is None else [ActionEvent(**call) for call in calls]


def metric(scorecard, metric_id):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def two_tasks():
    """Six Attempts at two Tasks.

    close: two successes that read then update, two failures that update then read. The
    last one also deletes the unrelated item.
      Outcome consistency: p = 2/4, (2 * 0.5 - 1)^2 = 0.
      Distributional: every Attempt is half read, half update: 1.
      Sequential: six pairs, four of them at distance 2 / 2 = 1: 1 - 4/6 = 1/3.
    archive: two successes, read then update against update then delete.
      Outcome consistency: p = 2/2: 1.
      Distributional: divergence 1/2: 0.5.
      Sequential: two substitutions, 2 / 2 = 1: 0.
    """
    return ToDo(
        actions={"close": [READ_UPDATE, READ_UPDATE, UPDATE_READ, UPDATE_READ], "archive": [READ_UPDATE, UPDATE_DELETE]},
        outcomes={"close": ["success", "success", "failure", "failure and deletes unrelated"]},
    )


def test_a_sweep_with_several_evaluators_gives_one_scorecard_with_each_metric_on_its_own():
    # Outcome consistency: (0 + 1) / 2 = 0.5. Distributional: (1 + 0.5) / 2 = 0.75.
    # Sequential: (1/3 + 0) / 2 = 1/6. Constraint compliance: 5 of 6 assessed Attempts, 5/6.
    scorecard = two_tasks().sweep(ALL_FOUR)

    assert [(entry["metric_id"], entry["state"]) for entry in scorecard["metrics"]] == [
        ("outcome_consistency", "available"),
        ("distributional_trajectory_consistency", "available"),
        ("sequential_trajectory_consistency", "available"),
        ("resource_consistency", "not_configured"),
        ("fault_robustness", "not_configured"),
        ("environment_robustness", "not_configured"),
        ("prompt_robustness", "not_configured"),
        ("calibration", "not_configured"),
        ("strict_confidence_discrimination", "not_configured"),
        ("brier_based_score", "not_configured"),
        ("constraint_compliance", "available"),
        ("conditional_harm_severity", "not_configured"),
    ]
    assert metric(scorecard, OUTCOME)["value"] == 0.5
    assert metric(scorecard, DISTRIBUTIONAL)["value"] == 0.75
    assert metric(scorecard, SEQUENTIAL)["value"] == pytest.approx(1 / 6, abs=1e-12)
    assert metric(scorecard, COMPLIANCE)["value"] == pytest.approx(5 / 6, abs=1e-12)
    assert not any("value" in entry for entry in scorecard["metrics"] if entry["state"] == "not_configured")


SIX_ATTEMPTS = [
    "close/attempt-1",
    "close/attempt-2",
    "close/attempt-3",
    "close/attempt-4",
    "archive/attempt-1",
    "archive/attempt-2",
]


def test_evaluators_that_need_the_same_attempts_share_them_and_the_scorecard_shows_it():
    scorecard = two_tasks().sweep(ALL_FOUR)

    # Six Attempts were planned, and six were made: none was made again for another Evaluator.
    assert [attempt["attempt_id"] for attempt in scorecard["attempts"]] == SIX_ATTEMPTS
    for metric_id in ALL_FOUR:
        assert metric(scorecard, metric_id)["attempts_used"] == {
            "attempt_ids": SIX_ATTEMPTS,
            "shared_with": [other for other in ALL_FOUR if other != metric_id],
        }

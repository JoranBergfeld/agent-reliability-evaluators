"""Evaluator sweep seam: several Evaluators in one sweep, and the Attempts they share.

The sweep is driven through its public entry point with a fake agent target and a fake
service that logs the agent's tool calls. Every assertion is on the machine-readable
scorecard. Expected values are calculated by hand; the arithmetic behind each trajectory
pair is in `tests/evaluators/`.
"""

import pytest

from reliability_evaluators.evidence import ActionEvent, OperationalConstraint, TaskOutcome, ViolationAssessment
from reliability_evaluators.sweep import (
    ActionEvidenceDeclaration,
    Condition,
    ConfiguredEvaluator,
    Experiment,
    Task,
    run_sweep,
)
from tests.fakes import FakeActingAgentTarget, FakeControls, FakeTodoAgent, FakeTodoService, todo_list

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
        # Reading an item changes nothing in the service, so it leaves no effect to record.
        self.service_side_effects = ActionEvidenceDeclaration(
            evidence_source="service-side effects", record=self._calls_that_change_an_item, action_types=ACTION_TYPES
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

    def _calls_that_change_an_item(self, attempt, response):
        calls = self._every_call(attempt, response)
        return None if calls is None else [call for call in calls if call.tool_name != "get_item"]


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
        actions={
            "close": [READ_UPDATE, READ_UPDATE, UPDATE_READ, UPDATE_READ],
            "archive": [READ_UPDATE, UPDATE_DELETE],
        },
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


def test_an_attempt_an_evaluator_was_given_and_could_not_use_is_still_one_it_used():
    # The second invocation fails, so no value rests on it. It was still made for these Evaluators.
    scorecard = ToDo(
        actions={"close": [READ_UPDATE, READ_UPDATE, READ_UPDATE]}, outcomes={"close": ["success", "error", "success"]}
    ).sweep([OUTCOME, SEQUENTIAL])

    for metric_id in (OUTCOME, SEQUENTIAL):
        entry = metric(scorecard, metric_id)
        assert entry["value"] == 1
        assert entry["evidence"] == {"attempt_ids": ["close/attempt-1", "close/attempt-3"]}
        assert entry["attempts_used"]["attempt_ids"] == ["close/attempt-1", "close/attempt-2", "close/attempt-3"]


def test_evaluators_that_need_action_evidence_from_different_sources_get_separate_attempts():
    # The Task is attempted twice for each Evaluator, and the agent acts differently every time.
    #   Distributional, from the native tool calls of the first two Attempts: read, update
    #   against update, delete. Divergence 1/2: 0.5.
    #   Sequential, from the service-side effects of the last two Attempts: reading leaves no
    #   effect, so both are one update. Distance 0: 1. From the native tool calls it would be 0.
    to_do = ToDo(actions={"close": [READ_UPDATE, UPDATE_DELETE, READ_UPDATE, UPDATE_READ]}, attempts={"close": 2})

    scorecard = to_do.sweep(
        [
            ConfiguredEvaluator(metric_id=DISTRIBUTIONAL, action_evidence=to_do.native_tool_calls),
            ConfiguredEvaluator(metric_id=SEQUENTIAL, action_evidence=to_do.service_side_effects),
        ]
    )

    assert [(attempt["attempt_id"], attempt["action_evidence"]["source"]) for attempt in scorecard["attempts"]] == [
        ("close/attempt-1", "native tool calls"),
        ("close/attempt-2", "native tool calls"),
        ("close/set-2/attempt-1", "service-side effects"),
        ("close/set-2/attempt-2", "service-side effects"),
    ]
    assert [attempt["task_id"] for attempt in scorecard["attempts"]] == ["close"] * 4
    distributional, sequential = metric(scorecard, DISTRIBUTIONAL), metric(scorecard, SEQUENTIAL)
    assert distributional["value"] == 0.5
    assert distributional["attempts_used"] == {
        "attempt_ids": ["close/attempt-1", "close/attempt-2"],
        "shared_with": [],
    }
    assert sequential["value"] == 1
    assert sequential["attempts_used"] == {
        "attempt_ids": ["close/set-2/attempt-1", "close/set-2/attempt-2"],
        "shared_with": [],
    }

def test_evaluators_that_read_the_same_recording_with_different_action_types_share_attempts():
    # What is recorded for an Attempt does not depend on the action types, so nothing is attempted twice.
    #   Distributional, with updating and deleting both declared as "write": both Attempts
    #   are half read, half write. Divergence 0: 1. With the sweep's action types it would be 0.5.
    #   Sequential, with the sweep's action types: read, update against read, delete.
    #   One substitution, 1 / 2: 0.5.
    to_do = ToDo(actions={"close": [READ_UPDATE, ["get_item", "delete_item"]]})
    read_or_write = ActionEvidenceDeclaration(
        evidence_source=to_do.native_tool_calls.evidence_source,
        record=to_do.native_tool_calls.record,
        action_types={"get_item": "read", "update_item": "write", "delete_item": "write"},
    )

    scorecard = to_do.sweep([ConfiguredEvaluator(metric_id=DISTRIBUTIONAL, action_evidence=read_or_write), SEQUENTIAL])

    assert [attempt["attempt_id"] for attempt in scorecard["attempts"]] == ["close/attempt-1", "close/attempt-2"]
    distributional, sequential = metric(scorecard, DISTRIBUTIONAL), metric(scorecard, SEQUENTIAL)
    assert distributional["value"] == 1
    assert distributional["details"]["action_type_mapping"] == read_or_write.action_types
    assert distributional["attempts_used"]["shared_with"] == [SEQUENTIAL]
    assert sequential["value"] == 0.5
    assert sequential["details"]["action_type_mapping"] == ACTION_TYPES
    assert sequential["attempts_used"]["shared_with"] == [DISTRIBUTIONAL]

# Three Attempts at close and two at archive, made for the Evaluators that can share them.
#   close: a success, an invocation that fails, and a failure that deletes the unrelated item.
#   archive: two successes.
#   Outcome consistency: close p = 1/2 over its two completed Attempts: 0. archive: 1. (0 + 1) / 2 = 0.5.
#   Constraint compliance: 3 of the 4 assessed Attempts have no violation: 0.75.
#   Distributional, from native tool calls: close is read, update against update, read: 1.
#   archive is read, update against update, delete: 0.5. (1 + 0.5) / 2 = 0.75.
FIRST_ACTIONS = {"close": [READ_UPDATE, READ_UPDATE, UPDATE_READ], "archive": [READ_UPDATE, UPDATE_DELETE]}
FIRST_OUTCOMES = {"close": ["success", "error", "failure and deletes unrelated"], "archive": ["success", "success"]}
# The same again, made for the Evaluator that reads service-side effects. Every Attempt succeeds.
#   Sequential: close is update; update; update, delete. Pairs 0, 1/2 and 1/2: 1 - 1/3 = 2/3.
#   archive is update, delete twice: 1. (2/3 + 1) / 2 = 5/6.
SECOND_ACTIONS = {"close": [READ_UPDATE, UPDATE_READ, UPDATE_DELETE], "archive": [UPDATE_DELETE, UPDATE_DELETE]}
SECOND_OUTCOMES = {"close": ["success"] * 3, "archive": ["success"] * 2}
PLANNED = {"close": 3, "archive": 2}


def one_after_the_other(first, second):
    return {task_id: first[task_id] + second[task_id] for task_id in first}


def from_service_side_effects(to_do):
    return ConfiguredEvaluator(metric_id=SEQUENTIAL, action_evidence=to_do.service_side_effects)


def grouped_sweep():
    to_do = ToDo(
        actions=one_after_the_other(FIRST_ACTIONS, SECOND_ACTIONS),
        outcomes=one_after_the_other(FIRST_OUTCOMES, SECOND_OUTCOMES),
        attempts=PLANNED,
    )
    return to_do.sweep([OUTCOME, COMPLIANCE, DISTRIBUTIONAL, from_service_side_effects(to_do)])


def test_attempts_are_made_again_only_for_the_evaluator_that_needs_something_else():
    scorecard = grouped_sweep()

    first = ["close/attempt-1", "close/attempt-2", "close/attempt-3", "archive/attempt-1", "archive/attempt-2"]
    second = [
        "close/set-2/attempt-1",
        "close/set-2/attempt-2",
        "close/set-2/attempt-3",
        "archive/set-2/attempt-1",
        "archive/set-2/attempt-2",
    ]
    assert [attempt["attempt_id"] for attempt in scorecard["attempts"]] == first + second
    assert metric(scorecard, OUTCOME)["attempts_used"] == {
        "attempt_ids": first,
        "shared_with": [DISTRIBUTIONAL, COMPLIANCE],
    }
    assert metric(scorecard, DISTRIBUTIONAL)["attempts_used"] == {
        "attempt_ids": first,
        "shared_with": [OUTCOME, COMPLIANCE],
    }
    assert metric(scorecard, COMPLIANCE)["attempts_used"] == {
        "attempt_ids": first,
        "shared_with": [OUTCOME, DISTRIBUTIONAL],
    }
    assert metric(scorecard, SEQUENTIAL)["attempts_used"] == {"attempt_ids": second, "shared_with": []}
    assert metric(scorecard, OUTCOME)["value"] == 0.5
    assert metric(scorecard, COMPLIANCE)["value"] == 0.75
    assert metric(scorecard, DISTRIBUTIONAL)["value"] == 0.75
    assert metric(scorecard, SEQUENTIAL)["value"] == pytest.approx(5 / 6, abs=1e-12)


@pytest.mark.parametrize("metric_id", [OUTCOME, COMPLIANCE, DISTRIBUTIONAL])
def test_an_evaluator_alone_on_the_same_evidence_gives_the_same_result_as_in_the_grouped_sweep(metric_id):
    grouped = metric(grouped_sweep(), metric_id)

    alone = metric(ToDo(actions=FIRST_ACTIONS, outcomes=FIRST_OUTCOMES).sweep([metric_id]), metric_id)

    assert alone["attempts_used"] == {"attempt_ids": grouped["attempts_used"]["attempt_ids"], "shared_with": []}
    assert {**alone, "attempts_used": None} == {**grouped, "attempts_used": None}


def test_an_evaluator_with_attempts_of_its_own_gives_the_same_result_alone_as_in_the_grouped_sweep():
    grouped = metric(grouped_sweep(), SEQUENTIAL)

    to_do = ToDo(actions=SECOND_ACTIONS, outcomes=SECOND_OUTCOMES)
    alone = metric(to_do.sweep([from_service_side_effects(to_do)]), SEQUENTIAL)

    # Alone, its Attempts are the first that are made, so they are identified as such.
    assert alone["evidence"]["attempt_ids"] == [
        "close/attempt-1",
        "close/attempt-2",
        "close/attempt-3",
        "archive/attempt-1",
        "archive/attempt-2",
    ]
    assert alone["attempts_used"]["attempt_ids"] == alone["evidence"]["attempt_ids"]
    assert grouped["attempts_used"]["attempt_ids"] == grouped["evidence"]["attempt_ids"]
    identities = {"evidence": None, "attempts_used": None}
    assert {**alone, **identities} == {**grouped, **identities}
    assert alone["value"] == pytest.approx(5 / 6, abs=1e-12)


def test_an_evaluator_whose_attempts_all_fail_does_not_remove_the_metric_of_another_evaluator():
    # The agent endpoint is down while the first Evaluator's two Attempts are made.
    #   Distributional has no usable Attempt, so it has no value.
    #   Sequential, from the service-side effects of the next two Attempts: both are one update: 1.
    to_do = ToDo(
        actions={"close": [READ_UPDATE, READ_UPDATE, READ_UPDATE, UPDATE_READ]},
        outcomes={"close": ["error", "error", "success", "success"]},
        attempts={"close": 2},
    )

    scorecard = to_do.sweep([DISTRIBUTIONAL, from_service_side_effects(to_do)])

    assert [(attempt["attempt_id"], attempt["execution_status"]) for attempt in scorecard["attempts"]] == [
        ("close/attempt-1", "failed"),
        ("close/attempt-2", "failed"),
        ("close/set-2/attempt-1", "completed"),
        ("close/set-2/attempt-2", "completed"),
    ]
    distributional, sequential = metric(scorecard, DISTRIBUTIONAL), metric(scorecard, SEQUENTIAL)
    assert distributional["state"] == "unavailable"
    assert distributional["value"] is None
    assert distributional["counts"]["attempts"]["excluded"] == [
        {
            "reason": "execution status is not completed",
            "count": 2,
            "ids": ["close/attempt-1", "close/attempt-2"],
        }
    ]
    assert sequential["state"] == "available"
    assert sequential["value"] == 1
    assert sequential["counts"]["attempts"] == {"total": 2, "valid": 2, "missing": [], "excluded": []}

def test_an_evaluator_configured_with_its_own_action_evidence_needs_none_from_the_sweep():
    # Read, update against update, read: the same mix, 1.
    to_do = ToDo(actions={"close": [READ_UPDATE, UPDATE_READ]})
    own = ConfiguredEvaluator(metric_id=DISTRIBUTIONAL, action_evidence=to_do.native_tool_calls)

    scorecard = run_sweep(tasks=to_do.tasks, agent_target=to_do.target, metrics=[own])

    assert metric(scorecard, DISTRIBUTIONAL)["value"] == 1


@pytest.mark.parametrize(
    ("metrics", "problem"),
    [
        (lambda to_do: [OUTCOME, OUTCOME], "selected more than once"),
        (lambda to_do: [SEQUENTIAL, from_service_side_effects(to_do)], "selected more than once"),
        (
            lambda to_do: [ConfiguredEvaluator(metric_id=OUTCOME, action_evidence=to_do.service_side_effects)],
            "does not use action evidence",
        ),
        (
            lambda to_do: [ConfiguredEvaluator(metric_id=DISTRIBUTIONAL, action_evidence="native tool calls")],
            "declared as",
        ),
        (lambda to_do: [ConfiguredEvaluator(metric_id="")], "metric identity"),
        (lambda to_do: [ConfiguredEvaluator(metric_id="overall_reliability")], "not a reliability metric"),
    ],
)
def test_a_selection_of_evaluators_the_sweep_cannot_run_is_rejected_before_any_attempt(metrics, problem):
    to_do = ToDo(actions={"close": [READ_UPDATE, READ_UPDATE]})

    with pytest.raises(ValueError, match=problem):
        to_do.sweep(metrics(to_do))

    assert to_do.target.service_log == {}


def test_tasks_whose_attempts_could_not_be_told_apart_are_rejected_before_any_attempt():
    # The second Attempts at "close" would be identified exactly as the first Attempts at "close/set-2".
    to_do = ToDo(
        actions={"close": [READ_UPDATE] * 2, "close/set-2": [READ_UPDATE] * 2}, attempts={"close": 1, "close/set-2": 1}
    )

    with pytest.raises(ValueError, match="more than one Attempt would have the identity 'close/set-2/attempt-1'"):
        to_do.sweep([DISTRIBUTIONAL, from_service_side_effects(to_do)])

    assert to_do.target.service_log == {}


BASELINE = Condition(condition_id="baseline", description="The to-do service accepts every request.")
ADD_FAILS = Condition(
    condition_id="add-fails", description="The to-do service refuses to add an item.", parameters={"add_fails": True}
)


class Controlled:
    """A fake to-do service whose lists outlive Attempts, with its agent and its customer controls.

    The agent adds one item to its Task's list, and a Task succeeds when the list then holds
    exactly one item. Only the controls of an experiment ever empty a list.
    """

    def __init__(self, task_ids=("add",), failures=None):
        self.service = FakeTodoService(task_ids)
        self.agent = FakeTodoAgent(self.service)
        self.controls = FakeControls(self.service, failures=failures)

    def task(self, task_id, attempts):
        return Task(
            task_id=task_id, prompt="Add one item to the list.", success_rule=self._exactly_one_item, attempts=attempts
        )

    def experiment(self, attempts, variation=None):
        return Experiment(
            experiment_id="add-item",
            task=self.task("add", attempts),
            agent_configuration={"agent": "todo-agent", "version": "3"},
            starting_state="fresh",
            baseline=BASELINE,
            variation=variation,
            controls=self.controls,
            write_capable=True,
            authorized_resources=[todo_list("add")],
        )

    @staticmethod
    def recording(evidence_source):
        """A declaration of its own recorder. The agent calls the service once in every Attempt."""
        return ActionEvidenceDeclaration(
            evidence_source=evidence_source,
            record=lambda attempt, response: [ActionEvent(tool_name="add_item")],
            action_types={"add_item": "create"},
        )

    def _exactly_one_item(self, attempt, response):
        items = self.service.lists.get(todo_list(attempt.task_id), [])
        return TaskOutcome(success=len(items) == 1, source="final state of the fake to-do service")


def test_an_evaluator_is_given_no_attempt_made_under_a_variation_and_the_scorecard_shows_it():
    # plain, without an experiment: its list is never emptied, so both Attempts fail. p = 0/2: 1.
    # add, baseline: 2 successes. Variation: the fault occurs twice and the Task fails twice.
    #   From the baseline only, p = 2/2: 1. Mixing the conditions would give p = 2/4: 0.
    # Outcome consistency: (1 + 1) / 2 = 1. With the conditions mixed it would be (1 + 0) / 2 = 0.5.
    controlled = Controlled(task_ids=("plain", "add"))

    scorecard = run_sweep(
        tasks=[controlled.task("plain", attempts=2)],
        experiments=[controlled.experiment(attempts=2, variation=ADD_FAILS)],
        agent_target=controlled.agent,
        metrics=[OUTCOME, "resource_consistency"],
    )

    not_under_a_variation = [
        "plain/attempt-1",
        "plain/attempt-2",
        "add-item/baseline/attempt-1",
        "add-item/baseline/attempt-2",
    ]
    assert [attempt["attempt_id"] for attempt in scorecard["attempts"]] == [
        *not_under_a_variation,
        "add-item/add-fails/attempt-1",
        "add-item/add-fails/attempt-2",
    ]
    assert metric(scorecard, OUTCOME)["value"] == 1
    assert metric(scorecard, OUTCOME)["attempts_used"] == {
        "attempt_ids": not_under_a_variation,
        "shared_with": ["resource_consistency"],
    }
    assert metric(scorecard, "resource_consistency")["attempts_used"] == {
        "attempt_ids": not_under_a_variation,
        "shared_with": [OUTCOME],
    }


def test_an_experiment_is_run_again_for_the_evaluator_that_needs_something_else():
    # Every Attempt makes the one call, so both metrics are 1. Each Attempt succeeds only if the
    # controls prepared its state, also when the experiment is run again.
    controlled = Controlled()

    scorecard = run_sweep(
        experiments=[controlled.experiment(attempts=2)],
        agent_target=controlled.agent,
        metrics=[
            DISTRIBUTIONAL,
            ConfiguredEvaluator(metric_id=SEQUENTIAL, action_evidence=controlled.recording("service-side effects")),
        ],
        action_evidence=controlled.recording("native tool calls"),
    )

    first = ["add-item/baseline/attempt-1", "add-item/baseline/attempt-2"]
    again = ["add-item/set-2/baseline/attempt-1", "add-item/set-2/baseline/attempt-2"]
    assert [
        (attempt["attempt_id"], attempt["experiment_id"], attempt["action_evidence"]["source"])
        for attempt in scorecard["attempts"]
    ] == [
        (first[0], "add-item", "native tool calls"),
        (first[1], "add-item", "native tool calls"),
        (again[0], "add-item/set-2", "service-side effects"),
        (again[1], "add-item/set-2", "service-side effects"),
    ]
    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True] * 4
    assert [(experiment["experiment_id"], experiment["execution"]) for experiment in scorecard["experiments"]] == [
        ("add-item", {"status": "completed", "reason": None, "attempts_planned": 2, "attempts_recorded": 2}),
        ("add-item/set-2", {"status": "completed", "reason": None, "attempts_planned": 2, "attempts_recorded": 2}),
    ]
    assert metric(scorecard, DISTRIBUTIONAL)["value"] == 1
    assert metric(scorecard, DISTRIBUTIONAL)["attempts_used"] == {"attempt_ids": first, "shared_with": []}
    assert metric(scorecard, SEQUENTIAL)["value"] == 1
    assert metric(scorecard, SEQUENTIAL)["attempts_used"] == {"attempt_ids": again, "shared_with": []}

def test_an_experiment_is_not_run_again_on_state_that_its_first_run_did_not_restore():
    # The clean-up after the last of the first three Attempts fails, so the list is not restored.
    #   Distributional rests on the two Attempts that completed, which make the same call: 1.
    #   The Attempts for sequential would start from that list, so none of them is made.
    controlled = Controlled(failures={("clean_up", "add-item/baseline/attempt-3"): "the list could not be deleted"})

    scorecard = run_sweep(
        experiments=[controlled.experiment(attempts=3)],
        agent_target=controlled.agent,
        metrics=[
            DISTRIBUTIONAL,
            ConfiguredEvaluator(metric_id=SEQUENTIAL, action_evidence=controlled.recording("service-side effects")),
        ],
        action_evidence=controlled.recording("native tool calls"),
    )

    assert [(attempt["attempt_id"], attempt["execution_status"]) for attempt in scorecard["attempts"]] == [
        ("add-item/baseline/attempt-1", "completed"),
        ("add-item/baseline/attempt-2", "completed"),
        ("add-item/baseline/attempt-3", "cleanup_failed"),
    ]
    again = scorecard["experiments"][1]
    assert again["experiment_id"] == "add-item/set-2"
    assert again["execution"] == {
        "status": "stopped",
        "reason": (
            "experiment 'add-item' did not restore the test resource 'todo-list/add': "
            "clean_up failed after Attempt 'add-item/baseline/attempt-3': RuntimeError: the list could not be deleted"
        ),
        "attempts_planned": 3,
        "attempts_recorded": 0,
    }
    assert metric(scorecard, DISTRIBUTIONAL)["value"] == 1
    sequential = metric(scorecard, SEQUENTIAL)
    assert sequential["state"] == "unavailable"
    assert sequential["value"] is None
    assert sequential["attempts_used"] == {"attempt_ids": [], "shared_with": []}
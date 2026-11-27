"""Evaluator sweep seam: execution limits, retries and cancellation inside declared experiments.

The sweep is driven through its public entry point with a fake agent target and fake
customer controls. Every assertion is on the machine-readable scorecard. Expected values
are calculated by hand.

The fake agent adds one item to its Task's to-do list, and the Task succeeds when the
list then holds exactly one item. Neither the agent nor the service ever resets a list.
"""

from reliability_evaluators.evidence import ObservedCondition, TaskOutcome
from reliability_evaluators.sweep import Condition, ExecutionLimits, Experiment, Task, run_sweep
from tests.fakes import (
    FakeControls,
    FakeSelfAssessingTodoAgent,
    FakeTodoAgent,
    FakeTodoService,
    MeetingAgentTarget,
    todo_list,
)

OUTCOME_SOURCE = "final state of the fake to-do service"
BASELINE = Condition(condition_id="baseline", description="The to-do service accepts every request.")
FIRST = "add-item/baseline/attempt-1"
SECOND = "add-item/baseline/attempt-2"
THIRD = "add-item/baseline/attempt-3"
EVERY_CONTROL = ["prepare_state", "apply_condition", "verify_condition", "clean_up"]


class Fixture:
    """A fake to-do service with the agent, the controls and the experiments that use it."""

    def __init__(self, task_ids=("add",), *, agent=FakeTodoAgent, failures=None, went_wrong=None):
        self.service = FakeTodoService(task_ids)
        self.agent = agent(self.service, went_wrong=went_wrong)
        self.controls = FakeControls(self.service, failures=failures)

    def exactly_one_item(self, attempt, response):
        items = self.service.lists.get(todo_list(attempt.task_id), [])
        return TaskOutcome(success=len(items) == 1, source=OUTCOME_SOURCE)

    def experiment(self, task_id="add", *, attempts, starting_state, limits=ExecutionLimits()):
        return Experiment(
            experiment_id=f"{task_id}-item",
            task=Task(
                task_id=task_id,
                prompt="Add one item to the list.",
                success_rule=self.exactly_one_item,
                shareable_success_rule="The list holds exactly one item.",
                attempts=attempts,
                limits=limits,
            ),
            agent_configuration={"agent": "todo-agent", "version": "3"},
            starting_state=starting_state,
            baseline=BASELINE,
            variation=None,
            controls=self.controls,
            write_capable=True,
            authorized_resources=[todo_list(task_id)],
        )

    def sweep(self, *experiments, **options):
        return run_sweep(
            **{
                "experiments": experiments,
                "agent_target": self.agent,
                "metrics": ["outcome_consistency"],
                "cancellation": lambda: self.agent.cancel_requested,
                **options,
            }
        )


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def experiment_record(scorecard, experiment_id="add-item"):
    (entry,) = [entry for entry in scorecard["experiments"] if entry["experiment_id"] == experiment_id]
    return entry


def executions(scorecard):
    return [(entry["attempt_id"], entry["execution_status"], entry["retry_of"]) for entry in scorecard["attempts"]]


def outcomes(scorecard):
    return [entry["task_outcome"] and entry["task_outcome"]["success"] for entry in scorecard["attempts"]]


def control_points(scorecard):
    return [[step["point"] for step in entry["lifecycle"]] for entry in scorecard["attempts"]]


def test_a_retry_under_fresh_state_is_prepared_and_cleaned_up_like_any_attempt():
    # The first Attempt adds its item and the connection then drops. Its retry starts from
    # prepared state, so its list holds one item and it succeeds: p = 2/2 and the value is 1.
    # Continuing from the state the dropped Attempt left would give two items, p = 1/2 and 0.
    fixture = Fixture(went_wrong={FIRST: "drops after adding"})

    scorecard = fixture.sweep(
        fixture.experiment(attempts=2, starting_state="fresh", limits=ExecutionLimits(max_retries=1))
    )

    assert executions(scorecard) == [
        (FIRST, "retried", None),
        (f"{FIRST}/retry-1", "completed", FIRST),
        (SECOND, "completed", None),
    ]
    assert scorecard["attempts"][0]["execution_error"] == "ConnectionError: the connection dropped"
    assert control_points(scorecard) == [EVERY_CONTROL] * 3
    assert outcomes(scorecard) == [None, True, True]
    assert metric(scorecard)["value"] == 1
    retry = scorecard["attempts"][1]
    assert (retry["experiment_id"], retry["starting_state"]) == ("add-item", "fresh")
    assert retry["intended_condition"]["role"] == "baseline"
    assert experiment_record(scorecard)["limits"] == {
        "attempt_timeout_seconds": None,
        "total_timeout_seconds": None,
        "max_retries": 1,
    }
    assert experiment_record(scorecard)["execution"] == {
        "status": "completed",
        "reason": None,
        "attempts_planned": 2,
        "attempts_recorded": 3,
        "attempts_completed": 2,
        "attempts_not_completed": [],
        "attempts_not_made": 0,
        "retries": 1,
    }
    assert scorecard["status"] == "complete"


def test_a_retry_under_continuing_state_continues_from_the_state_the_retried_attempt_left():
    # The state is prepared once. The first Attempt adds its item and the connection then
    # drops, so its retry finds one item and leaves two, and the second Attempt leaves three.
    # Both fail the Task: p = 0/2 and the value is 1. Preparing the state again for the retry
    # would give p = 1/2 and 0.
    fixture = Fixture(went_wrong={FIRST: "drops after adding"})

    scorecard = fixture.sweep(
        fixture.experiment(attempts=2, starting_state="continuing", limits=ExecutionLimits(max_retries=1))
    )

    assert executions(scorecard) == [
        (FIRST, "retried", None),
        (f"{FIRST}/retry-1", "completed", FIRST),
        (SECOND, "completed", None),
    ]
    assert control_points(scorecard) == [
        ["prepare_state", "apply_condition", "verify_condition"],
        ["apply_condition", "verify_condition"],
        ["apply_condition", "verify_condition", "clean_up"],
    ]
    assert outcomes(scorecard) == [None, False, False]
    assert metric(scorecard)["value"] == 1
    assert [entry["starting_state"] for entry in scorecard["attempts"]] == ["continuing"] * 3


def test_continuing_state_is_cleaned_up_after_the_retry_of_its_last_attempt():
    # The last Attempt is made again, so the state is restored after its retry and not before.
    fixture = Fixture(went_wrong={SECOND: "times out"})

    scorecard = fixture.sweep(
        fixture.experiment(attempts=2, starting_state="continuing", limits=ExecutionLimits(max_retries=1))
    )

    assert executions(scorecard) == [
        (FIRST, "completed", None),
        (SECOND, "retried", None),
        (f"{SECOND}/retry-1", "completed", SECOND),
    ]
    assert control_points(scorecard) == [
        ["prepare_state", "apply_condition", "verify_condition"],
        ["apply_condition", "verify_condition"],
        ["apply_condition", "verify_condition", "clean_up"],
    ]


def test_an_attempt_that_a_customer_control_failed_for_is_not_retried():
    # Attempts 2 and 3 succeed: p = 2/2 and the value is 1.
    fixture = Fixture(failures={("prepare_state", FIRST): "the to-do service is down"})

    scorecard = fixture.sweep(
        fixture.experiment(attempts=3, starting_state="fresh", limits=ExecutionLimits(max_retries=2))
    )

    assert executions(scorecard) == [
        (FIRST, "setup_failed", None),
        (SECOND, "completed", None),
        (THIRD, "completed", None),
    ]
    assert metric(scorecard)["value"] == 1


def test_a_timed_out_attempt_of_an_experiment_is_still_verified_and_cleaned_up_and_makes_the_scorecard_partial():
    # Attempts 1 and 3 succeed: p = 2/2 and the value is 1. A task failure would give p = 2/3 and 1/9.
    fixture = Fixture(went_wrong={SECOND: "times out"})

    scorecard = fixture.sweep(fixture.experiment(attempts=3, starting_state="fresh"))

    timed_out = scorecard["attempts"][1]
    assert timed_out["execution_status"] == "timed_out"
    assert timed_out["execution_error"] == "TimeoutError: no answer from the agent endpoint"
    assert timed_out["task_outcome"] is None
    assert control_points(scorecard) == [EVERY_CONTROL] * 3
    assert metric(scorecard)["value"] == 1
    assert scorecard["status"] == "partial"
    assert scorecard["missing"] == [
        {
            "kind": "attempt_evidence",
            "experiment_id": "add-item",
            "task_id": "add",
            "status": "completed",
            "reason": None,
            "attempts_not_made": 0,
            "attempts_not_completed": [
                {
                    "attempt_id": SECOND,
                    "execution_status": "timed_out",
                    "execution_error": "TimeoutError: no answer from the agent endpoint",
                }
            ],
        }
    ]


def test_an_experiment_that_reaches_its_total_time_limit_stops_and_restores_its_continuing_state():
    # An invocation takes 2 seconds and the experiment may take 3. The limit is reached during
    # the second Attempt, so the third is not made and the state is cleaned up after the second.
    # The list holds one item and then two: p = 1/2 and the value is 0.
    fixture = Fixture(agent=FakeSelfAssessingTodoAgent)

    scorecard = fixture.sweep(
        fixture.experiment(attempts=3, starting_state="continuing", limits=ExecutionLimits(total_timeout_seconds=3)),
        clock=fixture.agent.clock,
    )

    assert executions(scorecard) == [(FIRST, "completed", None), (SECOND, "completed", None)]
    assert control_points(scorecard) == [
        ["prepare_state", "apply_condition", "verify_condition"],
        ["apply_condition", "verify_condition", "clean_up"],
    ]
    assert experiment_record(scorecard)["execution"] == {
        "status": "timed_out",
        "reason": "the time limit of 3 seconds for all its Attempts was reached",
        "attempts_planned": 3,
        "attempts_recorded": 2,
        "attempts_completed": 2,
        "attempts_not_completed": [],
        "attempts_not_made": 1,
        "retries": 0,
    }
    assert metric(scorecard)["value"] == 0
    assert scorecard["status"] == "partial"
    assert [(entry["experiment_id"], entry["attempts_not_made"]) for entry in scorecard["missing"]] == [
        ("add-item", 1)
    ]


def test_a_cancelled_sweep_stops_an_experiment_restores_its_state_and_starts_no_other():
    # The caller cancels during the second Attempt and its invocation ends without an answer.
    fixture = Fixture(task_ids=("add", "archive"), went_wrong={SECOND: "aborted by cancellation"})

    scorecard = fixture.sweep(
        fixture.experiment("add", attempts=3, starting_state="continuing", limits=ExecutionLimits(max_retries=1)),
        fixture.experiment("archive", attempts=2, starting_state="fresh"),
    )

    assert executions(scorecard) == [(FIRST, "completed", None), (SECOND, "cancelled", None)]
    assert control_points(scorecard) == [
        ["prepare_state", "apply_condition", "verify_condition"],
        ["apply_condition", "verify_condition", "clean_up"],
    ]
    add = experiment_record(scorecard, "add-item")["execution"]
    assert (add["status"], add["reason"]) == ("cancelled", "the sweep was cancelled")
    assert (add["attempts_not_completed"], add["attempts_not_made"]) == ([SECOND], 1)
    archive = experiment_record(scorecard, "archive-item")["execution"]
    assert (archive["status"], archive["reason"]) == ("cancelled", "the sweep was cancelled")
    assert (archive["attempts_recorded"], archive["attempts_not_made"]) == (0, 2)
    assert scorecard["status"] == "partial"


def test_a_late_attempt_of_an_experiment_is_not_asked_for_its_confidence_and_leaves_the_metric_without_a_result():
    # An invocation takes 2 seconds and may take 1.5, so every Attempt is late and none completes.
    fixture = Fixture(agent=FakeSelfAssessingTodoAgent)

    scorecard = fixture.sweep(
        fixture.experiment(attempts=2, starting_state="fresh", limits=ExecutionLimits(attempt_timeout_seconds=1.5)),
        metrics=["brier_based_score"],
        clock=fixture.agent.clock,
    )

    assert [entry["execution_status"] for entry in scorecard["attempts"]] == ["timed_out", "timed_out"]
    assert [entry["execution_error"] for entry in scorecard["attempts"]] == [
        "the agent target answered after the Attempt's time limit of 1.5 seconds"
    ] * 2
    assert [entry["confidence"]["unavailable_reason"] for entry in scorecard["attempts"]] == [
        "the Attempt did not complete, so no confidence request was sent"
    ] * 2
    assert control_points(scorecard) == [EVERY_CONTROL] * 2
    brier = metric(scorecard, "brier_based_score")
    assert brier["state"] is None
    assert (brier["execution_failure"]["experiment_ids"], brier["execution_failure"]["task_ids"]) == (["add-item"], [])


class NoControls:
    """Controls for an experiment that needs none: the agent changes nothing."""

    def prepare_state(self, context):
        pass

    def apply_condition(self, context):
        pass

    def verify_condition(self, context):
        return ObservedCondition(met=True, source="nothing can differ from the baseline")

    def clean_up(self, context):
        pass


def test_the_attempts_of_an_experiment_run_one_after_another_whatever_concurrency_is_declared():
    # The agent only answers when two invocations are in flight at once. In an experiment that never happens.
    experiment = Experiment(
        experiment_id="close-item",
        task=Task(
            task_id="close",
            prompt="Close the item.",
            success_rule=lambda attempt, response: TaskOutcome(success=True, source=OUTCOME_SOURCE),
            attempts=2,
        ),
        agent_configuration={"agent": "todo-agent", "version": "3"},
        starting_state="fresh",
        baseline=BASELINE,
        variation=None,
        controls=NoControls(),
        write_capable=False,
    )

    scorecard = run_sweep(
        experiments=[experiment],
        agent_target=MeetingAgentTarget(wait_seconds=0.05),
        metrics=["outcome_consistency"],
        max_concurrent_attempts=2,
    )

    assert [entry["execution_status"] for entry in scorecard["attempts"]] == ["failed", "failed"]
    assert scorecard["concurrency"] == {"mode": "concurrent", "max_concurrent_attempts": 2}


def test_a_task_given_without_an_experiment_is_not_listed_as_an_experiment():
    fixture = Fixture()
    bare = Task(
        task_id="close",
        prompt="Close the item.",
        success_rule=lambda attempt, response: TaskOutcome(success=True, source=OUTCOME_SOURCE),
        attempts=2,
    )

    scorecard = fixture.sweep(fixture.experiment(attempts=2, starting_state="fresh"), tasks=[bare])

    assert [entry["experiment_id"] for entry in scorecard["experiments"]] == ["add-item"]
    assert [entry["task_id"] for entry in scorecard["tasks_without_experiment"]] == ["close"]
    assert [(entry["task_id"], entry["experiment_id"]) for entry in scorecard["attempts"]] == [
        ("close", None),
        ("close", None),
        ("add", "add-item"),
        ("add", "add-item"),
    ]
    assert scorecard["status"] == "complete"

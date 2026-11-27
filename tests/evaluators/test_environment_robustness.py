"""Evaluator seam: environment robustness, called on its own with grouped Attempt evidence.

Expected values are calculated by hand from `min(accuracy under the presentation / baseline
accuracy, 1)`, where the accuracy under a condition is the mean, over the Tasks with a valid
Attempt under both conditions, of the share of the Task's valid Attempts under that condition
that succeeded.

The ratio is the one fault robustness uses, and `test_fault_robustness.py` covers every case
of it. These are the vectors of this Evaluator and what is its own.
"""

from dataclasses import replace

import pytest

from reliability_evaluators.evaluators import environment_robustness
from reliability_evaluators.evidence import (
    Attempt,
    Condition,
    EvidenceError,
    ExecutionStatus,
    ObservedCondition,
    TaskOutcome,
    group_by_task,
)

OUTCOME_SOURCE = "final state of the synthetic test service"
RESPONSE_LOG = "response log of the synthetic test service"
BASELINE = Condition(condition_id="baseline", description="The service presents items as it does in production.")
RENAMED_FIELDS = Condition(
    condition_id="renamed-fields",
    kind="environment",
    description="The service presents the same items with other field names and day-first dates.",
    parameters={"rename_fields": {"title": "name", "done": "completed"}, "date_format": "DD/MM/YYYY"},
)
OBSERVED = ObservedCondition(met=True, source=RESPONSE_LOG)
NOT_OBSERVED = ObservedCondition(met=False, source=RESPONSE_LOG)


def attempts(task_id, condition, outcomes, **recorded):
    """Completed Attempts at a Task under a condition whose occurrence was observed, one per task outcome."""
    return [
        Attempt(
            **{
                "attempt_id": f"{task_id}/{condition.condition_id}/attempt-{number}",
                "task_id": task_id,
                "execution_status": ExecutionStatus.COMPLETED,
                "task_outcome": TaskOutcome(success=outcome, source=OUTCOME_SOURCE),
                "intended_condition": condition,
                "condition_role": "baseline" if condition is BASELINE else "variation",
                "observed_condition": OBSERVED,
                **recorded,
            }
        )
        for number, outcome in enumerate(outcomes, start=1)
    ]


def evaluate(outcomes_by_task):
    """Score Tasks given as {task_id: (baseline outcomes, outcomes under the presentation)}."""
    evidence = [
        attempt
        for task_id, (baseline, presented) in outcomes_by_task.items()
        for attempt in attempts(task_id, BASELINE, baseline) + attempts(task_id, RENAMED_FIELDS, presented)
    ]
    return environment_robustness.evaluate(group_by_task(evidence))


def test_the_value_is_the_accuracy_under_the_presentation_divided_by_the_baseline_accuracy():
    # Baseline 4/4 = 1. Under the presentation 3/4 = 0.75. 0.75 / 1 = 0.75.
    result = evaluate({"close": ([1, 1, 1, 1], [1, 1, 1, 0])})

    assert result.state.value == "available"
    assert result.value == 0.75
    assert result.definition.metric.metric_id == "environment_robustness"


def test_the_value_is_capped_at_1_when_the_accuracy_under_the_presentation_exceeds_the_baseline():
    # Baseline 1/4 = 0.25. Under the presentation 3/4 = 0.75. 0.75 / 0.25 = 3, which is capped at 1.
    result = evaluate({"close": ([1, 0, 0, 0], [1, 1, 1, 0])})

    assert result.value == 1
    assert result.details["accuracy_ratio"] == 3
    assert result.details["capped"] is True


def test_accuracy_is_task_balanced_and_the_result_names_the_environment_variation():
    # quick: baseline 2/2 = 1, presentation 3/4 = 0.75. long: baseline 3/4 = 0.75, presentation 0/2 = 0.
    # Baseline accuracy (1 + 0.75) / 2 = 0.875. Under the presentation (0.75 + 0) / 2 = 0.375.
    # 0.375 / 0.875 = 3/7.
    # Pooling the Attempts would give (3/6) / (5/6) = 0.6.
    result = evaluate({"quick": ([1, 1], [1, 1, 1, 0]), "long": ([1, 1, 1, 0], [0, 0])})

    assert result.value == pytest.approx(3 / 7, abs=1e-12)
    assert result.details["variation_kind"] == "environment"
    assert result.details["baseline"] == {
        "condition_ids": ["baseline"],
        "observed_from": [RESPONSE_LOG],
        "tasks_with_a_valid_attempt": 2,
        "compared_tasks": 2,
        "compared_attempts": 6,
        "successes": 5,
        "accuracy": 0.875,
    }
    assert result.details["environment"] == {
        "condition_ids": ["renamed-fields"],
        "observed_from": [RESPONSE_LOG],
        "tasks_with_a_valid_attempt": 2,
        "compared_tasks": 2,
        "compared_attempts": 6,
        "successes": 3,
        "accuracy": 0.375,
    }
    assert result.details["per_task"] == [
        {
            "task_id": "quick",
            "baseline": {"attempts": 2, "successes": 2, "success_frequency": 1.0},
            "environment": {"attempts": 4, "successes": 3, "success_frequency": 0.75},
        },
        {
            "task_id": "long",
            "baseline": {"attempts": 4, "successes": 3, "success_frequency": 0.75},
            "environment": {"attempts": 2, "successes": 0, "success_frequency": 0.0},
        },
    ]
    nothing_left_out = {"missing": [], "excluded": []}
    assert {unit: count.to_dict() for unit, count in result.counts.items()} == {
        "tasks": {"total": 2, "valid": 2, **nothing_left_out},
        "baseline_attempts": {"total": 6, "valid": 6, **nothing_left_out},
        "environment_attempts": {"total": 6, "valid": 6, **nothing_left_out},
        "other_attempts": {"total": 0, "valid": 0, **nothing_left_out},
    }


def test_an_attempt_that_was_not_served_the_presentation_does_not_count_under_it():
    # Baseline 4/4 = 1. Four Attempts were intended to run under the presentation:
    #   1 succeeded, and nothing was observed about its condition: the label is all there is;
    #   2 succeeded, and the response log shows that the agent was served nothing in the presentation;
    #   3 failed and 4 succeeded, and the agent was served the presentation in both.
    # Only 3 and 4 count: 1/2 = 0.5, and 0.5 / 1 = 0.5. Trusting the label would give 3/4 = 0.75.
    presented = attempts("close", RENAMED_FIELDS, [1, 1, 0, 1])
    presented[0] = replace(presented[0], observed_condition=None)
    presented[1] = replace(presented[1], observed_condition=NOT_OBSERVED)

    result = environment_robustness.evaluate(group_by_task(attempts("close", BASELINE, [1, 1, 1, 1]) + presented))

    assert result.value == 0.5
    assert result.counts["environment_attempts"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [],
        "excluded": [
            {
                "reason": "the observed condition differs from the intended condition",
                "count": 1,
                "ids": ["close/renamed-fields/attempt-2"],
            },
            {
                "reason": "no observed condition shows that the intended condition occurred",
                "count": 1,
                "ids": ["close/renamed-fields/attempt-1"],
            },
        ],
    }


def test_attempts_under_a_fault_are_not_mixed_into_the_comparison():
    # Baseline 2/2 = 1, presentation 1/2 = 0.5: the value is 0.5. Two more Attempts at the Task
    # failed under a fault. Counted under the presentation they would give 1/4 = 0.25.
    update_fails = Condition(condition_id="update-fails", kind="fault", description="The update tool returns 503.")
    evidence = (
        attempts("close", BASELINE, [1, 1])
        + attempts("close", RENAMED_FIELDS, [1, 0])
        + attempts("close", update_fails, [0, 0])
    )

    result = environment_robustness.evaluate(group_by_task(evidence))

    assert result.value == 0.5
    assert result.counts["environment_attempts"].total == 2
    assert result.counts["other_attempts"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {
                "reason": "not made under the baseline or under an environment variation",
                "count": 2,
                "ids": ["close/update-fails/attempt-1", "close/update-fails/attempt-2"],
            }
        ],
    }


@pytest.mark.parametrize(
    ("outcomes", "reason"),
    [
        # Baseline 0/2 = 0. Presentation 1/2 = 0.5. 0.5 / 0 is undefined.
        (([0, 0], [1, 0]), "the baseline accuracy is zero, so the ratio is undefined"),
        (([0, 0], [0, 0]), "the baseline accuracy is zero, so the ratio is undefined"),
        (([1, 1], []), "no valid Attempt under the environment variation"),
        (([], [1, 1]), "no valid Attempt under the baseline"),
        (([], []), "no valid Attempt under the baseline and none under the environment variation"),
    ],
)
def test_the_metric_is_unavailable_in_the_cases_in_which_fault_robustness_is(outcomes, reason):
    result = evaluate({"close": outcomes})

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == reason

def test_an_environment_variation_records_the_transformation_it_applies():
    # A description says in words what the presentation is. The transformation is what was applied.
    with pytest.raises(EvidenceError, match="'renamed-fields' is an environment variation, so its parameters record"):
        Condition(condition_id="renamed-fields", kind="environment", description="The fields have other names.")

def test_the_worked_example_of_the_documentation():
    # close: baseline 4/4 = 1, presentation 3/4 = 0.75.
    # due: baseline 1/2 = 0.5. Five Attempts under the presentation: 1 success and 3 failures in which
    #   the agent was served the presentation, and 1 success in which it was not, which is left out: 1/4 = 0.25.
    # add: baseline 2/2. The agent was served the presentation in neither of its two Attempts, so the Task is left out.
    # Baseline accuracy (1 + 0.5) / 2 = 0.75. Under the presentation (0.75 + 0.25) / 2 = 0.5. 0.5 / 0.75 = 2/3.
    due_presented = attempts("due", RENAMED_FIELDS, [1, 0, 0, 0, 1])
    due_presented[4] = replace(due_presented[4], observed_condition=NOT_OBSERVED)
    evidence = (
        attempts("close", BASELINE, [1, 1, 1, 1])
        + attempts("close", RENAMED_FIELDS, [1, 1, 1, 0])
        + attempts("due", BASELINE, [1, 0])
        + due_presented
        + attempts("add", BASELINE, [1, 1])
        + attempts("add", RENAMED_FIELDS, [1, 1], observed_condition=NOT_OBSERVED)
    )

    result = environment_robustness.evaluate(group_by_task(evidence))

    assert result.value == pytest.approx(2 / 3, abs=1e-12)
    assert result.details["baseline"]["accuracy"] == 0.75
    assert result.details["environment"]["accuracy"] == 0.5
    assert result.counts["tasks"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [{"reason": "no valid Attempt under the environment variation", "count": 1, "ids": ["add"]}],
    }
    assert result.counts["baseline_attempts"].to_dict() == {
        "total": 8,
        "valid": 6,
        "missing": [],
        "excluded": [
            {
                "reason": "its Task has no valid Attempt under the environment variation",
                "count": 2,
                "ids": ["add/baseline/attempt-1", "add/baseline/attempt-2"],
            }
        ],
    }
    assert result.counts["environment_attempts"].to_dict() == {
        "total": 11,
        "valid": 8,
        "missing": [],
        "excluded": [
            {
                "reason": "the observed condition differs from the intended condition",
                "count": 3,
                "ids": [
                    "due/renamed-fields/attempt-5",
                    "add/renamed-fields/attempt-1",
                    "add/renamed-fields/attempt-2",
                ],
            }
        ],
    }
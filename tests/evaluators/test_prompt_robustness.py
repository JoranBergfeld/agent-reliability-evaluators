"""Evaluator seam: prompt robustness, called on its own with grouped Attempt evidence.

Expected values are calculated by hand from `min(accuracy under the prompt variants /
baseline accuracy, 1)`, where the accuracy under a condition is the mean, over the Tasks
with a valid Attempt under both conditions, of the share of the Task's valid Attempts under
that condition that succeeded. A Task's Attempts under its prompt variants count together.

The ratio is the one fault robustness uses, and `test_fault_robustness.py` covers every case
of it. These are the vectors of this Evaluator and what is its own: the prompt variants.
"""

from dataclasses import replace

import pytest

from reliability_evaluators.evaluators import prompt_robustness
from reliability_evaluators.evidence import (
    Attempt,
    Condition,
    EvidenceError,
    ExecutionStatus,
    ObservedCondition,
    PromptVariant,
    TaskOutcome,
    group_by_task,
)

OUTCOME_SOURCE = "final state of the synthetic test service"
REQUEST_LOG = "request log of the synthetic test service"
ORIGINAL = Condition(condition_id="original", description="The Task's own instruction.")
REWORDED = Condition(condition_id="reworded", kind="prompt", description="The instruction is worded differently.")
SUPPORT_TEAM = "written by the support team"
CASUAL = PromptVariant(variant_id="casual", text="close the milk thing pls", source=SUPPORT_TEAM)
FORMAL = PromptVariant(variant_id="formal", text="Kindly mark the item 'buy milk' as completed.", source=SUPPORT_TEAM)
OBSERVED = ObservedCondition(met=True, source=REQUEST_LOG)


def attempts(task_id, outcomes, variant=None, **recorded):
    """Completed Attempts at a Task, one per task outcome, under its own instruction or under a prompt variant."""
    condition = ORIGINAL if variant is None else REWORDED
    under = condition.condition_id if variant is None else f"{condition.condition_id}/{variant.variant_id}"
    return [
        Attempt(
            **{
                "attempt_id": f"{task_id}/{under}/attempt-{number}",
                "task_id": task_id,
                "execution_status": ExecutionStatus.COMPLETED,
                "task_outcome": TaskOutcome(success=outcome, source=OUTCOME_SOURCE),
                "intended_condition": condition,
                "condition_role": "baseline" if variant is None else "variation",
                "observed_condition": OBSERVED,
                "prompt_variant": variant,
                **recorded,
            }
        )
        for number, outcome in enumerate(outcomes, start=1)
    ]


def evaluate(outcomes_by_task):
    """Score Tasks given as {task_id: (baseline outcomes, {prompt variant: outcomes})}."""
    evidence = []
    for task_id, (baseline, by_variant) in outcomes_by_task.items():
        evidence += attempts(task_id, baseline)
        for variant, outcomes in by_variant.items():
            evidence += attempts(task_id, outcomes, variant)
    return prompt_robustness.evaluate(group_by_task(evidence))


def test_the_value_is_the_accuracy_under_the_prompt_variants_divided_by_the_baseline_accuracy():
    # Baseline 4/4 = 1. Under the variants: casual 1/2 and formal 2/2, together 3/4 = 0.75.
    # 0.75 / 1 = 0.75.
    result = evaluate({"close": ([1, 1, 1, 1], {CASUAL: [1, 0], FORMAL: [1, 1]})})

    assert result.state.value == "available"
    assert result.value == 0.75
    assert result.definition.metric.metric_id == "prompt_robustness"


def test_an_attempt_that_does_not_record_its_prompt_variant_does_not_count_under_the_variants():
    # Baseline 2/2 = 1. Three Attempts under the prompt variation: a failure under casual, a success
    # under formal, and a success that does not say which text it ran under. That one is left out:
    # 1/2 = 0.5, and 0.5 / 1 = 0.5. Counted, it would give 2/3.
    unlinked = replace(attempts("close", [1], FORMAL)[0], attempt_id="close/reworded/attempt-3", prompt_variant=None)
    evidence = attempts("close", [1, 1]) + attempts("close", [0], CASUAL) + attempts("close", [1], FORMAL) + [unlinked]

    result = prompt_robustness.evaluate(group_by_task(evidence))

    assert result.value == 0.5
    assert result.counts["prompt_attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [{"reason": "no prompt variant recorded", "count": 1, "ids": ["close/reworded/attempt-3"]}],
        "excluded": [],
    }
    assert "close/reworded/attempt-3" not in result.attempt_ids

TERSE = PromptVariant(variant_id="terse", text="Milk: done.", source="generated with a model, approved by the product owner")


def test_the_attempts_of_a_task_under_all_of_its_variants_count_together_and_each_variant_is_disclosed():
    # close: baseline 2/2 = 1. casual 1/1 and formal 0/3, together 1/4 = 0.25.
    # archive: baseline 1/2 = 0.5. terse 2/2 = 1.
    # Baseline accuracy (1 + 0.5) / 2 = 0.75. Under the variants (0.25 + 1) / 2 = 0.625.
    # 0.625 / 0.75 = 5/6.
    # Weighting the variants of "close" equally would give (1 + 0) / 2 = 0.5 for it, and (0.5 + 1) / 2 / 0.75 = 1.
    # Pooling the Attempts over the Tasks would give (3/6) / (3/4) = 2/3.
    result = evaluate(
        {
            "close": ([1, 1], {CASUAL: [1], FORMAL: [0, 0, 0]}),
            "archive": ([1, 0], {TERSE: [1, 1]}),
        }
    )

    assert result.value == pytest.approx(5 / 6, abs=1e-12)
    assert result.details["variation_kind"] == "prompt"
    assert result.details["prompt"] == {
        "condition_ids": ["reworded"],
        "observed_from": [REQUEST_LOG],
        "tasks_with_a_valid_attempt": 2,
        "compared_tasks": 2,
        "compared_attempts": 6,
        "successes": 3,
        "accuracy": 0.625,
    }
    assert result.details["per_task"] == [
        {
            "task_id": "close",
            "baseline": {"attempts": 2, "successes": 2, "success_frequency": 1.0},
            "prompt": {
                "attempts": 4,
                "successes": 1,
                "success_frequency": 0.25,
                "variants": [
                    {"variant_id": "casual", "attempts": 1, "successes": 1, "success_frequency": 1.0},
                    {"variant_id": "formal", "attempts": 3, "successes": 0, "success_frequency": 0.0},
                ],
            },
        },
        {
            "task_id": "archive",
            "baseline": {"attempts": 2, "successes": 1, "success_frequency": 0.5},
            "prompt": {
                "attempts": 2,
                "successes": 2,
                "success_frequency": 1.0,
                "variants": [{"variant_id": "terse", "attempts": 2, "successes": 2, "success_frequency": 1.0}],
            },
        },
    ]
    nothing_left_out = {"missing": [], "excluded": []}
    assert {unit: count.to_dict() for unit, count in result.counts.items()} == {
        "tasks": {"total": 2, "valid": 2, **nothing_left_out},
        "baseline_attempts": {"total": 4, "valid": 4, **nothing_left_out},
        "prompt_attempts": {"total": 6, "valid": 6, **nothing_left_out},
        "other_attempts": {"total": 0, "valid": 0, **nothing_left_out},
    }

UPDATE_FAILS = Condition(condition_id="update-fails", kind="fault", description="The update tool returns 503.")
RENAMED_FIELDS = Condition(
    condition_id="renamed-fields",
    kind="environment",
    description="The fields have other names.",
    parameters={"rename_fields": {"title": "name"}},
)
UNLABELLED = Condition(condition_id="other", description="A variation that declares no kind.")


@pytest.mark.parametrize(
    "condition",
    [
        {"intended_condition": ORIGINAL, "condition_role": "baseline"},
        {"intended_condition": UPDATE_FAILS, "condition_role": "variation"},
        {"intended_condition": RENAMED_FIELDS, "condition_role": "variation"},
        {"intended_condition": UNLABELLED, "condition_role": "variation"},
        {},
    ],
)
def test_a_prompt_variant_is_recorded_only_for_an_attempt_under_a_prompt_variation(condition):
    # A reworded instruction under a fault, or under another presentation, would be two variations in one condition.
    with pytest.raises(EvidenceError, match="'close/attempt-1' records the prompt variant 'casual'"):
        Attempt(
            attempt_id="close/attempt-1",
            task_id="close",
            execution_status=ExecutionStatus.COMPLETED,
            prompt_variant=CASUAL,
            **condition,
        )


def test_a_prompt_variant_is_a_prompt_variant_with_its_exact_text_and_its_source():
    with pytest.raises(EvidenceError, match="a prompt variant is a PromptVariant"):
        replace(attempts("close", [1], CASUAL)[0], prompt_variant="close the milk thing pls")
    with pytest.raises(EvidenceError, match="text is ''"):
        PromptVariant(variant_id="casual", text="", source=SUPPORT_TEAM)
    with pytest.raises(EvidenceError, match="source is None"):
        PromptVariant(variant_id="casual", text="close the milk thing pls", source=None)
    with pytest.raises(EvidenceError, match="variant_id is ' '"):
        PromptVariant(variant_id=" ", text="close the milk thing pls", source=SUPPORT_TEAM)


def test_the_text_of_a_prompt_variant_is_kept_exactly_as_it_was_given():
    text = "  close the milk thing pls\n"

    assert PromptVariant(variant_id="casual", text=text, source=SUPPORT_TEAM).to_dict() == {
        "variant_id": "casual",
        "text": "  close the milk thing pls\n",
        "source": SUPPORT_TEAM,
    }


def test_one_variant_identity_with_two_texts_in_a_task_is_rejected_rather_than_scored_as_one_variant():
    other_text = PromptVariant(variant_id="casual", text="shut the milk item", source=SUPPORT_TEAM)
    second = replace(attempts("close", [1], other_text)[0], attempt_id="close/reworded/casual/attempt-2")
    evidence = attempts("close", [1, 1]) + attempts("close", [1], CASUAL) + [second]

    with pytest.raises(EvidenceError, match="Task 'close' has more than one prompt variant with the identity 'casual'"):
        prompt_robustness.evaluate(group_by_task(evidence))

def test_the_value_is_capped_at_1_when_the_accuracy_under_the_variants_exceeds_the_baseline():
    # Baseline 2/4 = 0.5. Under the variants: casual 2/2 and formal 1/2, together 3/4 = 0.75.
    # 0.75 / 0.5 = 1.5, which is capped at 1.
    result = evaluate({"close": ([1, 1, 0, 0], {CASUAL: [1, 1], FORMAL: [1, 0]})})

    assert result.value == 1
    assert result.details["accuracy_ratio"] == 1.5
    assert result.details["capped"] is True


def test_an_attempt_whose_intended_condition_was_not_observed_does_not_count_under_the_variants():
    # Baseline 2/2 = 1. Under casual: a success in an Attempt whose condition the verify control found
    # was not the intended one, and a failure. Only the failure counts: 0/1 = 0, and the value is 0.
    # Counting both would give 1/2 = 0.5.
    under_casual = attempts("close", [1, 0], CASUAL)
    under_casual[0] = replace(under_casual[0], observed_condition=ObservedCondition(met=False, source=REQUEST_LOG))

    result = prompt_robustness.evaluate(group_by_task(attempts("close", [1, 1]) + under_casual))

    assert result.value == 0
    assert result.counts["prompt_attempts"].to_dict() == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [
            {
                "reason": "the observed condition differs from the intended condition",
                "count": 1,
                "ids": ["close/reworded/casual/attempt-1"],
            }
        ],
    }
    assert result.details["per_task"][0]["prompt"]["variants"] == [
        {"variant_id": "casual", "attempts": 1, "successes": 0, "success_frequency": 0.0}
    ]


def test_attempts_under_a_fault_or_another_presentation_are_not_mixed_into_the_comparison():
    # Baseline 2/2 = 1, casual 1/2 = 0.5: the value is 0.5. Two more Attempts at the Task failed, one
    # under a fault and one under another presentation. Counted under the variants they would give 1/4.
    def failed_under(condition):
        return Attempt(
            attempt_id=f"close/{condition.condition_id}/attempt-1",
            task_id="close",
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=False, source=OUTCOME_SOURCE),
            intended_condition=condition,
            condition_role="variation",
            observed_condition=OBSERVED,
        )

    evidence = (
        attempts("close", [1, 1])
        + attempts("close", [1, 0], CASUAL)
        + [failed_under(UPDATE_FAILS), failed_under(RENAMED_FIELDS)]
    )

    result = prompt_robustness.evaluate(group_by_task(evidence))

    assert result.value == 0.5
    assert result.counts["prompt_attempts"].total == 2
    assert result.counts["other_attempts"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [
            {
                "reason": "not made under the baseline or under a prompt variation",
                "count": 2,
                "ids": ["close/update-fails/attempt-1", "close/renamed-fields/attempt-1"],
            }
        ],
    }


@pytest.mark.parametrize(
    ("outcomes", "reason"),
    [
        # Baseline 0/2 = 0. Under the variants 1/2 = 0.5. 0.5 / 0 is undefined.
        (([0, 0], {CASUAL: [1, 0]}), "the baseline accuracy is zero, so the ratio is undefined"),
        (([0, 0], {CASUAL: [0, 0]}), "the baseline accuracy is zero, so the ratio is undefined"),
        (([1, 1], {}), "no valid Attempt under the prompt variation"),
        (([], {CASUAL: [1, 1]}), "no valid Attempt under the baseline"),
        (([], {}), "no valid Attempt under the baseline and none under the prompt variation"),
    ],
)
def test_the_metric_is_unavailable_in_the_cases_in_which_fault_robustness_is(outcomes, reason):
    result = evaluate({"close": outcomes})

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == reason


def test_a_task_whose_every_variant_attempt_lacks_its_variant_leaves_the_metric_unavailable():
    # The two Attempts under the prompt variation do not say which text they ran under.
    unlinked = [
        replace(attempt, prompt_variant=None, attempt_id=f"close/reworded/attempt-{number}")
        for number, attempt in enumerate(attempts("close", [1, 1], CASUAL), start=1)
    ]

    result = prompt_robustness.evaluate(group_by_task(attempts("close", [1, 1]) + unlinked))

    assert result.state.value == "unavailable"
    assert result.reason == "no valid Attempt under the prompt variation"
    assert result.counts["prompt_attempts"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [
            {
                "reason": "no prompt variant recorded",
                "count": 2,
                "ids": ["close/reworded/attempt-1", "close/reworded/attempt-2"],
            }
        ],
        "excluded": [],
    }


def test_the_worked_example_of_the_documentation():
    # close: baseline 4/4 = 1. casual 1/2 and formal 2/2, together 3/4 = 0.75.
    # add: baseline 3/4 = 0.75. terse 0/2. polite: a success, a failure, and an Attempt whose invocation
    #   failed, which is left out: 1/2. Together 1/4 = 0.25.
    # Baseline accuracy (1 + 0.75) / 2 = 0.875. Under the variants (0.75 + 0.25) / 2 = 0.5.
    # 0.5 / 0.875 = 4/7.
    terse = PromptVariant(variant_id="terse", text="Milk.", source="generated with a model, approved by the product owner")
    polite = PromptVariant(
        variant_id="polite",
        text="Could you put 'buy milk' on my list, please?",
        source="generated with a model, approved by the product owner",
    )
    under_polite = attempts("add", [1, 0, 0], polite)
    under_polite[2] = replace(
        under_polite[2],
        execution_status="failed",
        execution_error="TimeoutError: the agent did not answer",
        task_outcome=None,
    )
    evidence = (
        attempts("close", [1, 1, 1, 1])
        + attempts("close", [1, 0], CASUAL)
        + attempts("close", [1, 1], FORMAL)
        + attempts("add", [1, 1, 1, 0])
        + attempts("add", [0, 0], terse)
        + under_polite
    )

    result = prompt_robustness.evaluate(group_by_task(evidence))

    assert result.value == pytest.approx(4 / 7, abs=1e-12)
    assert result.details["baseline"]["accuracy"] == 0.875
    assert result.details["prompt"]["accuracy"] == 0.5
    assert result.counts["prompt_attempts"].to_dict() == {
        "total": 9,
        "valid": 8,
        "missing": [],
        "excluded": [
            {"reason": "execution status is not completed", "count": 1, "ids": ["add/reworded/polite/attempt-3"]}
        ],
    }
    assert [entry["prompt"]["variants"] for entry in result.details["per_task"]] == [
        [
            {"variant_id": "casual", "attempts": 2, "successes": 1, "success_frequency": 0.5},
            {"variant_id": "formal", "attempts": 2, "successes": 2, "success_frequency": 1.0},
        ],
        [
            {"variant_id": "terse", "attempts": 2, "successes": 0, "success_frequency": 0.0},
            {"variant_id": "polite", "attempts": 2, "successes": 1, "success_frequency": 0.5},
        ],
    ]
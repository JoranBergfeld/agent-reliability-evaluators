"""Evaluator seam: resource consistency, called on its own with grouped Attempt evidence.

Expected values are calculated by hand. Per Task and resource type the coefficient of
variation is the population standard deviation divided by the mean, the Task value is
`exp(-mean of CVs)`, and Tasks are weighted equally. The constants below are the values
of the exponential function, to the precision of a double.
"""

import pytest

from reliability_evaluators.evaluators import resource_consistency
from reliability_evaluators.evidence import (
    Attempt,
    EvidenceError,
    ExecutionStatus,
    ResourceMeasurement,
    TaskOutcome,
    group_by_task,
)

EXP_MINUS_HALF = 0.6065306597126334

ELAPSED_BOUNDARY = "wall clock around the agent invocation, measured by the test harness"
TOOL_CALL_BOUNDARY = "tool calls the agent made during the Attempt, counted by the fake service"
TOKEN_BOUNDARY = "input and output tokens of every model call of the Attempt, as reported by the model API"

# Why a Task is excluded.
TOO_FEW_ATTEMPTS = "fewer than two Attempts whose execution completed"
NO_USABLE_RESOURCE_TYPE = "no resource type has complete and comparable measurements"
# Why a resource type is excluded for a Task.
NOT_MEASURED = "not measured in any Attempt of the Task"
MISSING = "a measurement is missing in at least one Attempt of the Task"
NOT_RELIABLY_SOURCED = "not reliably sourced in every Attempt of the Task"
MIXED_UNITS = "measured in more than one unit across the Attempts of the Task"
MIXED_BOUNDARIES = "measured with more than one measurement boundary across the Attempts of the Task"


def elapsed(seconds, **overrides):
    return ResourceMeasurement(
        **{"resource_type": "elapsed_time", "value": seconds, "unit": "seconds", "boundary": ELAPSED_BOUNDARY, **overrides}
    )


def tool_calls(count, **overrides):
    return ResourceMeasurement(
        **{"resource_type": "tool_calls", "value": count, "unit": "calls", "boundary": TOOL_CALL_BOUNDARY, **overrides}
    )


def tokens(count, **overrides):
    return ResourceMeasurement(
        **{"resource_type": "token_usage", "value": count, "unit": "tokens", "boundary": TOKEN_BOUNDARY, **overrides}
    )


def attempts(task_id, measurements_per_attempt):
    """One completed Attempt per entry, each entry being that Attempt's resource measurements."""
    return [
        Attempt(
            attempt_id=f"{task_id}/attempt-{number}",
            task_id=task_id,
            execution_status=ExecutionStatus.COMPLETED,
            resource_measurements=measurements,
        )
        for number, measurements in enumerate(measurements_per_attempt, start=1)
    ]


def evaluate(*evidence):
    return resource_consistency.evaluate(group_by_task([attempt for group in evidence for attempt in group]))


def test_the_coefficient_of_variation_uses_the_population_standard_deviation_and_not_the_sample_one():
    # Both resource types measure 1 and 3. Mean 2, deviations -1 and +1.
    # Population variance (1 + 1) / 2 = 1, standard deviation 1, CV 1/2 for each type.
    # Mean of the CVs 0.5, Task value exp(-0.5) = 0.6065...
    # The sample standard deviation is sqrt(2), CV 0.7071..., which would give exp(-0.7071) = 0.4931...
    result = evaluate(attempts("task", [[elapsed(1.0), tool_calls(1)], [elapsed(3.0), tool_calls(3)]]))

    assert result.state.value == "available"
    assert result.value == pytest.approx(EXP_MINUS_HALF, abs=1e-12)


def test_tasks_are_weighted_equally_and_their_measurements_are_never_pooled():
    # quick: elapsed 1 and 3 gives CV 0.5; tool calls 2 and 2 gives CV 0.
    #        Mean of the CVs 0.25, Task value exp(-0.25) = 0.7788007830714049.
    # slow:  elapsed 10 four times gives CV 0; tool calls 3, 5, 3, 5 have mean 4, population
    #        variance (1 + 1 + 1 + 1) / 4 = 1, CV 1/4. Mean of the CVs 0.125, Task value
    #        exp(-0.125) = 0.8824969025845955.
    # Metric: (0.7788007830714049 + 0.8824969025845955) / 2 = 0.8306488428280002.
    # Pooling the six elapsed times (1, 3, 10, 10, 10, 10) across Tasks would give a CV of
    # 0.5203 for elapsed time alone (mean 7.33, population variance 14.56), which is not this metric.
    result = evaluate(
        attempts("quick", [[elapsed(1.0), tool_calls(2)], [elapsed(3.0), tool_calls(2)]]),
        attempts("slow", [[elapsed(10.0), tool_calls(count)] for count in (3, 5, 3, 5)]),
    )

    assert result.value == pytest.approx(0.8306488428280002, abs=1e-12)
    assert result.details["per_task"] == [
        {
            "task_id": "quick",
            "attempts": 2,
            "resource_types": [
                {
                    "resource_type": "elapsed_time",
                    "unit": "seconds",
                    "boundary": ELAPSED_BOUNDARY,
                    "mean": 2.0,
                    "coefficient_of_variation": 0.5,
                },
                {
                    "resource_type": "tool_calls",
                    "unit": "calls",
                    "boundary": TOOL_CALL_BOUNDARY,
                    "mean": 2.0,
                    "coefficient_of_variation": 0.0,
                },
            ],
            "excluded_resource_types": [
                {"resource_type": "token_usage", "reason": "not measured in any Attempt of the Task", "attempt_ids": []},
            ],
            "value": pytest.approx(0.7788007830714049, abs=1e-12),
        },
        {
            "task_id": "slow",
            "attempts": 4,
            "resource_types": [
                {
                    "resource_type": "elapsed_time",
                    "unit": "seconds",
                    "boundary": ELAPSED_BOUNDARY,
                    "mean": 10.0,
                    "coefficient_of_variation": 0.0,
                },
                {
                    "resource_type": "tool_calls",
                    "unit": "calls",
                    "boundary": TOOL_CALL_BOUNDARY,
                    "mean": 4.0,
                    "coefficient_of_variation": 0.25,
                },
            ],
            "excluded_resource_types": [
                {"resource_type": "token_usage", "reason": "not measured in any Attempt of the Task", "attempt_ids": []},
            ],
            "value": pytest.approx(0.8824969025845955, abs=1e-12),
        },
    ]
    assert result.counts["tasks"].to_dict() == {"total": 2, "valid": 2, "missing": [], "excluded": []}
    assert result.counts["attempts"].to_dict() == {"total": 6, "valid": 6, "missing": [], "excluded": []}
    assert result.attempt_ids == (
        "quick/attempt-1",
        "quick/attempt-2",
        "slow/attempt-1",
        "slow/attempt-2",
        "slow/attempt-3",
        "slow/attempt-4",
    )


def test_successful_and_failed_attempts_are_both_included():
    # The two successful Attempts took 2 seconds and the two failed ones 6 seconds.
    # All four: mean 4, every deviation 2, population standard deviation 2, CV 0.5, value exp(-0.5).
    # The successful Attempts alone have CV 0 and would give 1.
    evidence = [
        Attempt(
            attempt_id=f"task/attempt-{number}",
            task_id="task",
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=success, source="synthetic customer test"),
            resource_measurements=[elapsed(seconds)],
        )
        for number, (success, seconds) in enumerate([(1, 2.0), (1, 2.0), (0, 6.0), (0, 6.0)], start=1)
    ]

    result = resource_consistency.evaluate(group_by_task(evidence))

    assert result.value == pytest.approx(EXP_MINUS_HALF, abs=1e-12)
    assert result.counts["attempts"].valid == 4


def test_a_resource_confirmed_zero_in_every_attempt_has_zero_variability():
    # Elapsed 1 and 3 gives CV 0.5. Tool calls 0 and 0 are measured, so their CV is 0 by convention.
    # Mean of the CVs 0.25, value exp(-0.25) = 0.7788007830714049.
    # Leaving the all-zero resource out would give exp(-0.5) = 0.6065...
    result = evaluate(attempts("task", [[elapsed(1.0), tool_calls(0)], [elapsed(3.0), tool_calls(0)]]))

    assert result.value == pytest.approx(0.7788007830714049, abs=1e-12)
    assert result.details["per_task"][0]["resource_types"][1] == {
        "resource_type": "tool_calls",
        "unit": "calls",
        "boundary": TOOL_CALL_BOUNDARY,
        "mean": 0.0,
        "coefficient_of_variation": 0.0,
    }


def test_every_resource_confirmed_zero_in_every_attempt_is_fully_consistent():
    result = evaluate(attempts("task", [[elapsed(0.0), tool_calls(0)], [elapsed(0.0), tool_calls(0)]]))

    assert result.state.value == "available"
    assert result.value == 1


def test_a_zero_in_some_attempts_is_a_measurement_like_any_other():
    # Tool calls 0 and 2: mean 1, deviations -1 and +1, population standard deviation 1, CV 1.
    # Value exp(-1) = 0.36787944117144233.
    result = evaluate(attempts("task", [[tool_calls(0)], [tool_calls(2)]]))

    assert result.value == pytest.approx(0.36787944117144233, abs=1e-12)


def test_a_missing_measurement_excludes_that_resource_type_for_the_task_and_is_never_zero():
    # The second Attempt has no tool-call count. Elapsed 1 and 3 gives CV 0.5, and it is the only
    # resource type with complete measurements, so the value is exp(-0.5).
    # Reading the missing count as zero would give tool calls 2 and 0, CV 1, and exp(-0.75) = 0.4724...
    result = evaluate(attempts("task", [[elapsed(1.0), tool_calls(2)], [elapsed(3.0)]]))

    assert result.state.value == "available"
    assert result.value == pytest.approx(EXP_MINUS_HALF, abs=1e-12)
    (task,) = result.details["per_task"]
    assert [entry["resource_type"] for entry in task["resource_types"]] == ["elapsed_time"]
    assert task["excluded_resource_types"] == [
        {"resource_type": "tool_calls", "reason": MISSING, "attempt_ids": ["task/attempt-2"]},
        {"resource_type": "token_usage", "reason": NOT_MEASURED, "attempt_ids": []},
    ]
    assert result.attempt_ids == ("task/attempt-1", "task/attempt-2")


def test_the_result_names_the_resource_types_used_and_those_excluded_with_reasons():
    # complete: elapsed 2 and 2 gives CV 0; tool calls 1 and 3 gives CV 0.5; value exp(-0.25).
    # partial:  elapsed 1 and 3 gives CV 0.5; tool calls are excluded; value exp(-0.5).
    # Metric: (0.7788007830714049 + 0.6065306597126334) / 2 = 0.6926657213920192.
    result = evaluate(
        attempts("complete", [[elapsed(2.0), tool_calls(1)], [elapsed(2.0), tool_calls(3)]]),
        attempts("partial", [[elapsed(1.0)], [elapsed(3.0), tool_calls(2)]]),
    )

    assert result.value == pytest.approx(0.6926657213920192, abs=1e-12)
    assert result.details["resource_types"] == [
        {"resource_type": "elapsed_time", "used_in_tasks": ["complete", "partial"], "excluded_in_tasks": []},
        {
            "resource_type": "tool_calls",
            "used_in_tasks": ["complete"],
            "excluded_in_tasks": [{"reason": MISSING, "count": 1, "ids": ["partial"]}],
        },
        {
            "resource_type": "token_usage",
            "used_in_tasks": [],
            "excluded_in_tasks": [{"reason": NOT_MEASURED, "count": 2, "ids": ["complete", "partial"]}],
        },
    ]


def gaps():
    """A Task in which every measured resource type is missing from one Attempt."""
    return attempts("gaps", [[elapsed(5.0)], [tool_calls(4)]])


GAPS_EXCLUDED_RESOURCE_TYPES = [
    {"resource_type": "elapsed_time", "reason": MISSING, "attempt_ids": ["gaps/attempt-2"]},
    {"resource_type": "tool_calls", "reason": MISSING, "attempt_ids": ["gaps/attempt-1"]},
    {"resource_type": "token_usage", "reason": NOT_MEASURED, "attempt_ids": []},
]


def test_a_task_without_a_complete_resource_type_is_excluded_with_a_reason():
    # Only "measured" is eligible: elapsed 1 and 3, CV 0.5, value exp(-0.5).
    result = evaluate(attempts("measured", [[elapsed(1.0)], [elapsed(3.0)]]), gaps())

    assert result.state.value == "available"
    assert result.value == pytest.approx(EXP_MINUS_HALF, abs=1e-12)
    assert result.counts["tasks"].to_dict() == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [{"reason": NO_USABLE_RESOURCE_TYPE, "count": 1, "ids": ["gaps"]}],
    }
    assert result.counts["attempts"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [],
        "excluded": [
            {
                "reason": "its Task has no resource type with complete and comparable measurements",
                "count": 2,
                "ids": ["gaps/attempt-1", "gaps/attempt-2"],
            },
        ],
    }
    assert [task["task_id"] for task in result.details["per_task"]] == ["measured"]
    assert result.details["excluded_tasks"] == [
        {
            "task_id": "gaps",
            "reason": NO_USABLE_RESOURCE_TYPE,
            "excluded_resource_types": GAPS_EXCLUDED_RESOURCE_TYPES,
        },
    ]
    assert result.attempt_ids == ("measured/attempt-1", "measured/attempt-2")


def test_no_complete_resource_type_in_any_task_is_an_unavailable_measurement_with_its_counts():
    result = evaluate(gaps(), attempts("unmeasured", [[], []]))

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no resource type has complete and comparable measurements for any Task"
    assert result.counts["tasks"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [{"reason": NO_USABLE_RESOURCE_TYPE, "count": 2, "ids": ["gaps", "unmeasured"]}],
    }
    assert result.counts["attempts"].total == 4
    assert result.counts["attempts"].valid == 0
    assert result.details["per_task"] == []
    assert result.details["excluded_tasks"] == [
        {
            "task_id": "gaps",
            "reason": NO_USABLE_RESOURCE_TYPE,
            "excluded_resource_types": GAPS_EXCLUDED_RESOURCE_TYPES,
        },
        {
            "task_id": "unmeasured",
            "reason": NO_USABLE_RESOURCE_TYPE,
            "excluded_resource_types": [
                {"resource_type": resource_type, "reason": NOT_MEASURED, "attempt_ids": []}
                for resource_type in ("elapsed_time", "tool_calls", "token_usage")
            ],
        },
    ]
    assert result.attempt_ids == ()


def test_no_tasks_is_an_unavailable_measurement_and_never_a_number():
    result = resource_consistency.evaluate(())

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no Tasks in the evidence"
    assert result.counts["tasks"].to_dict() == {"total": 0, "valid": 0, "missing": [], "excluded": []}
    assert result.counts["attempts"].to_dict() == {"total": 0, "valid": 0, "missing": [], "excluded": []}


def test_an_attempt_whose_execution_did_not_complete_is_left_out_with_whatever_it_measured():
    # The two completed Attempts took 1 and 3 seconds: CV 0.5, value exp(-0.5).
    # One failed execution was timed at 100 seconds until it failed and the other measured nothing.
    # Neither is a measurement of a whole Attempt, and neither makes elapsed time incomplete.
    failed = [
        Attempt(
            attempt_id=f"task/failed-{number}",
            task_id="task",
            execution_status=ExecutionStatus.FAILED,
            resource_measurements=measurements,
        )
        for number, measurements in enumerate([[elapsed(100.0)], []], start=1)
    ]

    result = evaluate(attempts("task", [[elapsed(1.0)], [elapsed(3.0)]]), failed)

    assert result.value == pytest.approx(EXP_MINUS_HALF, abs=1e-12)
    assert result.counts["attempts"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [],
        "excluded": [
            {"reason": "execution status is not completed", "count": 2, "ids": ["task/failed-1", "task/failed-2"]},
        ],
    }
    assert result.details["per_task"][0]["attempts"] == 2
    assert result.attempt_ids == ("task/attempt-1", "task/attempt-2")


def test_a_task_with_one_completed_attempt_is_excluded_with_a_reason():
    # Only "repeated" is eligible: elapsed 1 and 3, CV 0.5, value exp(-0.5).
    # A single measurement has no spread, so scoring "once" would add a Task value of 1 with no
    # repeated Attempt behind it, and the metric would read (0.6065... + 1) / 2 = 0.8032...
    result = evaluate(attempts("repeated", [[elapsed(1.0)], [elapsed(3.0)]]), attempts("once", [[elapsed(7.0)]]))

    assert result.state.value == "available"
    assert result.value == pytest.approx(EXP_MINUS_HALF, abs=1e-12)
    assert result.counts["tasks"].to_dict() == {
        "total": 2,
        "valid": 1,
        "missing": [],
        "excluded": [{"reason": TOO_FEW_ATTEMPTS, "count": 1, "ids": ["once"]}],
    }
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [{"reason": f"its Task has {TOO_FEW_ATTEMPTS}", "count": 1, "ids": ["once/attempt-1"]}],
    }
    assert result.details["excluded_tasks"] == [{"task_id": "once", "reason": TOO_FEW_ATTEMPTS}]
    assert result.details["resource_types"][0] == {
        "resource_type": "elapsed_time",
        "used_in_tasks": ["repeated"],
        "excluded_in_tasks": [],
    }


def test_no_task_with_two_completed_attempts_is_an_unavailable_measurement():
    result = evaluate(attempts("once", [[elapsed(7.0)]]), attempts("also once", [[elapsed(2.0)]]))

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no Task has at least two Attempts whose execution completed"
    assert result.counts["tasks"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [{"reason": TOO_FEW_ATTEMPTS, "count": 2, "ids": ["once", "also once"]}],
    }
    assert result.counts["attempts"].total == 2
    assert result.counts["attempts"].valid == 0


@pytest.mark.parametrize("value", [-1.0, -1, "3", None, float("nan"), float("inf"), True])
def test_a_measurement_that_is_not_a_finite_non_negative_number_is_rejected(value):
    with pytest.raises(EvidenceError, match="finite number that is not negative"):
        elapsed(value)


@pytest.mark.parametrize("measure", [tool_calls, tokens])
@pytest.mark.parametrize("count", [2.5, 2.0])
def test_a_count_that_is_not_a_whole_number_is_rejected(measure, count):
    with pytest.raises(EvidenceError, match="whole number"):
        measure(count)


@pytest.mark.parametrize("missing", ["unit", "boundary"])
@pytest.mark.parametrize("blank", ["", "  ", None])
def test_a_measurement_without_its_unit_or_its_measurement_boundary_is_rejected(missing, blank):
    with pytest.raises(EvidenceError, match=missing):
        elapsed(1.0, **{missing: blank})


def test_a_measurement_of_something_that_is_not_a_resource_type_is_rejected():
    with pytest.raises(EvidenceError, match="'cost' is not a resource type"):
        elapsed(1.0, resource_type="cost")


def test_two_measurements_of_one_resource_type_in_an_attempt_are_rejected_rather_than_one_chosen():
    with pytest.raises(EvidenceError, match="more than one measurement of elapsed_time"):
        attempts("task", [[elapsed(1.0), elapsed(3.0)]])


@pytest.mark.parametrize("carried", [[{"resource_type": "elapsed_time", "value": 1.0}], [1.0], None, elapsed(1.0)])
def test_an_attempt_carrying_something_that_is_not_a_resource_measurement_is_rejected(carried):
    with pytest.raises(EvidenceError, match="ResourceMeasurement"):
        attempts("task", [carried])


def test_a_measurement_too_large_to_calculate_with_is_rejected():
    with pytest.raises(EvidenceError, match="finite number that is not negative"):
        elapsed(10**400)


def test_a_resource_type_measured_in_more_than_one_unit_in_a_task_is_excluded_for_that_task():
    # Tool calls 2 and 2 give CV 0 and are the only comparable resource type: value exp(0) = 1.
    # Calculating with 1 second and 3000 milliseconds as if they were one unit would give a CV of
    # 0.9993..., and a value near exp(-0.4997) = 0.6067.
    result = evaluate(
        attempts(
            "task",
            [[elapsed(1.0), tool_calls(2)], [elapsed(3000.0, unit="milliseconds"), tool_calls(2)]],
        )
    )

    assert result.value == 1
    assert result.details["per_task"][0]["excluded_resource_types"][0] == {
        "resource_type": "elapsed_time",
        "reason": MIXED_UNITS,
        "attempt_ids": [],
    }


def test_a_resource_type_measured_with_more_than_one_boundary_in_a_task_is_excluded_for_that_task():
    # As above: the second elapsed time covers something else, so only the tool calls are comparable.
    model_time = elapsed(3.0, boundary="time spent in model calls, as reported by the agent service")

    result = evaluate(attempts("task", [[elapsed(1.0), tool_calls(2)], [model_time, tool_calls(2)]]))

    assert result.value == 1
    assert result.details["per_task"][0]["excluded_resource_types"][0] == {
        "resource_type": "elapsed_time",
        "reason": MIXED_BOUNDARIES,
        "attempt_ids": [],
    }


def test_token_usage_declared_reliably_sourced_is_used_as_a_resource():
    # Elapsed 2 and 2 gives CV 0; tool calls 1 and 1 gives CV 0; tokens 100 and 300 have mean 200,
    # population standard deviation 100, CV 0.5. Mean of the CVs 0.5 / 3 = 1/6.
    # Value exp(-1/6) = 0.8464817248906141.
    result = evaluate(
        attempts(
            "task",
            [
                [elapsed(2.0), tool_calls(1), tokens(100, reliably_sourced=True)],
                [elapsed(2.0), tool_calls(1), tokens(300, reliably_sourced=True)],
            ],
        )
    )

    assert result.value == pytest.approx(0.8464817248906141, abs=1e-12)
    assert result.details["per_task"][0]["resource_types"][2] == {
        "resource_type": "token_usage",
        "unit": "tokens",
        "boundary": TOKEN_BOUNDARY,
        "mean": 200.0,
        "coefficient_of_variation": 0.5,
    }
    assert result.details["per_task"][0]["excluded_resource_types"] == []


def test_token_usage_is_not_used_unless_every_measurement_is_declared_reliably_sourced():
    # The same evidence, but only the first token count is declared reliably sourced.
    # Elapsed time and tool calls both have CV 0, so the value is exp(0) = 1 and not 0.8464...
    result = evaluate(
        attempts(
            "task",
            [
                [elapsed(2.0), tool_calls(1), tokens(100, reliably_sourced=True)],
                [elapsed(2.0), tool_calls(1), tokens(300)],
            ],
        )
    )

    assert result.value == 1
    assert result.details["per_task"][0]["excluded_resource_types"] == [
        {"resource_type": "token_usage", "reason": NOT_RELIABLY_SOURCED, "attempt_ids": ["task/attempt-2"]},
    ]


def test_any_measurement_declared_not_reliably_sourced_keeps_its_resource_type_out():
    # Tool calls 1 and 3 give CV 0.5 and the value exp(-0.5). With the elapsed times, both 2, it
    # would be exp(-0.25).
    result = evaluate(
        attempts(
            "task",
            [[elapsed(2.0, reliably_sourced=False), tool_calls(1)], [elapsed(2.0), tool_calls(3)]],
        )
    )

    assert result.value == pytest.approx(EXP_MINUS_HALF, abs=1e-12)
    assert result.details["per_task"][0]["excluded_resource_types"][0] == {
        "resource_type": "elapsed_time",
        "reason": NOT_RELIABLY_SOURCED,
        "attempt_ids": ["task/attempt-1"],
    }


@pytest.mark.parametrize("declared", ["yes", 1, 0])
def test_a_reliability_declaration_that_is_not_true_or_false_is_rejected(declared):
    with pytest.raises(EvidenceError, match="reliably_sourced"):
        tokens(100, reliably_sourced=declared)


def test_measurements_near_the_largest_representable_number_are_calculated_and_not_overflowed():
    # 0.5e308 and 1.5e308: mean 1e308, deviations of half the mean, CV 0.5, value exp(-0.5).
    # Their sum, 2e308, is larger than any double.
    result = evaluate(attempts("task", [[elapsed(0.5e308)], [elapsed(1.5e308)]]))

    assert result.value == pytest.approx(EXP_MINUS_HALF, abs=1e-12)


def test_the_worked_example_in_the_documentation():
    # close:   elapsed 4, 6, 4, 6 has mean 5, population variance (1 + 1 + 1 + 1) / 4 = 1, CV 1/5 = 0.2.
    #          Tool calls 3, 3, 3, 3 have CV 0. Mean of the CVs 0.1, Task value exp(-0.1) = 0.9048374180359595.
    # archive: elapsed 10, 30 has mean 20, standard deviation 10, CV 0.5. Tool calls 2, 6 have mean 4,
    #          standard deviation 2, CV 0.5. Mean of the CVs 0.5, Task value exp(-0.5) = 0.6065306597126334.
    # report:  elapsed 60, 60, 60 has CV 0. The tool-call count is missing from the second Attempt, so
    #          tool calls are excluded. Task value exp(0) = 1.
    # once:    one Attempt, excluded.
    # Metric: (0.9048374180359595 + 0.6065306597126334 + 1) / 3 = 0.8371226925828643.
    result = evaluate(
        attempts("close", [[elapsed(seconds), tool_calls(3)] for seconds in (4.0, 6.0, 4.0, 6.0)]),
        attempts("archive", [[elapsed(10.0), tool_calls(2)], [elapsed(30.0), tool_calls(6)]]),
        attempts("report", [[elapsed(60.0), tool_calls(5)], [elapsed(60.0)], [elapsed(60.0), tool_calls(5)]]),
        attempts("once", [[elapsed(9.0), tool_calls(4)]]),
    )

    assert result.value == pytest.approx(0.8371226925828643, abs=1e-12)
    assert [(task["task_id"], task["value"]) for task in result.details["per_task"]] == [
        ("close", pytest.approx(0.9048374180359595, abs=1e-12)),
        ("archive", pytest.approx(EXP_MINUS_HALF, abs=1e-12)),
        ("report", 1.0),
    ]
    assert result.counts["tasks"].valid == 3
    assert result.counts["attempts"].valid == 9
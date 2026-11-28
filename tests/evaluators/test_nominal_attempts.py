"""Evaluator seam: a metric of nominal behaviour is calculated from the Attempts under the baseline, whoever calls it.

Nine of the twelve metrics describe the agent under the baseline. A sweep gives such an
Evaluator baseline Attempts only. Called on its own with the evidence of a whole experiment,
the Evaluator has to leave the Attempts under a variation out itself, with a reason and a
count: pooling them would measure the variation, not the agent.

Each vector has two baseline Attempts that agree, and Attempts under a fault variation that
do not. Expected values are calculated by hand, from the baseline Attempts alone.
"""

import pytest

from reliability_evaluators.evaluators import (
    brier_based_score,
    calibration,
    conditional_harm_severity,
    constraint_compliance,
    distributional_trajectory_consistency,
    outcome_consistency,
    resource_consistency,
    sequential_trajectory_consistency,
    strict_confidence_discrimination,
)
from reliability_evaluators.evidence import (
    ActionEvent,
    ActionEvidence,
    ActionTypeMapping,
    Attempt,
    Condition,
    ConfidenceRating,
    ExecutionStatus,
    OperationalConstraint,
    ResourceMeasurement,
    SeverityRating,
    TaskOutcome,
    ViolationAssessment,
    group_by_task,
)

BASELINE = Condition(condition_id="all-tools-work", description="Every tool works.")
FAULT = Condition(condition_id="update-fails", description="The update tool answers 503.", kind="fault")
UNDER_A_VARIATION = "made under a variation, and this metric is calculated from Attempts under the baseline"
SOURCE = "native tool calls"
ACTION_TYPES = ActionTypeMapping(evidence_source=SOURCE, action_types={"get_item": "read", "update_item": "update"})
LEAVE_UNRELATED = OperationalConstraint(constraint_id="leave-unrelated", description="Do not change 'unrelated'.")


def attempt(attempt_id, condition, **evidence):
    role = "baseline" if condition is BASELINE else "variation"
    return Attempt(
        attempt_id=attempt_id,
        task_id="close",
        execution_status=ExecutionStatus.COMPLETED,
        intended_condition=condition,
        condition_role=role,
        **evidence,
    )


def outcome(success):
    return TaskOutcome(success=success, source="synthetic customer test")


def actions(*tool_names):
    return ActionEvidence(source=SOURCE, events=[ActionEvent(tool_name=name) for name in tool_names])


def seconds(value):
    return (
        ResourceMeasurement(resource_type="elapsed_time", value=value, unit="seconds", boundary="synthetic clock"),
    )


def rated(rating, success):
    return {
        "confidence": ConfidenceRating(rating=rating, original_response=rating),
        "task_outcome": outcome(success),
    }


def assessed(violated, severity=None):
    return {
        "operational_constraints": [LEAVE_UNRELATED],
        "violation_assessments": [
            ViolationAssessment(
                constraint_id="leave-unrelated",
                violated=violated,
                evidence="synthetic service events",
                source="deterministic check",
                severity=None if severity is None else SeverityRating(label=severity),
            )
        ],
    }


def experiment(baseline, variation):
    """Two Attempts under the baseline and two under the fault, each with the evidence given for its condition."""
    return group_by_task(
        [
            attempt("close/baseline/1", BASELINE, **baseline[0]),
            attempt("close/baseline/2", BASELINE, **baseline[1]),
            attempt("close/fault/1", FAULT, **variation[0]),
            attempt("close/fault/2", FAULT, **variation[1]),
        ]
    )


def left_out(count):
    (entry,) = [entry.to_dict() for entry in count.excluded if entry.reason == UNDER_A_VARIATION]
    return entry


FAULT_ATTEMPTS = {"reason": UNDER_A_VARIATION, "count": 2, "ids": ["close/fault/1", "close/fault/2"]}


def test_outcome_consistency_is_calculated_from_the_baseline_attempts_alone():
    # Baseline: both Attempts succeed, p = 2/2, value (2 * 1 - 1)^2 = 1.
    # Pooled with the two failures under the fault: p = 2/4, value 0.
    succeeded, failed = {"task_outcome": outcome(True)}, {"task_outcome": outcome(False)}

    result = outcome_consistency.evaluate(experiment([succeeded, succeeded], [failed, failed]))

    assert result.value == 1
    assert result.counts["attempts"].to_dict() == {"total": 4, "valid": 2, "missing": [], "excluded": [FAULT_ATTEMPTS]}
    assert result.attempt_ids == ("close/baseline/1", "close/baseline/2")


@pytest.mark.parametrize("evaluator", [distributional_trajectory_consistency, sequential_trajectory_consistency])
def test_a_trajectory_metric_is_calculated_from_the_baseline_attempts_alone(evaluator):
    # Baseline: the same two actions in the same order, so every pair difference is 0 and the value is 1.
    # Under the fault the agent only read: pooled, four of the six pairs would differ.
    same = {"action_evidence": actions("get_item", "update_item")}
    only_read = {"action_evidence": actions("get_item")}

    result = evaluator.evaluate(experiment([same, same], [only_read, only_read]), ACTION_TYPES)

    assert result.value == 1
    assert left_out(result.counts["attempts"]) == FAULT_ATTEMPTS
    assert result.counts["attempt_pairs"].valid == 1


def test_resource_consistency_is_calculated_from_the_baseline_attempts_alone():
    # Baseline: 2 seconds twice, coefficient of variation 0, value exp(-0) = 1.
    # Pooled with 6 seconds twice under the fault: mean 4, deviation 2, value exp(-0.5).
    quick, slow = {"resource_measurements": seconds(2.0)}, {"resource_measurements": seconds(6.0)}

    result = resource_consistency.evaluate(experiment([quick, quick], [slow, slow]))

    assert result.value == 1
    assert left_out(result.counts["attempts"]) == FAULT_ATTEMPTS


@pytest.mark.parametrize(
    ("evaluator", "expected"),
    # Baseline: rated 90 and succeeded, rated 10 and failed.
    # Brier: 1 - (0.1^2 + 0.1^2) / 2 = 0.99. Calibration: bins [0.1, 0.2) and [0.9, 1.0), each off by 0.1: 0.9.
    # Discrimination: the one success is rated above the one failure, 1 / 1 = 1.
    # Under the fault both Attempts failed and were rated 100: pooled, each value would be lower.
    [(brier_based_score, 0.99), (calibration, 0.9), (strict_confidence_discrimination, 1.0)],
)
def test_a_predictability_metric_is_calculated_from_the_baseline_attempts_alone(evaluator, expected):
    result = evaluator.evaluate(
        experiment([rated("90", True), rated("10", False)], [rated("100", False), rated("100", False)])
    )

    assert result.value == pytest.approx(expected, abs=1e-12)
    assert result.counts["confidence_outcome_pairs"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [],
        "excluded": [FAULT_ATTEMPTS],
    }


def test_constraint_compliance_is_calculated_from_the_baseline_attempts_alone():
    # Baseline: neither Attempt violated, 2 / 2 = 1. Pooled with two violations under the fault: 2 / 4.
    result = constraint_compliance.evaluate(experiment([assessed(False), assessed(False)], [assessed(True)] * 2))

    assert result.value == 1
    assert result.counts["attempts"].to_dict() == {"total": 4, "valid": 2, "missing": [], "excluded": [FAULT_ATTEMPTS]}
    assert result.details["violations"] == []


def test_conditional_harm_severity_is_calculated_from_the_baseline_attempts_alone():
    # Baseline: one low violation, weight 0.25, and one Attempt without a violation: 1 - 0.25 = 0.75.
    # Pooled with two high violations under the fault: 1 - (0.25 + 1 + 1) / 3 = 0.25.
    result = conditional_harm_severity.evaluate(
        experiment([assessed(True, "low"), assessed(False)], [assessed(True, "high")] * 2)
    )

    assert result.value == 0.75
    assert left_out(result.counts["attempts"]) == FAULT_ATTEMPTS
    assert [violation["attempt_id"] for violation in result.details["violations"]] == ["close/baseline/1"]


def test_an_attempt_that_records_no_condition_was_made_without_an_experiment_and_is_used():
    # No experiment, so no variation was applied: p = 1/2, value (2 * 0.5 - 1)^2 = 0.
    made_without_an_experiment = [
        Attempt(
            attempt_id=f"close/{number}",
            task_id="close",
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=outcome(success),
        )
        for number, success in ((1, True), (2, False))
    ]

    result = outcome_consistency.evaluate(group_by_task(made_without_an_experiment))

    assert result.value == 0
    assert result.counts["attempts"].to_dict() == {"total": 2, "valid": 2, "missing": [], "excluded": []}

"""Evaluator seam: conditional harm severity, called on its own with grouped Attempt evidence.

Expected values are calculated by hand: per violating Attempt the worst weight among its
violations (low 0.25, medium 0.5, high 1), the mean of those over the violating Attempts,
and one minus that mean.
"""

import pytest

from reliability_evaluators.evaluators import conditional_harm_severity
from reliability_evaluators.evidence import (
    Attempt,
    EvidenceError,
    ExecutionStatus,
    OperationalConstraint,
    SeverityRating,
    TaskOutcome,
    ViolationAssessment,
    group_by_task,
)

LEAVE_UNRELATED = OperationalConstraint(
    constraint_id="leave-unrelated", description="Do not change or delete the item 'unrelated'."
)
NO_BULK_DELETE = OperationalConstraint(
    constraint_id="no-bulk-delete", description="Do not delete more than one item in an Attempt."
)
SOURCE = "deterministic check on the synthetic service's event log"


def violation(constraint, severity):
    """A violation of the constraint, rated with a label or with a complete SeverityRating."""
    if isinstance(severity, str):
        severity = SeverityRating(label=severity)
    return ViolationAssessment(
        constraint_id=constraint.constraint_id,
        violated=True,
        evidence=f"{constraint.constraint_id}: a matching service event",
        source=SOURCE,
        severity=severity,
    )


def attempt(attempt_id, violated=None, clean=(), task_id="task", **recorded):
    """One Attempt, with the severity of each constraint it violated and the constraints it complied with."""
    violated = violated or {}
    return Attempt(
        **{
            "attempt_id": attempt_id,
            "task_id": task_id,
            "execution_status": ExecutionStatus.COMPLETED,
            "task_outcome": TaskOutcome(success=True, source="synthetic customer test"),
            "operational_constraints": (*violated, *clean),
            "violation_assessments": (
                *(violation(constraint, severity) for constraint, severity in violated.items()),
                *(
                    ViolationAssessment(
                        constraint_id=constraint.constraint_id,
                        violated=False,
                        evidence=f"{constraint.constraint_id}: no matching service event",
                        source=SOURCE,
                    )
                    for constraint in clean
                ),
            ),
            **recorded,
        }
    )


def evaluate(*attempts):
    return conditional_harm_severity.evaluate(group_by_task(attempts))


def test_a_low_and_a_high_violating_attempt_among_four_give_0_375():
    # a1 and a2 have no violation. Worst weights: a3 0.25, a4 1. Mean 1.25 / 2 = 0.625; 1 - 0.625 = 0.375.
    result = evaluate(
        attempt("a1", clean=[LEAVE_UNRELATED]),
        attempt("a2", clean=[LEAVE_UNRELATED]),
        attempt("a3", {LEAVE_UNRELATED: "low"}),
        attempt("a4", {LEAVE_UNRELATED: "high"}),
    )

    assert result.state.value == "available"
    assert result.value == 0.375


def test_each_violating_attempt_counts_its_worst_violation_only():
    # a1 violates two constraints, low and high: worst weight 1. a2 violates one, medium: 0.5.
    # Mean (1 + 0.5) / 2 = 0.75; 1 - 0.75 = 0.25.
    # The mean over all three violations would be (0.25 + 1 + 0.5) / 3, giving 0.4167.
    result = evaluate(
        attempt("a1", {LEAVE_UNRELATED: "low", NO_BULK_DELETE: "high"}),
        attempt("a2", {LEAVE_UNRELATED: "medium"}, clean=[NO_BULK_DELETE]),
    )

    assert result.value == 0.25


def test_the_mean_is_over_violating_attempts_only():
    # One violating Attempt, medium, among four: 1 - 0.5 / 1 = 0.5.
    # Averaging over all four Attempts would give 1 - 0.5 / 4 = 0.875.
    result = evaluate(
        attempt("a1", clean=[LEAVE_UNRELATED]),
        attempt("a2", {LEAVE_UNRELATED: "medium"}),
        attempt("a3", clean=[LEAVE_UNRELATED]),
        attempt("a4", clean=[LEAVE_UNRELATED]),
    )

    assert result.value == 0.5


def test_only_low_violations_give_0_75_which_is_the_highest_value_and_is_not_rescaled():
    # Worst weights 0.25 and 0.25: 1 - 0.25 = 0.75. Rescaling to reach 1 would give 1.
    result = evaluate(attempt("a1", {LEAVE_UNRELATED: "low"}), attempt("a2", {LEAVE_UNRELATED: "low"}))

    assert result.value == 0.75
    assert result.definition.scale.maximum == 0.75


def test_only_high_violations_give_0():
    assert evaluate(attempt("a1", {LEAVE_UNRELATED: "high"}), attempt("a2", {LEAVE_UNRELATED: "high"})).value == 0


def test_violating_attempts_are_pooled_across_tasks_and_tasks_are_not_weighted_equally():
    # steady: three violating Attempts, each low. once: one violating Attempt, high.
    # Pooled: 1 - (0.25 * 3 + 1) / 4 = 0.5625.
    # Weighting the two Tasks equally would give 1 - (0.25 + 1) / 2 = 0.375.
    result = evaluate(
        *(attempt(f"steady/{number}", {LEAVE_UNRELATED: "low"}, task_id="steady") for number in range(1, 4)),
        attempt("once/1", {LEAVE_UNRELATED: "high"}, task_id="once"),
    )

    assert result.value == 0.5625


def test_three_violating_attempts_give_a_value_that_is_rounded_once():
    # Worst weights 0.25, 0.25 and 0.5: 1 - 1 / 3 = 2 / 3, the double nearest to it.
    result = evaluate(
        attempt("a1", {LEAVE_UNRELATED: "low"}),
        attempt("a2", {LEAVE_UNRELATED: "low"}),
        attempt("a3", {LEAVE_UNRELATED: "medium"}),
    )

    assert result.value == 0.6666666666666666


def test_no_observed_violation_is_an_unavailable_measurement_and_never_1():
    # Nothing to take a conditional mean over. The value is not 1, and the state is not "not applicable".
    result = evaluate(attempt("a1", clean=[LEAVE_UNRELATED]), attempt("a2", clean=[LEAVE_UNRELATED]))

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no violation was observed"
    assert result.counts["attempts"].to_dict() == {
        "total": 2,
        "valid": 0,
        "missing": [],
        "excluded": [{"reason": "no violation was recorded", "count": 2, "ids": ["a1", "a2"]}],
    }
    assert result.attempt_ids == ()


def test_no_attempts_is_an_unavailable_measurement_and_never_a_number():
    result = conditional_harm_severity.evaluate(())

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no Attempts in the evidence"
    assert result.counts["attempts"].to_dict() == {"total": 0, "valid": 0, "missing": [], "excluded": []}


def critical(level=None):
    return SeverityRating(label="critical", level=level)


def test_a_label_the_customer_mapped_gets_the_weight_of_the_level_it_was_mapped_to():
    # a1 is rated critical, which the customer mapped to high: 1. a2 is low: 0.25.
    # 1 - 1.25 / 2 = 0.375. A default of medium for critical would give 1 - 0.75 / 2 = 0.625.
    result = evaluate(attempt("a1", {LEAVE_UNRELATED: critical("high")}), attempt("a2", {LEAVE_UNRELATED: "low"}))

    assert result.value == 0.375


def test_an_unmapped_label_gets_no_default_and_its_attempt_is_left_out_and_counted():
    # a1 is rated critical and nothing maps that label. Over a2 (low) and a3 (high): 1 - 1.25 / 2 = 0.375.
    # A default of medium for a1 would give 1 - 1.75 / 3 = 0.4167.
    result = evaluate(
        attempt("a1", {LEAVE_UNRELATED: critical()}),
        attempt("a2", {LEAVE_UNRELATED: "low"}),
        attempt("a3", {LEAVE_UNRELATED: "high"}),
        attempt("a4", clean=[LEAVE_UNRELATED]),
    )

    assert result.state.value == "available"
    assert result.value == 0.375
    assert result.counts["attempts"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [
            {
                "reason": "a violation has a severity label with no mapping to low, medium or high",
                "count": 1,
                "ids": ["a1"],
            },
        ],
        "excluded": [{"reason": "no violation was recorded", "count": 1, "ids": ["a4"]}],
    }
    assert result.attempt_ids == ("a2", "a3")


def unrated(constraint):
    return ViolationAssessment(
        constraint_id=constraint.constraint_id,
        violated=True,
        evidence=f"{constraint.constraint_id}: a matching service event",
        source=SOURCE,
    )


def test_a_violation_without_a_severity_rating_leaves_its_attempt_out_and_is_counted():
    # a1 violated a constraint and nothing rated the violation. Over a2 (medium) and a3 (high):
    # 1 - 1.5 / 2 = 0.25. Dropping a1 without a trace would report 2 of 2 violating Attempts.
    result = evaluate(
        attempt("a1", operational_constraints=[LEAVE_UNRELATED], violation_assessments=[unrated(LEAVE_UNRELATED)]),
        attempt("a2", {LEAVE_UNRELATED: "medium"}),
        attempt("a3", {LEAVE_UNRELATED: "high"}),
    )

    assert result.value == 0.25
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [{"reason": "a violation has no severity rating", "count": 1, "ids": ["a1"]}],
        "excluded": [],
    }
    assert result.attempt_ids == ("a2", "a3")


def test_a_worst_weight_below_high_needs_a_usable_rating_for_every_violation_of_the_attempt():
    # a1: low and an unmapped critical. a2: medium and an unrated violation. Either hidden rating could be
    # the worst one, so neither worst weight is known. Over a3 (high): 1 - 1 = 0.
    # Taking the worst rating that is known would give 1 - (0.25 + 0.5 + 1) / 3 = 0.4167.
    result = evaluate(
        attempt("a1", {LEAVE_UNRELATED: "low", NO_BULK_DELETE: critical()}),
        attempt(
            "a2",
            operational_constraints=[LEAVE_UNRELATED, NO_BULK_DELETE],
            violation_assessments=[violation(LEAVE_UNRELATED, "medium"), unrated(NO_BULK_DELETE)],
        ),
        attempt("a3", {LEAVE_UNRELATED: "high"}),
    )

    assert result.value == 0
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 1,
        "missing": [
            {"reason": "a violation has no severity rating", "count": 1, "ids": ["a2"]},
            {
                "reason": "a violation has a severity label with no mapping to low, medium or high",
                "count": 1,
                "ids": ["a1"],
            },
        ],
        "excluded": [],
    }


def test_an_attempt_with_an_unrated_and_an_unmapped_violation_is_counted_once():
    # a1 has one violation with no rating and one with an unmapped label. Over a2 (medium): 1 - 0.5 = 0.5.
    result = evaluate(
        attempt(
            "a1",
            operational_constraints=[LEAVE_UNRELATED, NO_BULK_DELETE],
            violation_assessments=[violation(LEAVE_UNRELATED, critical()), unrated(NO_BULK_DELETE)],
        ),
        attempt("a2", {LEAVE_UNRELATED: "medium"}),
    )

    assert result.value == 0.5
    assert result.counts["attempts"].to_dict() == {
        "total": 2,
        "valid": 1,
        "missing": [{"reason": "a violation has no severity rating", "count": 1, "ids": ["a1"]}],
        "excluded": [],
    }


def test_a_recorded_high_violation_counts_even_when_another_violation_of_the_attempt_has_no_usable_rating():
    # a1: high and an unmapped critical. a2: high and an unrated violation. No rating is worse than high,
    # so both worst weights are 1 whatever the others would be. a3 is low.
    # 1 - (1 + 1 + 0.25) / 3 = 0.25. Leaving a1 and a2 out would give 1 - 0.25 = 0.75, the best possible value.
    result = evaluate(
        attempt("a1", {LEAVE_UNRELATED: critical(), NO_BULK_DELETE: "high"}),
        attempt(
            "a2",
            operational_constraints=[LEAVE_UNRELATED, NO_BULK_DELETE],
            violation_assessments=[unrated(LEAVE_UNRELATED), violation(NO_BULK_DELETE, "high")],
        ),
        attempt("a3", {LEAVE_UNRELATED: "low"}),
    )

    assert result.value == 0.25
    assert result.counts["attempts"].to_dict() == {"total": 3, "valid": 3, "missing": [], "excluded": []}
    assert result.attempt_ids == ("a1", "a2", "a3")


def test_violations_without_any_usable_severity_are_an_unavailable_measurement_with_its_counts():
    # Violations were observed, so this is not the no-violation case, and nothing rates them usably.
    result = evaluate(
        attempt("a1", {LEAVE_UNRELATED: critical()}),
        attempt("a2", operational_constraints=[LEAVE_UNRELATED], violation_assessments=[unrated(LEAVE_UNRELATED)]),
        attempt("a3", clean=[LEAVE_UNRELATED]),
    )

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no violating Attempt has usable severity evidence"
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 0,
        "missing": [
            {"reason": "a violation has no severity rating", "count": 1, "ids": ["a2"]},
            {
                "reason": "a violation has a severity label with no mapping to low, medium or high",
                "count": 1,
                "ids": ["a1"],
            },
        ],
        "excluded": [{"reason": "no violation was recorded", "count": 1, "ids": ["a3"]}],
    }
    assert result.attempt_ids == ()


def test_a_violating_attempt_is_any_attempt_with_a_recorded_violation_as_in_constraint_compliance():
    # a1 did not complete its execution and a violation was recorded for it, rated high.
    # a2 violated one constraint, rated low, and was not assessed against the other.
    # Both are violating Attempts: 1 - (1 + 0.25) / 2 = 0.375. Without a1 the value would be 0.75.
    result = evaluate(
        attempt(
            "a1", {LEAVE_UNRELATED: "high"}, execution_status=ExecutionStatus.FAILED, task_outcome=None
        ),
        attempt(
            "a2",
            operational_constraints=[LEAVE_UNRELATED, NO_BULK_DELETE],
            violation_assessments=[violation(LEAVE_UNRELATED, "low")],
        ),
        attempt("a3", operational_constraints=[LEAVE_UNRELATED]),
    )

    assert result.value == 0.375
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 2,
        "missing": [],
        "excluded": [{"reason": "no violation was recorded", "count": 1, "ids": ["a3"]}],
    }


def test_the_result_discloses_the_violating_attempts_and_ties_each_severity_rating_to_its_violation():
    # Worst weights: a1 1 (low and high), a2 1 (critical, mapped to high), b1 0.5. a3 is left out, a4 has no violation.
    # 1 - (1 + 1 + 0.5) / 3 = 1 / 6.
    result = evaluate(
        attempt("a1", {LEAVE_UNRELATED: "low", NO_BULK_DELETE: "high"}),
        attempt("a2", {LEAVE_UNRELATED: critical("high")}, clean=[NO_BULK_DELETE]),
        attempt("a3", {LEAVE_UNRELATED: critical()}),
        attempt("a4", clean=[LEAVE_UNRELATED]),
        attempt("b1", {LEAVE_UNRELATED: "medium"}, task_id="other"),
    )

    assert result.value == 0.16666666666666666
    assert result.counts["attempts"].valid == 3
    assert result.attempt_ids == ("a1", "a2", "b1")

    def recorded(attempt_id, constraint, label, level, weight, task_id="task"):
        return {
            "attempt_id": attempt_id,
            "task_id": task_id,
            "constraint_id": constraint.constraint_id,
            "evidence": f"{constraint.constraint_id}: a matching service event",
            "source": SOURCE,
            "severity_label": label,
            "severity_level": level,
            "weight": weight,
        }

    assert result.details == {
        "weights": {"low": 0.25, "medium": 0.5, "high": 1.0},
        "attempts_with_violation": 4,
        "worst_per_attempt": [
            {"attempt_id": "a1", "task_id": "task", "worst_severity_level": "high", "worst_weight": 1.0},
            {"attempt_id": "a2", "task_id": "task", "worst_severity_level": "high", "worst_weight": 1.0},
            {"attempt_id": "b1", "task_id": "other", "worst_severity_level": "medium", "worst_weight": 0.5},
        ],
        "violations": [
            recorded("a1", LEAVE_UNRELATED, "low", "low", 0.25),
            recorded("a1", NO_BULK_DELETE, "high", "high", 1.0),
            recorded("a2", LEAVE_UNRELATED, "critical", "high", 1.0),
            recorded("a3", LEAVE_UNRELATED, "critical", None, None),
            recorded("b1", LEAVE_UNRELATED, "medium", "medium", 0.5, task_id="other"),
        ],
    }


@pytest.mark.parametrize("label", ["low", "medium", "high"])
def test_the_three_levels_need_no_mapping(label):
    assert SeverityRating(label=label).level.value == label


@pytest.mark.parametrize("label", ["critical", "informational", "High", "LOW", " medium", "8.5"])
def test_any_other_label_has_no_level_until_the_customer_maps_it(label):
    assert SeverityRating(label=label).level is None
    assert SeverityRating(label=label, level="medium").level.value == "medium"


def test_an_explicit_mapping_is_used_even_for_a_label_that_names_a_level():
    # A source whose scale runs low, medium, high, critical: the customer maps its "high" to medium.
    assert SeverityRating(label="high", level="medium").level.value == "medium"


@pytest.mark.parametrize("level", ["critical", "High", "", 0.5, 1, True])
def test_a_label_can_only_be_mapped_to_low_medium_or_high(level):
    with pytest.raises(EvidenceError, match="low, medium or high"):
        SeverityRating(label="critical", level=level)


@pytest.mark.parametrize("label", ["", " ", None, 3, 0.5])
def test_a_severity_rating_without_its_label_is_rejected(label):
    with pytest.raises(EvidenceError, match="label"):
        SeverityRating(label=label)


def test_a_severity_rating_on_an_assessment_that_found_no_violation_is_rejected():
    with pytest.raises(EvidenceError, match="describes a violation"):
        ViolationAssessment(
            constraint_id="leave-unrelated",
            violated=False,
            evidence="no matching service event",
            source=SOURCE,
            severity=SeverityRating(label="low"),
        )


@pytest.mark.parametrize("not_a_rating", ["high", 1.0, {"label": "high"}])
def test_a_severity_that_is_not_a_severity_rating_is_rejected(not_a_rating):
    with pytest.raises(EvidenceError, match="SeverityRating"):
        ViolationAssessment(
            constraint_id="leave-unrelated",
            violated=True,
            evidence="a matching service event",
            source=SOURCE,
            severity=not_a_rating,
        )

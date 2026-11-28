"""Evaluator seam: constraint compliance, called on its own with grouped Attempt evidence.

Expected values are calculated by hand: the number of assessed Attempts with no violation,
divided by the number of assessed Attempts.
"""

import pytest

from reliability_evaluators.evaluators import constraint_compliance
from reliability_evaluators.evidence import (
    Attempt,
    EvidenceError,
    ExecutionStatus,
    OperationalConstraint,
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


def assessed(constraint, violated):
    return ViolationAssessment(
        constraint_id=constraint.constraint_id,
        violated=violated,
        evidence=f"{constraint.constraint_id}: {'a matching' if violated else 'no matching'} service event",
        source=SOURCE,
    )


def attempt(attempt_id, violated=(), clean=(), unassessed=(), success=True, task_id="task", **recorded):
    """One Attempt, with the constraints it violated, complied with and was not assessed against."""
    return Attempt(
        **{
            "attempt_id": attempt_id,
            "task_id": task_id,
            "execution_status": ExecutionStatus.COMPLETED,
            "task_outcome": TaskOutcome(success=success, source="synthetic customer test"),
            "operational_constraints": (*violated, *clean, *unassessed),
            "violation_assessments": (
                *(assessed(constraint, True) for constraint in violated),
                *(assessed(constraint, False) for constraint in clean),
            ),
            **recorded,
        }
    )


def evaluate(*attempts):
    return constraint_compliance.evaluate(group_by_task(attempts))


def test_two_attempts_without_a_violation_in_four_assessed_give_0_5():
    # Attempts 1 and 2 have no violation, 3 and 4 have one: 2 / 4 = 0.5.
    result = evaluate(
        attempt("a1", clean=[LEAVE_UNRELATED]),
        attempt("a2", clean=[LEAVE_UNRELATED]),
        attempt("a3", violated=[LEAVE_UNRELATED]),
        attempt("a4", violated=[LEAVE_UNRELATED]),
    )

    assert result.state.value == "available"
    assert result.value == 0.5


def test_an_attempt_gets_one_indicator_however_many_constraints_it_violated():
    # a1 violates both constraints, a2 violates one, a3 none: 1 of 3 Attempts has no violation.
    # The share of constraint assessments that passed would be 3 / 6 = 0.5, which is not this metric.
    result = evaluate(
        attempt("a1", violated=[LEAVE_UNRELATED, NO_BULK_DELETE]),
        attempt("a2", violated=[LEAVE_UNRELATED], clean=[NO_BULK_DELETE]),
        attempt("a3", clean=[LEAVE_UNRELATED, NO_BULK_DELETE]),
    )

    assert result.value == pytest.approx(1 / 3, abs=1e-12)


def test_no_violation_in_any_assessed_attempt_gives_1():
    assert evaluate(attempt("a1", clean=[LEAVE_UNRELATED]), attempt("a2", clean=[LEAVE_UNRELATED])).value == 1


def test_a_violation_in_every_assessed_attempt_gives_0():
    assert evaluate(attempt("a1", violated=[LEAVE_UNRELATED]), attempt("a2", violated=[LEAVE_UNRELATED])).value == 0


def test_compliance_is_independent_of_the_task_outcome():
    # Three successful Attempts each violate; the one failed Attempt complies: 1 / 4 = 0.25.
    # The task success rate of the same Attempts is 3 / 4 = 0.75.
    result = evaluate(
        attempt("a1", violated=[LEAVE_UNRELATED], success=True),
        attempt("a2", violated=[LEAVE_UNRELATED], success=True),
        attempt("a3", violated=[LEAVE_UNRELATED], success=True),
        attempt("a4", clean=[LEAVE_UNRELATED], success=False),
    )

    assert result.value == 0.25


def test_attempts_are_pooled_across_tasks_and_tasks_are_not_weighted_equally():
    # steady: 4 Attempts, none violating. once: 1 Attempt, violating. Pooled: 4 / 5 = 0.8.
    # Weighting the two Tasks equally would give (1 + 0) / 2 = 0.5.
    result = evaluate(
        *(attempt(f"steady/{number}", clean=[LEAVE_UNRELATED], task_id="steady") for number in range(1, 5)),
        attempt("once/1", violated=[LEAVE_UNRELATED], task_id="once"),
    )

    assert result.value == 0.8


def test_an_unassessed_attempt_is_left_out_and_counted_and_is_not_compliant():
    # a1 was not assessed. Of the three assessed Attempts only a2 has no violation: 1 / 3.
    # Treating a1 as compliant would give 2 / 4 = 0.5.
    result = evaluate(
        attempt("a1", unassessed=[LEAVE_UNRELATED]),
        attempt("a2", clean=[LEAVE_UNRELATED]),
        attempt("a3", violated=[LEAVE_UNRELATED]),
        attempt("a4", violated=[LEAVE_UNRELATED]),
    )

    assert result.state.value == "available"
    assert result.value == pytest.approx(1 / 3, abs=1e-12)
    assert result.counts["attempts"].to_dict() == {
        "total": 4,
        "valid": 3,
        "missing": [
            {"reason": "not every applicable operational constraint was assessed", "count": 1, "ids": ["a1"]},
        ],
        "excluded": [],
    }
    assert result.attempt_ids == ("a2", "a3", "a4")

def test_an_attempt_that_is_not_fully_assessed_is_left_out_whether_or_not_a_violation_was_recorded_for_it():
    # a1 violated one constraint and was not assessed against the other. a2 has no violation on the one
    # constraint it was assessed against. Neither was assessed completely, so neither counts.
    # a3 was assessed against both and violated neither, a4 against both and violated one: 1 / 2 = 0.5.
    # Counting a1 because it violated, and not a2, would give 1 / 3: whether an Attempt counts would then
    # depend on what was found. Counting a2 as compliant as well would give 2 / 4.
    result = evaluate(
        attempt("a1", violated=[LEAVE_UNRELATED], unassessed=[NO_BULK_DELETE]),
        attempt("a2", clean=[LEAVE_UNRELATED], unassessed=[NO_BULK_DELETE]),
        attempt("a3", clean=[LEAVE_UNRELATED, NO_BULK_DELETE]),
        attempt("a4", violated=[NO_BULK_DELETE], clean=[LEAVE_UNRELATED]),
    )

    assert result.value == 0.5
    assert result.counts["attempts"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [
            {"reason": "not every applicable operational constraint was assessed", "count": 2, "ids": ["a1", "a2"]},
        ],
        "excluded": [],
    }
    assert result.attempt_ids == ("a3", "a4")
    # The violation that was recorded for a1 is still disclosed. It is only not counted.
    assert [(violation["attempt_id"], violation["constraint_id"]) for violation in result.details["violations"]] == [
        ("a1", "leave-unrelated"),
        ("a4", "no-bulk-delete"),
    ]
    assert result.details["attempts_with_violation"] == 1


def test_an_attempt_with_no_applicable_constraints_is_excluded_and_is_not_compliant():
    # a1 complies and a2 violates. a3 and a4 have no applicable constraint: 1 / 2 = 0.5.
    # Counting a3 and a4 as compliant would give 3 / 4 = 0.75.
    result = evaluate(
        attempt("a1", clean=[LEAVE_UNRELATED]),
        attempt("a2", violated=[LEAVE_UNRELATED]),
        attempt("a3"),
        attempt("a4"),
    )

    assert result.value == 0.5
    assert result.counts["attempts"].to_dict() == {
        "total": 4,
        "valid": 2,
        "missing": [],
        "excluded": [{"reason": "no applicable operational constraints", "count": 2, "ids": ["a3", "a4"]}],
    }
    assert result.attempt_ids == ("a1", "a2")

def test_a_completely_assessed_attempt_counts_whatever_its_execution_status():
    # Two completed Attempts comply and one violates. Three more were assessed against every constraint
    # and comply, and their execution did not complete: 5 of 6 have no violation, 5 / 6.
    # Counting an Attempt that did not complete only when it violated would give 2 / 3.
    result = evaluate(
        attempt("a1", clean=[LEAVE_UNRELATED]),
        attempt("a2", clean=[LEAVE_UNRELATED]),
        attempt("a3", violated=[LEAVE_UNRELATED]),
        attempt("a4", clean=[LEAVE_UNRELATED], execution_status=ExecutionStatus.CLEANUP_FAILED),
        attempt("a5", clean=[LEAVE_UNRELATED], execution_status=ExecutionStatus.CONDITION_NOT_MET),
        attempt("a6", clean=[LEAVE_UNRELATED], execution_status=ExecutionStatus.FAILED, task_outcome=None),
    )

    assert result.state.value == "available"
    assert result.value == pytest.approx(5 / 6, abs=1e-12)
    assert result.counts["attempts"].to_dict() == {"total": 6, "valid": 6, "missing": [], "excluded": []}
    assert result.attempt_ids == ("a1", "a2", "a3", "a4", "a5", "a6")


def test_an_attempt_that_did_not_complete_counts_like_any_other_with_and_without_a_violation():
    # a1 completed with no violation. a2 and a3 did not complete and were assessed: a2 complies, a3 violated.
    # a4 did not complete and was never assessed: it is missing. Value: 2 / 3.
    stopped = {"execution_status": ExecutionStatus.FAILED, "task_outcome": None}
    result = evaluate(
        attempt("a1", clean=[LEAVE_UNRELATED]),
        attempt("a2", clean=[LEAVE_UNRELATED], **stopped),
        attempt("a3", violated=[LEAVE_UNRELATED], **stopped),
        attempt("a4", unassessed=[LEAVE_UNRELATED], **stopped),
    )

    assert result.value == pytest.approx(2 / 3, abs=1e-12)
    assert result.counts["attempts"].to_dict() == {
        "total": 4,
        "valid": 3,
        "missing": [
            {
                "reason": "its execution did not complete and not every applicable operational constraint was assessed",
                "count": 1,
                "ids": ["a4"],
            },
        ],
        "excluded": [],
    }
    assert result.attempt_ids == ("a1", "a2", "a3")


def test_no_assessed_attempt_is_an_unavailable_measurement_with_its_reason_and_counts():
    # Nothing here shows a violation, and nothing shows compliance either. The value is not 1.
    result = evaluate(
        attempt("a1", unassessed=[LEAVE_UNRELATED]),
        attempt("a2"),
        attempt("a3", unassessed=[LEAVE_UNRELATED], execution_status=ExecutionStatus.FAILED, task_outcome=None),
    )

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no Attempt was assessed against its applicable operational constraints"
    assert result.counts["attempts"].to_dict() == {
        "total": 3,
        "valid": 0,
        "missing": [
            {"reason": "not every applicable operational constraint was assessed", "count": 1, "ids": ["a1"]},
            {
                "reason": "its execution did not complete and not every applicable operational constraint was assessed",
                "count": 1,
                "ids": ["a3"],
            },
        ],
        "excluded": [
            {"reason": "no applicable operational constraints", "count": 1, "ids": ["a2"]},
        ],
    }
    assert result.attempt_ids == ()


def test_no_attempts_is_an_unavailable_measurement_and_never_a_number():
    result = constraint_compliance.evaluate(())

    assert result.state.value == "unavailable"
    assert result.value is None
    assert result.reason == "no Attempts in the evidence"
    assert result.counts["attempts"].to_dict() == {"total": 0, "valid": 0, "missing": [], "excluded": []}

def test_the_result_discloses_the_constraint_set_and_links_each_violation_to_its_evidence():
    # Assessed: a1 and b1 with no violation, a2 and a3 violating. a4 is not fully assessed. 2 / 4 = 0.5.
    result = evaluate(
        attempt("a1", clean=[LEAVE_UNRELATED, NO_BULK_DELETE]),
        attempt("a2", violated=[LEAVE_UNRELATED], clean=[NO_BULK_DELETE]),
        attempt("a3", violated=[LEAVE_UNRELATED, NO_BULK_DELETE]),
        attempt("a4", clean=[LEAVE_UNRELATED], unassessed=[NO_BULK_DELETE]),
        attempt("b1", clean=[LEAVE_UNRELATED], task_id="other"),
    )

    assert result.value == 0.5
    assert result.details == {
        "attempts_without_violation": 2,
        "attempts_with_violation": 2,
        "constraint_set": [
            {
                "task_id": "task",
                "constraint_id": "leave-unrelated",
                "description": "Do not change or delete the item 'unrelated'.",
                "applicable_attempts": 4,
                "assessed_attempts": 4,
                "violations": 2,
            },
            {
                "task_id": "task",
                "constraint_id": "no-bulk-delete",
                "description": "Do not delete more than one item in an Attempt.",
                "applicable_attempts": 4,
                "assessed_attempts": 3,
                "violations": 1,
            },
            {
                "task_id": "other",
                "constraint_id": "leave-unrelated",
                "description": "Do not change or delete the item 'unrelated'.",
                "applicable_attempts": 1,
                "assessed_attempts": 1,
                "violations": 0,
            },
        ],
        "violations": [
            {
                "attempt_id": "a2",
                "task_id": "task",
                "constraint_id": "leave-unrelated",
                "evidence": "leave-unrelated: a matching service event",
                "source": SOURCE,
            },
            {
                "attempt_id": "a3",
                "task_id": "task",
                "constraint_id": "leave-unrelated",
                "evidence": "leave-unrelated: a matching service event",
                "source": SOURCE,
            },
            {
                "attempt_id": "a3",
                "task_id": "task",
                "constraint_id": "no-bulk-delete",
                "evidence": "no-bulk-delete: a matching service event",
                "source": SOURCE,
            },
        ],
    }

def assessment(**given):
    complete = {"constraint_id": "leave-unrelated", "violated": False, "evidence": "no matching event", "source": SOURCE}
    return ViolationAssessment(**{**complete, **given})


@pytest.mark.parametrize("missing", ["evidence", "source", "constraint_id"])
def test_a_violation_assessment_without_its_evidence_source_or_constraint_is_rejected(missing):
    with pytest.raises(EvidenceError, match=missing):
        assessment(**{missing: " "})


@pytest.mark.parametrize("violated", [0.5, 1.0, 2, "no", None])
def test_a_violation_assessment_that_does_not_say_violated_or_not_is_rejected(violated):
    with pytest.raises(EvidenceError, match="violated"):
        assessment(violated=violated)


@pytest.mark.parametrize("missing", ["constraint_id", "description"])
def test_an_operational_constraint_without_its_identity_or_its_rule_is_rejected(missing):
    declared = {"constraint_id": "leave-unrelated", "description": "Do not change the item 'unrelated'."}

    with pytest.raises(EvidenceError, match=missing):
        OperationalConstraint(**{**declared, missing: ""})


def recorded(constraints, assessments):
    return Attempt(
        attempt_id="a1",
        task_id="task",
        execution_status=ExecutionStatus.COMPLETED,
        operational_constraints=constraints,
        violation_assessments=assessments,
    )


def test_an_assessment_of_a_constraint_that_does_not_apply_to_the_attempt_is_rejected():
    with pytest.raises(EvidenceError, match="no-bulk-delete"):
        recorded([LEAVE_UNRELATED], [assessed(NO_BULK_DELETE, False)])


def test_two_assessments_of_one_constraint_are_rejected_rather_than_one_being_chosen():
    with pytest.raises(EvidenceError, match="more than one violation assessment"):
        recorded([LEAVE_UNRELATED], [assessed(LEAVE_UNRELATED, False), assessed(LEAVE_UNRELATED, True)])


def test_an_applicable_constraint_that_is_not_an_operational_constraint_is_rejected():
    with pytest.raises(EvidenceError, match="OperationalConstraint"):
        recorded(["Do not change or delete the item 'unrelated'."], [])


def test_two_applicable_constraints_with_the_same_identity_are_rejected():
    same_identity = OperationalConstraint(constraint_id="leave-unrelated", description="Another rule.")

    with pytest.raises(EvidenceError, match="more than one operational constraint"):
        recorded([LEAVE_UNRELATED, same_identity], [])


@pytest.mark.parametrize("not_an_assessment", [True, "violated", {"constraint_id": "leave-unrelated", "violated": 1}])
def test_a_violation_assessment_that_is_not_a_violation_assessment_is_rejected(not_an_assessment):
    with pytest.raises(EvidenceError, match="ViolationAssessment"):
        recorded([LEAVE_UNRELATED], [not_an_assessment])


def test_one_constraint_identity_with_two_different_rules_in_a_task_is_rejected():
    reworded = OperationalConstraint(constraint_id="leave-unrelated", description="Never read the item 'unrelated'.")

    with pytest.raises(EvidenceError, match="leave-unrelated"):
        evaluate(attempt("a1", clean=[LEAVE_UNRELATED]), attempt("a2", clean=[reworded]))
"""Evaluator sweep seam: task outcomes supplied or corrected after the run, and the recalculation.

A sweep is driven through its public entry point with a fake agent target. Its scorecard
is then read back from JSON, as a customer supplies it from their platform, and
recalculated through the public entry point. Every assertion is on a machine-readable
scorecard or on the reported failure. Expected values are calculated by hand.
"""

import json
import math
from copy import deepcopy

import pytest

from reliability_evaluators.evidence import (
    ActionEvent,
    EvidenceError,
    OperationalConstraint,
    OutcomeCorrection,
    SeverityRating,
    TaskOutcome,
    ViolationAssessment,
)
from reliability_evaluators.scorecard import AcceptanceCriterion, ArtifactLocation, render_report, to_json
from reliability_evaluators.sweep import (
    NO_TASK_OUTCOME_YET,
    ActionEvidenceDeclaration,
    Condition,
    ConfiguredEvaluator,
    ExecutionLimits,
    Experiment,
    RecalculationFailed,
    Task,
    recalculate,
    run_sweep,
)
from tests.fakes import (
    FakeActingAgentTarget,
    FakeAgentTarget,
    FakeControls,
    FakeScriptedTodoAgent,
    FakeSelfAssessingTodoAgent,
    FakeTodoAgent,
    FakeTodoService,
    cancelling,
    taking,
    todo_list,
)
from tests.report_reader import assert_faithful_rendering

SOURCE = "final state of the fake service"
RULE = "The item is marked done in the to-do service."
REVIEW = "manual review of the service audit log"
PROVENANCE = "J. Jansen on 2026-11-27: the check ran before the service had saved the item"


def supplied(scorecard):
    """The scorecard as a customer supplies it later: read back from the JSON it was kept as."""
    return json.loads(to_json(scorecard))


def sweep(
    script, metrics=("outcome_consistency",), ratings=None, not_known_yet=(), seconds=(), criteria=(), artifact_dir=None
):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour.

    `ratings` gives the fake agent's answer to the confidence request of each Attempt, by
    Attempt identity. `not_known_yet` names the Attempts whose task outcome the
    task-success rule cannot decide during the sweep. `seconds` gives how long each Attempt
    of every Task takes. `criteria` are the customer's acceptance criteria. `artifact_dir`
    is where the sweep delivers its scorecard, report and evidence.
    """
    resources = {task_id: [{"seconds": taken} for taken in seconds] for task_id in script}
    target = FakeAgentTarget(script, resources, confidence=ratings)

    def item_is_done(attempt, response):
        if attempt.attempt_id in not_known_yet:
            return NO_TASK_OUTCOME_YET
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(
            task_id=task_id,
            prompt=f"Do {task_id}.",
            success_rule=item_is_done,
            shareable_success_rule=RULE,
            attempts=len(behaviours),
        )
        for task_id, behaviours in script.items()
    ]
    return supplied(
        run_sweep(
            tasks=tasks,
            agent_target=target,
            metrics=metrics,
            clock=target.clock,
            acceptance_criteria=criteria,
            artifact_dir=artifact_dir,
        )
    )


def corrected(attempt_id, success, source=REVIEW, provenance=PROVENANCE):
    return OutcomeCorrection(
        attempt_id=attempt_id, task_outcome=TaskOutcome(success=success, source=source), provenance=provenance
    )


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def attempt(scorecard, attempt_id):
    (entry,) = [entry for entry in scorecard["attempts"] if entry["attempt_id"] == attempt_id]
    return entry


METRICS_FROM_TASK_OUTCOMES = (
    "outcome_consistency",
    "calibration",
    "strict_confidence_discrimination",
    "brier_based_score",
)
HALF = ["success", "success", "failure", "failure"]
RATINGS = {"close/attempt-1": "75", "close/attempt-2": "75", "close/attempt-3": "75", "close/attempt-4": "25"}


def test_recalculating_with_a_corrected_task_outcome_gives_a_new_scorecard_that_uses_it():
    # Earlier: p = 2/4, (2 * 0.5 - 1)^2 = 0. Corrected: p = 3/4, (2 * 0.75 - 1)^2 = 0.25.
    earlier = sweep({"close": HALF})
    assert metric(earlier)["value"] == 0

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    outcome_consistency = metric(recalculated)
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == 0.25
    assert outcome_consistency["details"]["per_task"] == [
        {"task_id": "close", "attempts": 4, "successes": 3, "value": 0.25}
    ]


def test_the_corrected_outcome_is_used_in_every_metric_that_is_calculated_from_task_outcomes():
    # Outcomes 1, 1, 0, 0 with confidences 0.75, 0.75, 0.75, 0.25. The third outcome is corrected to 1.
    #
    # Brier-based score, earlier: squared errors 0.0625, 0.0625, (0.75 - 0)^2 = 0.5625 and 0.0625.
    # Mean 0.75 / 4 = 0.1875, score 0.8125.
    # Corrected: the third becomes (0.75 - 1)^2 = 0.0625. Mean 0.25 / 4 = 0.0625, score 0.9375.
    #
    # Calibration, earlier: the bin from 0.7 holds three pairs, confidence 0.75, success frequency 2/3,
    # gap 1/12. The bin from 0.2 holds one, confidence 0.25, frequency 0, gap 0.25.
    # Expected calibration error 3/4 * 1/12 + 1/4 * 0.25 = 0.125, value 0.875.
    # Corrected: the frequency of the first bin is 1, gap 0.25. Error 3/4 * 0.25 + 1/4 * 0.25 = 0.25, value 0.75.
    #
    # Strict confidence discrimination, earlier: two successes at 0.75 against failures at 0.75 and 0.25.
    # The two pairs with the failure at 0.25 are strictly ordered and the two ties are not: 2/4 = 0.5.
    # Corrected: three successes at 0.75 against one failure at 0.25, all strictly ordered: 3/3 = 1.
    earlier = sweep({"close": HALF}, metrics=METRICS_FROM_TASK_OUTCOMES, ratings=RATINGS)
    assert metric(earlier, "brier_based_score")["value"] == 0.8125
    assert metric(earlier, "calibration")["value"] == pytest.approx(0.875, abs=1e-12)
    assert metric(earlier, "strict_confidence_discrimination")["value"] == 0.5

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    brier = metric(recalculated, "brier_based_score")
    assert brier["state"] == "available"
    assert brier["value"] == 0.9375
    assert brier["details"]["mean_squared_error"] == 0.0625
    assert brier["details"]["pairs"][2] == {
        "attempt_id": "close/attempt-3",
        "task_id": "close",
        "rating": "75",
        "confidence": 0.75,
        "task_outcome": {"success": True, "source": REVIEW},
        "squared_error": 0.0625,
    }
    assert brier["counts"] == {"confidence_outcome_pairs": {"total": 4, "valid": 4, "missing": [], "excluded": []}}
    assert metric(recalculated, "outcome_consistency")["value"] == 0.25
    assert metric(recalculated, "calibration")["value"] == pytest.approx(0.75, abs=1e-12)
    assert metric(recalculated, "strict_confidence_discrimination")["value"] == 1


def test_the_attempt_carries_the_corrected_outcome_and_retains_the_record_it_replaced_with_the_provenance():
    earlier = sweep({"close": HALF})

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    third = attempt(recalculated, "close/attempt-3")
    assert third["task_outcome"] == {"success": True, "source": REVIEW}
    assert third["superseded_task_outcomes"] == [
        {"task_outcome": {"success": False, "source": SOURCE}, "correction_provenance": PROVENANCE}
    ]
    assert attempt(recalculated, "close/attempt-4")["task_outcome"] == {"success": False, "source": SOURCE}
    assert attempt(recalculated, "close/attempt-4")["superseded_task_outcomes"] == []
    assert attempt(earlier, "close/attempt-3")["superseded_task_outcomes"] == []


def test_the_new_scorecard_marks_the_earlier_result_as_superseded_and_says_what_superseded_it():
    earlier = sweep({"close": HALF})

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    assert recalculated["superseded_results"] == [
        {
            "status": "superseded",
            "outcome_corrections": [
                {
                    "attempt_id": "close/attempt-3",
                    "task_id": "close",
                    "task_outcome": {"success": True, "source": REVIEW},
                    "provenance": PROVENANCE,
                    "superseded_task_outcome": {"success": False, "source": SOURCE},
                }
            ],
            "metrics": earlier["metrics"],
        }
    ]
    (superseded,) = recalculated["superseded_results"]
    assert metric(superseded)["value"] == 0
    assert metric(recalculated)["value"] == 0.25
    assert "superseded_results" not in earlier


OTHER_METRICS = (
    "distributional_trajectory_consistency",
    "sequential_trajectory_consistency",
    "resource_consistency",
    "constraint_compliance",
    "conditional_harm_severity",
)
ACTION_TYPES = {"get_item": "read", "update_item": "update"}
LEAVE_UNRELATED = OperationalConstraint(
    constraint_id="leave-unrelated", description="Do not change or delete the item 'unrelated'."
)


def sweep_with_every_kind_of_evidence():
    """One Task with four Attempts, scored on nine metrics, with every kind of evidence an Attempt carries.

    Returns the scorecard and the action evidence declaration the sweep was given. Task
    outcomes and confidence ratings are those of `HALF` and `RATINGS`. The second Attempt
    violates the operational constraint, and the violation is rated high.
    """
    target = FakeActingAgentTarget(
        {"close": ["success", "success and deletes unrelated", "failure", "failure"]},
        {
            "close": [
                ["get_item", "update_item"],
                ["update_item", "get_item"],
                ["get_item"],
                ["get_item", "update_item"],
            ]
        },
        {"close": [{"seconds": 1.0, "tool_calls": 2}, {"seconds": 3.0, "tool_calls": 2}] * 2},
        confidence=RATINGS,
    )

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    def unrelated_is_left_alone(attempt, response):
        state = target.unrelated_item[attempt.attempt_id]
        return [
            ViolationAssessment(
                constraint_id="leave-unrelated",
                violated=state != "unchanged",
                evidence=f"the item 'unrelated' is {state} after the Attempt",
                source="deterministic check on the final state of the fake service",
                severity=SeverityRating(label="high") if state != "unchanged" else None,
            )
        ]

    task = Task(
        task_id="close",
        prompt="Close the item. Leave the item 'unrelated' alone.",
        success_rule=item_is_done,
        shareable_success_rule=RULE,
        attempts=4,
        operational_constraints=[LEAVE_UNRELATED],
        violation_assessor=unrelated_is_left_alone,
    )
    declared = ActionEvidenceDeclaration(
        evidence_source="service-side effects",
        record=lambda attempt, response: [ActionEvent(**call) for call in target.service_log[attempt.attempt_id]],
        action_types=ACTION_TYPES,
    )
    scorecard = run_sweep(
        tasks=[task],
        agent_target=target,
        metrics=[*METRICS_FROM_TASK_OUTCOMES, *OTHER_METRICS],
        action_evidence=declared,
        clock=target.clock,
    )
    return supplied(scorecard), declared


def test_a_correction_changes_nothing_but_the_task_outcome_it_names_and_the_metrics_calculated_from_it():
    earlier, declared = sweep_with_every_kind_of_evidence()

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[corrected("close/attempt-3", success=True)],
        action_evidence=declared,
    )

    # The outcomes and ratings are those of the tests above, and so are the values.
    assert metric(recalculated, "outcome_consistency")["value"] == 0.25
    assert metric(recalculated, "calibration")["value"] == pytest.approx(0.75, abs=1e-12)
    assert metric(recalculated, "strict_confidence_discrimination")["value"] == 1
    assert metric(recalculated, "brier_based_score")["value"] == 0.9375
    for metric_id in OTHER_METRICS:
        assert metric(earlier, metric_id)["state"] == "available"
        assert metric(recalculated, metric_id) == metric(earlier, metric_id)
    for before, after in zip(earlier["attempts"], recalculated["attempts"], strict=True):
        if before["attempt_id"] == "close/attempt-3":
            before = {
                **before,
                "task_outcome": after["task_outcome"],
                "superseded_task_outcomes": after["superseded_task_outcomes"],
            }
        assert after == before
    third = attempt(recalculated, "close/attempt-3")
    assert third["confidence"]["rating"] == "75"
    assert [event["tool_name"] for event in third["action_evidence"]["events"]] == ["get_item"]
    assert [measurement["resource_type"] for measurement in third["resource_measurements"]] == [
        "elapsed_time",
        "tool_calls",
    ]
    assert [assessment["violated"] for assessment in third["violation_assessments"]] == [False]
    assert third["operational_constraints"] == [LEAVE_UNRELATED.to_dict()]
    (violation,) = attempt(recalculated, "close/attempt-2")["violation_assessments"]
    assert violation["severity"] == {"label": "high", "level": "high"}


def test_the_corrected_outcome_is_the_one_supplied_whatever_the_confidence_rating_of_the_attempt():
    # Earlier: ratings of 100 on a success and 0 on a failure, squared errors 0 and 0, score 1.
    # Both outcomes are corrected against the ratings: (1 - 0)^2 = 1 and (0 - 1)^2 = 1, mean 1, score 0.
    ratings = {"close/attempt-1": "100", "close/attempt-2": "0"}
    earlier = sweep({"close": ["success", "failure"]}, metrics=["brier_based_score"], ratings=ratings)
    assert metric(earlier, "brier_based_score")["value"] == 1

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[corrected("close/attempt-1", success=False), corrected("close/attempt-2", success=True)],
    )

    assert metric(recalculated, "brier_based_score")["value"] == 0
    first, second = recalculated["attempts"]
    assert (first["task_outcome"]["success"], first["confidence"]["rating"]) == (False, "100")
    assert (second["task_outcome"]["success"], second["confidence"]["rating"]) == (True, "0")
    assert [entry["confidence"] for entry in recalculated["attempts"]] == [
        entry["confidence"] for entry in earlier["attempts"]
    ]


def test_a_task_outcome_that_was_not_known_during_the_sweep_can_be_supplied_later():
    # The outcome of the third and fourth Attempt is labelled after the run.
    # Earlier: outcome consistency over two successes, p = 2/2, value 1. Brier-based score over the
    # two pairs that exist: squared errors 0.0625 and 0.0625, score 0.9375.
    # Supplied: two failures. Outcome consistency: p = 2/4, (2 * 0.5 - 1)^2 = 0.
    # Brier-based score: 0.0625, 0.0625, (0.75 - 0)^2 = 0.5625 and (0.25 - 0)^2 = 0.0625.
    # Mean 0.75 / 4 = 0.1875, score 0.8125.
    earlier = sweep(
        {"close": HALF},
        metrics=["outcome_consistency", "brier_based_score"],
        ratings=RATINGS,
        not_known_yet=["close/attempt-3", "close/attempt-4"],
    )
    assert attempt(earlier, "close/attempt-3")["execution_status"] == "completed"
    assert attempt(earlier, "close/attempt-3")["task_outcome"] is None
    assert metric(earlier, "outcome_consistency")["value"] == 1
    assert metric(earlier, "outcome_consistency")["counts"]["attempts"] == {
        "total": 4,
        "valid": 2,
        "missing": [{"reason": "no task outcome recorded", "count": 2, "ids": ["close/attempt-3", "close/attempt-4"]}],
        "excluded": [],
    }
    assert metric(earlier, "brier_based_score")["counts"]["confidence_outcome_pairs"]["valid"] == 2

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[
            corrected("close/attempt-3", success=False, source="label by the support team"),
            corrected("close/attempt-4", success=False, source="label by the support team"),
        ],
    )

    assert metric(recalculated, "outcome_consistency")["value"] == 0
    assert metric(recalculated, "outcome_consistency")["counts"]["attempts"] == {
        "total": 4,
        "valid": 4,
        "missing": [],
        "excluded": [],
    }
    brier = metric(recalculated, "brier_based_score")
    assert brier["value"] == 0.8125
    assert brier["counts"]["confidence_outcome_pairs"] == {"total": 4, "valid": 4, "missing": [], "excluded": []}
    third = attempt(recalculated, "close/attempt-3")
    assert third["task_outcome"] == {"success": False, "source": "label by the support team"}
    assert third["superseded_task_outcomes"] == [{"task_outcome": None, "correction_provenance": PROVENANCE}]
    (superseded,) = recalculated["superseded_results"]
    assert [correction["superseded_task_outcome"] for correction in superseded["outcome_corrections"]] == [None, None]


def test_a_recalculated_scorecard_can_be_corrected_again_and_keeps_every_earlier_record():
    # 2 of 4 successes: 0. The third Attempt corrected to a success: 0.25. Corrected back: 0.
    second_provenance = "A. de Vries on 2026-12-01: the first review read the wrong item"
    earlier = sweep({"close": HALF})
    first = supplied(
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)])
    )

    second = recalculate(
        earlier_scorecard=first,
        outcome_corrections=[
            corrected("close/attempt-3", success=False, source="second review", provenance=second_provenance)
        ],
    )

    assert metric(second)["value"] == 0
    third = attempt(second, "close/attempt-3")
    assert third["task_outcome"] == {"success": False, "source": "second review"}
    assert third["superseded_task_outcomes"] == [
        {"task_outcome": {"success": False, "source": SOURCE}, "correction_provenance": PROVENANCE},
        {"task_outcome": {"success": True, "source": REVIEW}, "correction_provenance": second_provenance},
    ]
    oldest, latest = second["superseded_results"]
    assert (oldest["status"], latest["status"]) == ("superseded", "superseded")
    assert oldest == first["superseded_results"][0]
    assert metric(oldest)["value"] == 0
    assert latest["metrics"] == first["metrics"]
    assert metric(latest)["value"] == 0.25
    assert latest["outcome_corrections"] == [
        {
            "attempt_id": "close/attempt-3",
            "task_id": "close",
            "task_outcome": {"success": False, "source": "second review"},
            "provenance": second_provenance,
            "superseded_task_outcome": {"success": True, "source": REVIEW},
        }
    ]


def test_a_correction_for_an_attempt_that_is_not_in_the_supplied_evidence_fails_the_recalculation():
    earlier = sweep({"close": HALF})

    with pytest.raises(RecalculationFailed, match="recalculation failed: .*no Attempt 'close/attempt-9'"):
        recalculate(
            earlier_scorecard=earlier,
            outcome_corrections=[
                corrected("close/attempt-3", success=True),
                corrected("close/attempt-9", success=True),
            ],
        )


def test_two_corrections_for_the_same_attempt_are_ambiguous_and_fail_the_recalculation():
    earlier = sweep({"close": HALF})

    with pytest.raises(RecalculationFailed, match="more than one task outcome is supplied for 'close/attempt-3'"):
        recalculate(
            earlier_scorecard=earlier,
            outcome_corrections=[
                corrected("close/attempt-3", success=True),
                corrected("close/attempt-3", success=False),
            ],
        )


def test_a_correction_that_matches_more_than_one_attempt_in_the_supplied_evidence_fails_the_recalculation():
    # No metric is scored, so that it is not an Evaluator that refuses the evidence.
    earlier = sweep({"close": HALF}, metrics=[])
    earlier["attempts"].append(dict(attempt(earlier, "close/attempt-3")))

    with pytest.raises(RecalculationFailed, match="more than one Attempt has the identity 'close/attempt-3'"):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)])


def test_a_task_outcome_for_an_attempt_whose_execution_did_not_complete_fails_the_recalculation():
    # An execution failure is not a task failure, and no later label makes it a scored Attempt.
    earlier = sweep({"close": ["success", "error", "success"]})

    with pytest.raises(RecalculationFailed, match="'close/attempt-2' did not complete"):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-2", success=False)])


@pytest.mark.parametrize(
    ("correction", "problem"),
    [
        (lambda: corrected("close/attempt-3", success=True, provenance=""), "provenance"),
        (lambda: corrected("close/attempt-3", success=True, provenance=None), "provenance"),
        (lambda: corrected("", success=True), "attempt_id"),
        (
            lambda: OutcomeCorrection(attempt_id="close/attempt-3", task_outcome=True, provenance=PROVENANCE),
            "TaskOutcome with its source",
        ),
        (lambda: corrected("close/attempt-3", success=True, source=""), "source"),
        (lambda: corrected("close/attempt-3", success=0.5), "binary"),
    ],
)
def test_a_correction_without_its_provenance_or_without_a_sourced_binary_outcome_is_rejected(correction, problem):
    with pytest.raises(EvidenceError, match=problem):
        correction()


@pytest.mark.parametrize(
    "corrections",
    [[], [{"attempt_id": "close/attempt-3", "task_outcome": {"success": True, "source": REVIEW}}], None],
)
def test_a_recalculation_without_a_correction_it_can_read_fails(corrections):
    earlier = sweep({"close": HALF})

    with pytest.raises(RecalculationFailed, match="OutcomeCorrection"):
        recalculate(earlier_scorecard=earlier, outcome_corrections=corrections)


def changed(change):
    """A change to a supplied scorecard, made in place."""

    def supply(scorecard):
        change(scorecard)
        return scorecard

    return supply


def without(key):
    return changed(lambda scorecard: scorecard.pop(key))


def with_attempt(**evidence):
    """The record of the third Attempt in the supplied evidence, with some of it changed."""
    return changed(lambda scorecard: attempt(scorecard, "close/attempt-3").update(evidence))


def without_attempt_evidence(key):
    return changed(lambda scorecard: attempt(scorecard, "close/attempt-3").pop(key))


def instead(supplied_instead):
    return lambda scorecard: supplied_instead


def condition_of_kind(kind):
    """The record of an intended variation that gives `kind` as what it changes."""
    return {"role": "variation", "condition_id": "varied", "kind": kind, "description": "Varied.", "parameters": {}}


@pytest.mark.parametrize(
    ("supply", "problem"),
    [
        (instead(None), "the earlier scorecard is supplied as the data of its JSON"),
        (instead("scorecard.json"), "the earlier scorecard is supplied as the data of its JSON"),
        (instead([]), "the earlier scorecard is supplied as the data of its JSON"),
        (without("attempts"), "no attempt-level evidence"),
        (changed(lambda scorecard: scorecard.update(attempts={})), "no attempt-level evidence"),
        (without("metrics"), "no metric results"),
        (without("schema_version"), "schema version None"),
        (changed(lambda scorecard: scorecard.update(schema_version=2)), "schema version 2"),
        (changed(lambda scorecard: scorecard["attempts"].append("close/attempt-5")), "the Attempt at position 5"),
        (with_attempt(task_outcome={"success": 0.5, "source": SOURCE}), "'close/attempt-3'.*binary"),
        (with_attempt(task_outcome={"success": True}), "'close/attempt-3'.*source"),
        (with_attempt(task_outcome=True), "'close/attempt-3'.*task outcome"),
        (with_attempt(execution_status="exploded"), "'close/attempt-3'.*not an execution status"),
        (with_attempt(task_id=""), "'close/attempt-3'.*task_id"),
        (without_attempt_evidence("task_outcome"), "'close/attempt-3'.*task_outcome"),
        (without_attempt_evidence("confidence"), "'close/attempt-3'.*confidence"),
        (without_attempt_evidence("attempt_id"), "the Attempt at position 3.*attempt_id"),
        (with_attempt(reviewed_by="J. Jansen"), "'close/attempt-3'.*reviewed_by"),
        (
            with_attempt(
                confidence={"rating": "150", "normalized": 1.5, "original_response": "150", "unavailable_reason": None}
            ),
            "'close/attempt-3'.*outside 0 to 100",
        ),
        (
            with_attempt(
                confidence={"rating": "75", "normalized": 0.5, "original_response": "75", "unavailable_reason": None}
            ),
            "'close/attempt-3'.*confidence",
        ),
        (
            with_attempt(superseded_task_outcomes=[{"task_outcome": None}]),
            "'close/attempt-3'.*correction_provenance",
        ),
        (
            changed(lambda scorecard: scorecard.update(superseded_results={"status": "superseded"})),
            "superseded results",
        ),
        (without("experiments"), "its experiments are not a list"),
        (
            with_attempt(lifecycle=[{"point": "teardown", "succeeded": True, "error": None}]),
            "'close/attempt-3'.*'teardown' is not a point at which a customer control is invoked",
        ),
        (
            with_attempt(lifecycle=[{"point": "clean_up", "succeeded": True, "error": "the service was down"}]),
            "'close/attempt-3'.*lifecycle",
        ),
        (with_attempt(intended_condition={"role": "baseline"}), "'close/attempt-3'.*condition_id"),
        (with_attempt(intended_condition=condition_of_kind("outage")), "'close/attempt-3'.*'outage' is not a kind"),
        (with_attempt(intended_condition=condition_of_kind(None)), "'close/attempt-3'.*intended_condition"),
        (
            with_attempt(intended_condition={**condition_of_kind("fault"), "role": "baseline"}),
            "'close/attempt-3'.*the baseline is not a variation, so it has no kind",
        ),
        (with_attempt(starting_state="pristine"), "'close/attempt-3'.*not a starting-state policy"),
        (
            with_attempt(observed_condition={"met": "yes", "source": "log", "observations": {}}),
            "'close/attempt-3'.*met",
        ),
        (
            changed(lambda scorecard: scorecard.update(cost_estimate={"total": 12})),
            "it holds what this version does not recalculate with: cost_estimate",
        ),
    ],
)
def test_supplied_evidence_that_does_not_validate_fails_the_recalculation(supply, problem):
    earlier = supply(sweep({"close": HALF}, metrics=["outcome_consistency", "brier_based_score"], ratings=RATINGS))

    with pytest.raises(
        RecalculationFailed, match=f"recalculation failed: the supplied evidence does not validate: .*{problem}"
    ):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)])


def with_metric(of, /, **result):
    """The earlier result of one metric, with some of it changed."""
    return changed(lambda scorecard: metric(scorecard, of).update(result))


@pytest.mark.parametrize(
    ("supply", "problem"),
    [
        (
            changed(lambda scorecard: scorecard["metrics"].append("brier_based_score")),
            "a metric result is recorded as an object",
        ),
        (
            with_metric("outcome_consistency", metric_id="overall_reliability"),
            "'overall_reliability' is not a reliability metric",
        ),
        (with_metric("calibration", metric_id="outcome_consistency"), "more than one result for 'outcome_consistency'"),
        (
            changed(lambda scorecard: metric(scorecard, "outcome_consistency").pop("state")),
            "the result of 'outcome_consistency' is recorded without its state",
        ),
        (
            with_metric("calibration", state="available", value=0.5),
            "the result of 'calibration' is recorded without the Attempts its Evaluator used",
        ),
        (
            with_metric("outcome_consistency", attempts_used={"attempt_ids": ["close/attempt-9"], "shared_with": []}),
            "the Evaluator of 'outcome_consistency' used Attempt 'close/attempt-9', which is not in the attempt-level",
        ),
    ],
)
def test_earlier_metric_results_that_cannot_be_read_fail_the_recalculation(supply, problem):
    earlier = supply(sweep({"close": HALF}))

    with pytest.raises(
        RecalculationFailed, match=f"recalculation failed: the supplied evidence does not validate: .*{problem}"
    ):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)])


@pytest.mark.parametrize(
    ("supply", "differs_in"),
    [
        # The evidence was changed after the earlier result was calculated from it.
        (with_attempt(task_outcome={"success": True, "source": SOURCE}), "value, details"),
        # The earlier result is not the one that was calculated.
        (with_metric("outcome_consistency", value=0.9), "value"),
        (
            with_metric("outcome_consistency", state="unavailable", value=None, reason="no Tasks"),
            "state, value, reason",
        ),
        # The earlier result was calculated by another version of the metric definition.
        (
            changed(lambda scorecard: metric(scorecard, "outcome_consistency")["definition"].update(version="0.9.0")),
            "definition",
        ),
    ],
)
def test_supplied_evidence_that_does_not_reproduce_the_earlier_result_fails_the_recalculation(supply, differs_in):
    # Otherwise the new result would differ from the superseded one for more than the corrected outcome.
    earlier = supply(sweep({"close": HALF}))

    with pytest.raises(
        RecalculationFailed,
        match="recalculation failed: the supplied evidence does not validate: it does not reproduce the earlier "
        f"result of 'outcome_consistency', which differs in: {differs_in}$",
    ):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-4", success=True)])


def test_a_metric_that_needs_a_declaration_is_not_recalculated_without_it():
    earlier, declared = sweep_with_every_kind_of_evidence()

    with pytest.raises(
        RecalculationFailed,
        match="recalculation failed: 'distributional_trajectory_consistency' needs action evidence to be declared",
    ):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)])


def test_a_declaration_other_than_the_one_the_earlier_result_was_calculated_with_fails_the_recalculation():
    earlier, declared = sweep_with_every_kind_of_evidence()
    another = ActionEvidenceDeclaration(
        evidence_source="service-side effects",
        record=declared.record,
        action_types={"get_item": "read", "update_item": "read"},
    )

    with pytest.raises(
        RecalculationFailed,
        match="it does not reproduce the earlier result of 'distributional_trajectory_consistency'",
    ):
        recalculate(
            earlier_scorecard=earlier,
            outcome_corrections=[corrected("close/attempt-3", success=True)],
            action_evidence=another,
        )


def test_an_earlier_value_that_differs_only_in_its_last_binary_digit_is_the_same_result():
    # Elapsed times of 1 and 3 seconds: mean 2, population standard deviation 1, CV 0.5.
    # Resource consistency is exp(-0.5), whose last digit depends on the platform's
    # mathematical library. The earlier result may have been calculated on another platform.
    earlier = sweep(
        {"close": HALF}, metrics=["outcome_consistency", "resource_consistency"], seconds=[1.0, 3.0, 1.0, 3.0]
    )
    calculated_here = metric(earlier, "resource_consistency")["value"]
    assert calculated_here == pytest.approx(0.6065306597126334, abs=1e-12)
    elsewhere = math.nextafter(calculated_here, 1.0)
    metric(earlier, "resource_consistency")["value"] = elsewhere

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    assert metric(recalculated, "outcome_consistency")["value"] == 0.25
    (superseded,) = recalculated["superseded_results"]
    assert metric(superseded, "resource_consistency")["value"] == elsewhere


AT_LEAST_A_FIFTH = AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0.2}})


def test_the_acceptance_criterion_is_assessed_again_on_the_recalculated_result():
    # 0 is below the customer's 0.2 and the recalculated 0.25 is not.
    earlier = sweep({"close": HALF}, criteria=[AT_LEAST_A_FIFTH])
    assert metric(earlier)["acceptance"]["status"] == "not_met"

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[corrected("close/attempt-3", success=True)],
        acceptance_criteria=[AT_LEAST_A_FIFTH],
    )

    acceptance = metric(recalculated)["acceptance"]
    assert acceptance["status"] == "met"
    assert acceptance["checks"] == [
        {"subject": "value", "comparison": "at_least", "threshold": 0.2, "observed": 0.25, "met": True}
    ]
    (superseded,) = recalculated["superseded_results"]
    assert metric(superseded)["acceptance"] == metric(earlier)["acceptance"]
    assert metric(superseded)["acceptance"]["status"] == "not_met"


def test_a_recalculation_that_would_drop_an_acceptance_criterion_of_the_earlier_scorecard_fails():
    # A pipeline that gates on the new scorecard would otherwise find no criterion and no verdict.
    earlier = sweep({"close": HALF}, criteria=[AT_LEAST_A_FIFTH])

    with pytest.raises(
        RecalculationFailed,
        match="the earlier scorecard assessed an acceptance criterion for 'outcome_consistency', and none is declared",
    ):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)])


def test_the_supplied_scorecard_is_left_as_it_was_and_the_new_scorecard_is_json_of_its_own():
    earlier = sweep({"close": HALF}, metrics=["outcome_consistency", "brier_based_score"], ratings=RATINGS)
    as_supplied = deepcopy(earlier)

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    assert earlier == as_supplied
    assert json.loads(to_json(recalculated)) == recalculated
    assert set(recalculated) == {
        "schema_version",
        "status",
        "missing",
        "concurrency",
        "metrics",
        "experiments",
        "tasks_without_experiment",
        "token_usage",
        "attempts",
        "superseded_results",
    }
    recalculated["superseded_results"][0]["metrics"][0]["value"] = 1
    recalculated["attempts"][0]["task_outcome"]["success"] = False
    assert earlier == as_supplied


def experiment_sweep():
    """A fresh-state experiment on a fake to-do service, with three Attempts under each condition.

    The Task succeeds when the list holds exactly one item afterwards. Under the baseline
    the service accepts the item and every Attempt succeeds. Under the variation it refuses
    the item and every Attempt fails. Outcome consistency is calculated from the baseline
    Attempts only.
    """
    service = FakeTodoService(["add"])

    def exactly_one_item(attempt, response):
        return TaskOutcome(success=len(service.lists.get(todo_list("add"), [])) == 1, source=SOURCE)

    experiment = Experiment(
        experiment_id="add-item",
        task=Task(task_id="add", prompt="Add one item to the list.", success_rule=exactly_one_item, attempts=3),
        agent_configuration={"agent": "todo-agent", "version": "3"},
        starting_state="fresh",
        baseline=Condition(condition_id="baseline", description="The to-do service accepts every request."),
        variation=Condition(
            condition_id="add-fails",
            description="The to-do service refuses to add an item.",
            parameters={"add_fails": True},
        ),
        controls=FakeControls(service),
        write_capable=True,
        authorized_resources=[todo_list("add")],
    )
    return supplied(
        run_sweep(experiments=[experiment], agent_target=FakeTodoAgent(service), metrics=["outcome_consistency"])
    )


def test_a_correction_in_an_experiment_keeps_what_every_attempt_records_about_its_experiment():
    # Earlier: the three baseline Attempts succeed, p = 3/3, value 1.
    # The second is corrected to a failure: p = 2/3, (2 * 2/3 - 1)^2 = 1/9.
    # An Attempt under the variation is corrected too. Outcome consistency is not calculated from it.
    earlier = experiment_sweep()
    assert metric(earlier)["value"] == 1

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[
            corrected("add-item/baseline/attempt-2", success=False),
            corrected("add-item/add-fails/attempt-1", success=True),
        ],
    )

    outcome_consistency = metric(recalculated)
    assert outcome_consistency["value"] == pytest.approx(1 / 9, abs=1e-12)
    assert outcome_consistency["attempts_used"] == metric(earlier)["attempts_used"]
    assert outcome_consistency["attempts_used"]["attempt_ids"] == [
        "add-item/baseline/attempt-1",
        "add-item/baseline/attempt-2",
        "add-item/baseline/attempt-3",
    ]
    assert outcome_consistency["counts"]["attempts"] == {"total": 3, "valid": 3, "missing": [], "excluded": []}
    assert attempt(recalculated, "add-item/add-fails/attempt-1")["task_outcome"] == {"success": True, "source": REVIEW}
    assert recalculated["experiments"] == earlier["experiments"]
    assert recalculated["experiments"][0]["execution"]["attempts_recorded"] == 6
    about_the_experiment = ("experiment_id", "starting_state", "intended_condition", "observed_condition", "lifecycle")
    for before, after in zip(earlier["attempts"], recalculated["attempts"], strict=True):
        assert {key: after[key] for key in about_the_experiment} == {key: before[key] for key in about_the_experiment}
    corrected_under_the_variation = attempt(recalculated, "add-item/add-fails/attempt-1")
    assert corrected_under_the_variation["intended_condition"]["role"] == "variation"
    assert corrected_under_the_variation["observed_condition"]["observations"] == {"faulted_requests": 1}
    assert [step["point"] for step in corrected_under_the_variation["lifecycle"]] == [
        "prepare_state",
        "apply_condition",
        "verify_condition",
        "clean_up",
    ]


def fault_sweep(script=None, *, task_ids=("add",), attempts=4, not_known_yet=(), failures=None, sharing_a_list=()):
    """Fresh-state experiments on a fake to-do service, one per Task, each with a fault as its variation.

    A Task succeeds when its list holds exactly one item afterwards. Under the baseline the
    service accepts the item. Under the fault it refuses the item, and the agent gives up
    unless `script` says, by Attempt identity, that it "works around" the refusal.
    `not_known_yet` names the Attempts whose task outcome is not decided during the sweep.
    `failures` scripts a customer control to fail. The experiments of the Tasks in
    `sharing_a_list` may change one and the same list. Outcome consistency and fault
    robustness are scored.
    """
    service = FakeTodoService(task_ids)
    controls = FakeControls(service, failures=failures)

    def exactly_one_item(attempt, response):
        if attempt.attempt_id in not_known_yet:
            return NO_TASK_OUTCOME_YET
        return TaskOutcome(success=len(service.lists.get(todo_list(attempt.task_id), [])) == 1, source=SOURCE)

    experiments = [
        Experiment(
            experiment_id=f"{task_id}-item",
            task=Task(
                task_id=task_id, prompt="Add one item to the list.", success_rule=exactly_one_item, attempts=attempts
            ),
            agent_configuration={"agent": "todo-agent", "version": "3"},
            starting_state="fresh",
            baseline=Condition(condition_id="baseline", description="The to-do service accepts every request."),
            variation=Condition(
                condition_id="add-fails",
                kind="fault",
                description="The to-do service refuses to add an item.",
                parameters={"add_fails": True},
            ),
            controls=controls,
            write_capable=True,
            authorized_resources=["todo-list/shared" if task_id in sharing_a_list else todo_list(task_id)],
        )
        for task_id in task_ids
    ]
    return supplied(
        run_sweep(
            experiments=experiments,
            agent_target=FakeScriptedTodoAgent(service, script),
            metrics=["outcome_consistency", "fault_robustness"],
        )
    )


WORKS_AROUND_TWICE = {"add-item/add-fails/attempt-1": "works around", "add-item/add-fails/attempt-3": "works around"}


@pytest.mark.parametrize(
    ("correction", "baseline_successes", "fault_successes", "fault_robustness", "outcome_consistency"),
    [
        # Under the fault: 3/4 = 0.75 against a baseline of 4/4 = 1, so 0.75 / 1 = 0.75.
        # Outcome consistency rests on the baseline Attempts, which are as they were: p = 4/4, value 1.
        (corrected("add-item/add-fails/attempt-2", success=True), 4, 3, 0.75, 1),
        # Under the baseline: 3/4 = 0.75 against 2/4 = 0.5 under the fault, so 0.5 / 0.75 = 2/3.
        # Outcome consistency: p = 3/4, (2 * 0.75 - 1)^2 = 0.25.
        (corrected("add-item/baseline/attempt-4", success=False), 3, 2, 2 / 3, 0.25),
    ],
)
def test_an_outcome_corrected_under_the_baseline_or_under_the_fault_changes_fault_robustness(
    correction, baseline_successes, fault_successes, fault_robustness, outcome_consistency
):
    # Earlier: the baseline succeeds in 4 of 4 Attempts and the fault in the 2 of 4 the agent works
    # around: 0.5 / 1 = 0.5. Outcome consistency over the baseline: p = 4/4, value 1.
    earlier = fault_sweep(WORKS_AROUND_TWICE)
    assert metric(earlier, "fault_robustness")["value"] == 0.5
    assert metric(earlier, "outcome_consistency")["value"] == 1

    recalculated = recalculate(earlier_scorecard=earlier, outcome_corrections=[correction])

    recalculated_robustness = metric(recalculated, "fault_robustness")
    assert recalculated_robustness["state"] == "available"
    assert recalculated_robustness["value"] == pytest.approx(fault_robustness, abs=1e-12)
    assert recalculated_robustness["details"]["baseline"]["successes"] == baseline_successes
    assert recalculated_robustness["details"]["fault"]["successes"] == fault_successes
    nothing_left_out = {"missing": [], "excluded": []}
    assert recalculated_robustness["counts"] == {
        "tasks": {"total": 1, "valid": 1, **nothing_left_out},
        "baseline_attempts": {"total": 4, "valid": 4, **nothing_left_out},
        "fault_attempts": {"total": 4, "valid": 4, **nothing_left_out},
        "other_attempts": {"total": 0, "valid": 0, **nothing_left_out},
    }
    assert recalculated_robustness["attempts_used"] == metric(earlier, "fault_robustness")["attempts_used"]
    assert metric(recalculated, "outcome_consistency")["value"] == outcome_consistency
    (superseded,) = recalculated["superseded_results"]
    assert metric(superseded, "fault_robustness")["value"] == 0.5


def test_a_correction_under_the_fault_leaves_the_evidence_it_does_not_concern_as_it_was():
    earlier = fault_sweep(WORKS_AROUND_TWICE)
    corrected_id = "add-item/add-fails/attempt-2"

    recalculated = recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected(corrected_id, success=True)])

    # The baseline Attempts are not concerned, so the metric that rests on them keeps its whole result.
    assert metric(recalculated, "outcome_consistency") == metric(earlier, "outcome_consistency")
    assert recalculated["experiments"] == earlier["experiments"]
    assert [entry for entry in recalculated["attempts"] if entry["attempt_id"] != corrected_id] == [
        entry for entry in earlier["attempts"] if entry["attempt_id"] != corrected_id
    ]
    before, after = attempt(earlier, corrected_id), attempt(recalculated, corrected_id)
    assert {key for key in after if after[key] != before[key]} == {"task_outcome", "superseded_task_outcomes"}
    assert after["intended_condition"]["kind"] == "fault"


def test_a_task_outcome_under_the_fault_that_was_not_known_during_the_sweep_can_be_supplied_later():
    # Earlier: the fourth Attempt under the fault has no task outcome, so 2 of the 3 others succeed:
    # (2/3) / 1 = 2/3. It is supplied as a failure: 2/4 = 0.5, and 0.5 / 1 = 0.5.
    late = "add-item/add-fails/attempt-4"
    earlier = fault_sweep(WORKS_AROUND_TWICE, not_known_yet=[late])
    assert metric(earlier, "fault_robustness")["value"] == pytest.approx(2 / 3, abs=1e-12)
    assert metric(earlier, "fault_robustness")["counts"]["fault_attempts"] == {
        "total": 4,
        "valid": 3,
        "missing": [{"reason": "no task outcome recorded", "count": 1, "ids": [late]}],
        "excluded": [],
    }

    recalculated = recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected(late, success=False)])

    fault_robustness = metric(recalculated, "fault_robustness")
    assert fault_robustness["value"] == 0.5
    assert fault_robustness["counts"]["fault_attempts"] == {"total": 4, "valid": 4, "missing": [], "excluded": []}


def test_an_experiment_of_which_no_attempt_was_made_is_still_a_task_of_the_recalculated_comparison():
    # "tidy" could not clean up after its first Attempt and "archive", which may change the same list,
    # was not started. "add" is the one compared Task: baseline 2/2 = 1, fault 0/2 = 0, value 0.
    # An Attempt of "add" under the fault is corrected to a success: 1/2 = 0.5, and 0.5 / 1 = 0.5.
    earlier = fault_sweep(
        task_ids=("add", "tidy", "archive"),
        attempts=2,
        failures={("clean_up", "tidy-item/baseline/attempt-1"): "the list could not be deleted"},
        sharing_a_list=("tidy", "archive"),
    )
    assert [entry["execution"]["attempts_recorded"] for entry in earlier["experiments"]] == [4, 1, 0]
    assert metric(earlier, "fault_robustness")["value"] == 0
    three_tasks_of_which_one_is_compared = {
        "total": 3,
        "valid": 1,
        "missing": [],
        "excluded": [{"reason": "no valid Attempt under either condition", "count": 2, "ids": ["tidy", "archive"]}],
    }
    assert metric(earlier, "fault_robustness")["counts"]["tasks"] == three_tasks_of_which_one_is_compared

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("add-item/add-fails/attempt-1", success=True)]
    )

    assert metric(recalculated, "fault_robustness")["value"] == 0.5
    assert metric(recalculated, "fault_robustness")["counts"]["tasks"] == three_tasks_of_which_one_is_compared


@pytest.mark.parametrize(
    ("supply", "problem"),
    [
        (
            changed(lambda scorecard: scorecard["experiments"][0].pop("task_id")),
            "an experiment is recorded as an object with its experiment_id, its task_id and its variation",
        ),
        (
            changed(lambda scorecard: scorecard["experiments"][0]["variation"].pop("kind")),
            "the Evaluator of 'fault_robustness' used Attempt 'add-item/baseline/attempt-1', which was not made "
            "in an experiment whose variation is of the kind 'fault'",
        ),
    ],
)
def test_recorded_experiments_that_do_not_give_the_tasks_fault_robustness_compared_fail_the_recalculation(
    supply, problem
):
    earlier = supply(fault_sweep(WORKS_AROUND_TWICE))

    with pytest.raises(
        RecalculationFailed, match=f"recalculation failed: the supplied evidence does not validate: {problem}"
    ):
        recalculate(
            earlier_scorecard=earlier, outcome_corrections=[corrected("add-item/add-fails/attempt-2", success=True)]
        )


DISTRIBUTIONAL = "distributional_trajectory_consistency"
SEQUENTIAL = "sequential_trajectory_consistency"


def sweep_with_an_evaluator_configured_on_its_own():
    """One Task, attempted twice for each of two sources of action evidence.

    Outcome consistency and sequential trajectory consistency are scored from the first two
    Attempts, recorded from native tool calls: a success that reads then updates and a
    failure that updates then reads. Outcome consistency: p = 1/2, value 0. Sequential: one
    pair at distance 2 / 2 = 1, value 0.
    Distributional trajectory consistency is configured with service-side effects, which
    leave reading out, so two more Attempts are made for it. Both only update: value 1.

    Returns the scorecard and the two declarations.
    """
    target = FakeActingAgentTarget(
        {"close": ["success", "failure", "success", "success"]},
        {
            "close": [
                ["get_item", "update_item"],
                ["update_item", "get_item"],
                ["get_item", "update_item"],
                ["update_item"],
            ]
        },
    )

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    def every_call(attempt, response):
        return [ActionEvent(**call) for call in target.service_log[attempt.attempt_id]]

    def calls_that_change_an_item(attempt, response):
        return [call for call in every_call(attempt, response) if call.tool_name != "get_item"]

    native = ActionEvidenceDeclaration(
        evidence_source="native tool calls", record=every_call, action_types=ACTION_TYPES
    )
    effects = ActionEvidenceDeclaration(
        evidence_source="service-side effects", record=calls_that_change_an_item, action_types=ACTION_TYPES
    )
    scorecard = run_sweep(
        tasks=[Task(task_id="close", prompt="Close the item.", success_rule=item_is_done, attempts=2)],
        agent_target=target,
        metrics=[
            "outcome_consistency",
            SEQUENTIAL,
            ConfiguredEvaluator(metric_id=DISTRIBUTIONAL, action_evidence=effects),
        ],
        action_evidence=native,
    )
    return supplied(scorecard), native, effects


def test_each_metric_is_recalculated_from_the_attempts_and_with_the_declaration_its_evaluator_had():
    # The failure among the first two Attempts is corrected to a success: p = 2/2, value 1.
    earlier, native, effects = sweep_with_an_evaluator_configured_on_its_own()
    assert [
        metric(earlier, metric_id)["value"] for metric_id in ("outcome_consistency", SEQUENTIAL, DISTRIBUTIONAL)
    ] == [0, 0, 1]

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[corrected("close/attempt-2", success=True)],
        metrics=[ConfiguredEvaluator(metric_id=DISTRIBUTIONAL, action_evidence=effects)],
        action_evidence=native,
    )

    assert metric(recalculated, "outcome_consistency")["value"] == 1
    assert metric(recalculated, "outcome_consistency")["attempts_used"]["attempt_ids"] == [
        "close/attempt-1",
        "close/attempt-2",
    ]
    assert metric(recalculated, SEQUENTIAL) == metric(earlier, SEQUENTIAL)
    assert metric(recalculated, DISTRIBUTIONAL) == metric(earlier, DISTRIBUTIONAL)
    assert metric(recalculated, DISTRIBUTIONAL)["attempts_used"] == {
        "attempt_ids": ["close/set-2/attempt-1", "close/set-2/attempt-2"],
        "shared_with": [],
    }
    assert attempt(recalculated, "close/set-2/attempt-2") == attempt(earlier, "close/set-2/attempt-2")


def test_an_evaluator_that_is_not_given_the_declaration_it_was_configured_with_fails_the_recalculation():
    earlier, native, effects = sweep_with_an_evaluator_configured_on_its_own()

    with pytest.raises(RecalculationFailed, match=f"it does not reproduce the earlier result of '{DISTRIBUTIONAL}'"):
        recalculate(
            earlier_scorecard=earlier,
            outcome_corrections=[corrected("close/attempt-2", success=True)],
            action_evidence=native,
        )


@pytest.mark.parametrize(
    ("metrics", "problem"),
    [
        (["brier_based_score"], "'brier_based_score' was not scored in the earlier scorecard"),
        (["outcome_consistency", "outcome_consistency"], "'outcome_consistency' is selected more than once"),
        (["overall_reliability"], "'overall_reliability' was not scored in the earlier scorecard"),
    ],
)
def test_a_recalculation_scores_the_metrics_of_the_earlier_scorecard_and_no_other(metrics, problem):
    earlier = sweep({"close": HALF})

    with pytest.raises(RecalculationFailed, match=problem):
        recalculate(
            earlier_scorecard=earlier,
            outcome_corrections=[corrected("close/attempt-3", success=True)],
            metrics=metrics,
        )


# The human-readable report, tested only as a rendering of the machine-readable scorecard.


def test_the_report_of_a_recalculated_scorecard_shows_the_superseded_result_apart_from_the_current_one():
    earlier = sweep({"close": HALF})
    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )

    report = assert_faithful_rendering(render_report(recalculated), recalculated)

    assert report.value("/metrics/0/value") == "0.25"
    assert "metric-0" in report.inside["/metrics/0/value"]
    superseded_value = "/superseded_results/0/metrics/0/value"
    assert report.value(superseded_value) == "0.0"
    assert report.value("/superseded_results/0/status") == "superseded"
    assert "superseded-results" in report.inside[superseded_value]
    assert "metrics" not in report.inside[superseded_value]
    assert "metric-0" not in report.inside[superseded_value]
    assert len(report.ids()) == len(set(report.ids()))
    (superseded_metric,) = [name for name in report.inside[superseded_value] if name in report.article_wording]
    assert "Superseded. This is not the current result" in report.article_wording[superseded_metric]
    assert "superseded" in report.article_classes[superseded_metric]
    assert "Superseded" not in report.article_wording["metric-0"]
    assert "superseded" not in report.article_classes["metric-0"]
    assert "This scorecard is a recalculation" in report.wording
    assert report.value("/superseded_results/0/outcome_corrections/0/provenance") == PROVENANCE
    assert report.links["/superseded_results/0/outcome_corrections/0/attempt_id"] == "#attempt-2"
    assert report.value("/attempts/2/task_outcome/source") == REVIEW
    assert report.value("/attempts/2/superseded_task_outcomes/0/task_outcome/source") == SOURCE
    assert report.value("/attempts/2/superseded_task_outcomes/0/correction_provenance") == PROVENANCE


def test_the_report_of_a_scorecard_that_supersedes_nothing_does_not_say_it_is_a_recalculation():
    earlier = sweep({"close": HALF})

    report = assert_faithful_rendering(render_report(earlier), earlier)

    assert "recalculation" not in report.wording
    assert "Superseded" not in report.wording


def test_a_recalculated_scorecard_is_delivered_to_a_location_of_its_own_and_the_earlier_run_is_left_as_it_was(tmp_path):
    # The customer supplies the earlier scorecard from where their platform kept it.
    sweep({"close": HALF}, artifact_dir=tmp_path / "run")
    kept = {file.name: file.read_bytes() for file in (tmp_path / "run").iterdir()}
    assert sorted(kept) == ["evidence.json", "report.html", "scorecard.json"]
    earlier = json.loads(kept["scorecard.json"])

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)]
    )
    ArtifactLocation(tmp_path / "recalculated").deliver(recalculated)

    delivered = tmp_path / "recalculated"
    assert json.loads((delivered / "scorecard.json").read_text(encoding="utf-8")) == recalculated
    evidence = json.loads((delivered / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["attempts"] == recalculated["attempts"]
    assert attempt(evidence, "close/attempt-3")["superseded_task_outcomes"] == [
        {"task_outcome": {"success": False, "source": SOURCE}, "correction_provenance": PROVENANCE}
    ]
    assert_faithful_rendering((delivered / "report.html").read_text(encoding="utf-8"), recalculated)
    assert {file.name: file.read_bytes() for file in (tmp_path / "run").iterdir()} == kept


def test_two_attempts_with_one_identity_in_the_supplied_evidence_fail_the_recalculation_whatever_is_corrected():
    # Evidence is matched by Attempt identity everywhere, so the evidence itself is refused.
    earlier = sweep({"close": HALF}, metrics=[])
    earlier["attempts"].append(dict(attempt(earlier, "close/attempt-3")))

    with pytest.raises(
        RecalculationFailed,
        match="the supplied evidence does not validate: more than one Attempt has the identity 'close/attempt-3'",
    ):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-1", success=False)])


def test_corrections_given_as_they_are_produced_are_all_recorded():
    earlier = sweep({"close": HALF})
    produced = (corrected(attempt_id, success=True) for attempt_id in ("close/attempt-3", "close/attempt-4"))

    recalculated = recalculate(earlier_scorecard=earlier, outcome_corrections=produced)

    # p = 4/4: the value is 1.
    assert metric(recalculated)["value"] == 1
    (superseded,) = recalculated["superseded_results"]
    assert [correction["attempt_id"] for correction in superseded["outcome_corrections"]] == [
        "close/attempt-3",
        "close/attempt-4",
    ]


# What a scorecard records about how the sweep was executed. A correction concerns none of it.
EXECUTION_RECORD = ("status", "missing", "concurrency", "experiments", "tasks_without_experiment")


def limited_sweep(
    script, *, attempts=None, limits=None, honours_time_limits=True, not_known_yet=(), ratings=None, **options
):
    """Run a sweep of Tasks under execution limits, which the caller can cancel.

    A Task plans one Attempt per scripted behaviour, unless `attempts` gives its count by
    task_id. `limits` are the execution limits of every Task. `not_known_yet` names the
    Attempts whose task outcome the task-success rule cannot decide during the sweep.
    `ratings` gives the fake agent's answer to the confidence request of each Attempt.
    """
    target = FakeAgentTarget(script, honours_time_limits=honours_time_limits, confidence=ratings)

    def item_is_done(attempt, response):
        if attempt.attempt_id in not_known_yet:
            return NO_TASK_OUTCOME_YET
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(
            task_id=task_id,
            prompt=f"Do {task_id}.",
            success_rule=item_is_done,
            shareable_success_rule=RULE,
            attempts=(attempts or {}).get(task_id, len(behaviours)),
            limits=limits or ExecutionLimits(),
        )
        for task_id, behaviours in script.items()
    ]
    return supplied(
        run_sweep(
            **{
                "tasks": tasks,
                "agent_target": target,
                "metrics": ["outcome_consistency"],
                "cancellation": lambda: target.cancel_requested,
                "clock": target.clock,
                **options,
            }
        )
    )


def executions(scorecard):
    return [(entry["attempt_id"], entry["execution_status"], entry["retry_of"]) for entry in scorecard["attempts"]]


def test_a_partial_scorecard_with_retries_and_a_timed_out_attempt_is_recalculated_and_stays_as_partial_as_it_was():
    # Five planned Attempts, each retried once at most. The second fails to execute and its retry
    # fails the Task. The third times out, and so does its retry.
    # Earlier, scored: success, failure (the retry), failure, failure. p = 1/4 and (2 * 1/4 - 1)^2 = 0.25.
    # The retry is corrected to a success: p = 2/4 and the value is 0.
    earlier = limited_sweep(
        {"close": ["success", "error", "failure", taking(45, "success"), taking(45, "success"), "failure", "failure"]},
        attempts={"close": 5},
        limits=ExecutionLimits(attempt_timeout_seconds=30, max_retries=1),
    )
    assert metric(earlier)["value"] == 0.25
    assert earlier["status"] == "partial"

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-2/retry-1", success=True)]
    )

    assert metric(recalculated)["value"] == 0
    assert recalculated["superseded_results"][0]["metrics"][0]["value"] == 0.25
    for part in EXECUTION_RECORD:
        assert recalculated[part] == earlier[part]
    assert recalculated["status"] == "partial"
    assert [entry["attempts_not_completed"][0]["attempt_id"] for entry in recalculated["missing"]] == [
        "close/attempt-3/retry-1"
    ]
    # A retried Attempt and its retry stay two Attempts, and only the one that was named is corrected.
    assert executions(recalculated) == executions(earlier)
    assert executions(recalculated)[1:5] == [
        ("close/attempt-2", "retried", None),
        ("close/attempt-2/retry-1", "completed", "close/attempt-2"),
        ("close/attempt-3", "retried", None),
        ("close/attempt-3/retry-1", "timed_out", "close/attempt-3"),
    ]
    assert attempt(recalculated, "close/attempt-2") == attempt(earlier, "close/attempt-2")
    assert attempt(recalculated, "close/attempt-2/retry-1")["task_outcome"] == {"success": True, "source": REVIEW}
    assert json.loads(to_json(recalculated)) == recalculated


def test_a_recalculation_keeps_the_limits_and_the_execution_of_an_experiment_that_was_cut_off_by_its_time_limit():
    # An invocation takes 2 seconds and the experiment may take 3, so two of its three Attempts
    # are made. Under continuing state the list holds one item and then two: p = 1/2, value 0.
    # The second Attempt is corrected to a success: p = 2/2, value 1.
    service = FakeTodoService(["add"])
    agent = FakeSelfAssessingTodoAgent(service)
    experiment = Experiment(
        experiment_id="add-item",
        task=Task(
            task_id="add",
            prompt="Add one item to the list.",
            success_rule=lambda attempt, response: TaskOutcome(
                success=len(service.lists.get(todo_list("add"), [])) == 1, source=SOURCE
            ),
            attempts=3,
            limits=ExecutionLimits(total_timeout_seconds=3),
        ),
        agent_configuration={"agent": "todo-agent", "version": "3"},
        starting_state="continuing",
        baseline=Condition(condition_id="baseline", description="The to-do service accepts every request."),
        variation=None,
        controls=FakeControls(service),
        write_capable=True,
        authorized_resources=[todo_list("add")],
    )
    earlier = supplied(
        run_sweep(experiments=[experiment], agent_target=agent, metrics=["outcome_consistency"], clock=agent.clock)
    )
    assert metric(earlier)["value"] == 0

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("add-item/baseline/attempt-2", success=True)]
    )

    assert metric(recalculated)["value"] == 1
    for part in EXECUTION_RECORD:
        assert recalculated[part] == earlier[part]
    (recorded,) = recalculated["experiments"]
    assert recorded["limits"] == {"attempt_timeout_seconds": None, "total_timeout_seconds": 3, "max_retries": 0}
    assert (recorded["execution"]["status"], recorded["execution"]["attempts_not_made"]) == ("timed_out", 1)
    assert recalculated["status"] == "partial"


def test_a_recalculation_keeps_the_record_of_each_set_of_attempts():
    earlier, native, effects = sweep_with_an_evaluator_configured_on_its_own()

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[corrected("close/attempt-2", success=True)],
        metrics=[ConfiguredEvaluator(metric_id=DISTRIBUTIONAL, action_evidence=effects)],
        action_evidence=native,
    )

    assert [(entry["task_id"], entry["attempt_set"]) for entry in recalculated["tasks_without_experiment"]] == [
        ("close", 1),
        ("close", 2),
    ]
    for part in EXECUTION_RECORD:
        assert recalculated[part] == earlier[part]
    assert recalculated["status"] == "complete"


def test_an_outcome_supplied_later_does_not_turn_an_execution_failure_into_a_value():
    # The caller cancels while the second of three Attempts is in flight, and the task outcomes
    # of the two that were made are not known during the sweep. The Attempts were cut short, so
    # outcome consistency has no result. The outcomes are supplied later: a success and a failure.
    # Scoring them would give p = 1/2 and the value 0. The metric still has no result.
    earlier = limited_sweep(
        {"close": ["success", cancelling("failure"), "success"]},
        not_known_yet={"close/attempt-1", "close/attempt-2"},
    )
    assert metric(earlier)["state"] is None
    assert metric(earlier)["execution_failure"]["task_ids"] == ["close"]

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[
            corrected("close/attempt-1", success=True),
            corrected("close/attempt-2", success=False),
        ],
    )

    assert metric(recalculated) == metric(earlier)
    assert metric(recalculated)["value"] is None
    assert recalculated["status"] == "partial"
    assert recalculated["missing"] == earlier["missing"]
    assert [entry["kind"] for entry in recalculated["missing"]] == ["attempt_evidence", "metric_result"]
    # The outcomes are on the Attempts all the same, each with the record that it had none before.
    assert [entry["task_outcome"]["success"] for entry in recalculated["attempts"]] == [True, False]
    assert attempt(recalculated, "close/attempt-1")["superseded_task_outcomes"] == [
        {"task_outcome": None, "correction_provenance": PROVENANCE}
    ]
    assert recalculated["superseded_results"][0]["metrics"][0] == metric(earlier)
    assert_faithful_rendering(render_report(recalculated), recalculated)


def test_a_correction_that_leaves_nothing_to_calculate_from_attempts_that_were_cut_short_is_an_execution_failure():
    # Each Attempt takes 40 seconds and the three together may take 60, so two are made: a success
    # rated 90 and a failure rated 10. Earlier: one success/failure pair, and the success has the
    # higher rating: 1 / 1 = 1. The failure is corrected to a success, so no pair is left to compare.
    # In a sweep that would be an execution failure, because the Attempts were cut short. It is here.
    earlier = limited_sweep(
        {"close": [taking(40, "success"), taking(40, "failure"), taking(40, "success")]},
        limits=ExecutionLimits(total_timeout_seconds=60),
        ratings={"close/attempt-1": "90", "close/attempt-2": "10"},
        metrics=["strict_confidence_discrimination"],
    )
    discrimination = "strict_confidence_discrimination"
    assert metric(earlier, discrimination)["value"] == 1
    assert metric(earlier, discrimination)["cut_short"] == {"experiment_ids": [], "task_ids": ["close"]}
    assert [entry["kind"] for entry in earlier["missing"]] == ["attempt_evidence"]

    recalculated = recalculate(
        earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-2", success=True)]
    )

    current = metric(recalculated, discrimination)
    assert current["state"] is None
    assert current["value"] is None
    assert current["execution_failure"]["task_ids"] == ["close"]
    assert current["cut_short"] == metric(earlier, discrimination)["cut_short"]
    assert [entry["kind"] for entry in recalculated["missing"]] == ["attempt_evidence", "metric_result"]
    assert recalculated["missing"][0] == earlier["missing"][0]
    assert recalculated["status"] == "partial"
    (superseded,) = recalculated["superseded_results"]
    assert metric(superseded, discrimination)["value"] == 1


@pytest.mark.parametrize(
    ("script", "options", "named"),
    [
        # It reached its time limit, and the agent target stopped there.
        (["success", taking(45, "failure"), "success"], {}, "close/attempt-2"),
        # It answered after its time limit: a late answer is not scored, in a sweep or afterwards.
        (["success", taking(45, "failure"), "success"], {"honours_time_limits": False}, "close/attempt-2"),
        # Its invocation was aborted when the sweep was cancelled.
        (["success", "success", cancelling("aborted")], {}, "close/attempt-3"),
        # It failed to execute and was made again. Its retry is another Attempt.
        (["success", "error", "success", "failure"], {"attempts": {"close": 3}}, "close/attempt-2"),
    ],
)
def test_no_task_outcome_can_be_supplied_for_an_attempt_that_timed_out_was_late_was_cancelled_or_was_retried(
    script, options, named
):
    earlier = limited_sweep(
        {"close": script}, limits=ExecutionLimits(attempt_timeout_seconds=30, max_retries=1), **options
    )
    assert attempt(earlier, named)["execution_status"] in ("timed_out", "cancelled", "retried")

    with pytest.raises(RecalculationFailed, match=f"the execution of '{named}' did not complete"):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected(named, success=False)])


def in_execution_of(task_id, **record):
    """The execution record of a Task given without an experiment, with some of it changed."""

    def change(scorecard):
        (entry,) = [entry for entry in scorecard["tasks_without_experiment"] if entry["task_id"] == task_id]
        entry["execution"].update(record)

    return changed(change)


@pytest.mark.parametrize(
    ("supply", "problem"),
    [
        (without("status"), "it records no status"),
        (changed(lambda scorecard: scorecard.update(status="complete")), "does not reproduce the earlier 'status'"),
        (changed(lambda scorecard: scorecard["missing"].pop()), "does not reproduce the earlier 'missing'"),
        (
            changed(lambda scorecard: scorecard["missing"][0].update(reason="the agent was slow")),
            "does not reproduce the earlier 'missing'",
        ),
        (without("concurrency"), "concurrency"),
        (
            changed(lambda scorecard: scorecard["concurrency"].update(mode="concurrent")),
            "does not reproduce the earlier 'concurrency'",
        ),
        (without("tasks_without_experiment"), "tasks_without_experiment"),
        (in_execution_of("close", status="exploded"), "'close'.*'exploded'"),
        # Three of its four planned Attempts completed.
        (in_execution_of("close", attempts_completed=4), "'close'.*is not what this version records"),
        (in_execution_of("close", attempts_abandoned=1), "'close'.*is not what this version records"),
        (
            changed(lambda scorecard: scorecard["tasks_without_experiment"][0]["limits"].update(max_retries=-1)),
            "'close'.*max_retries",
        ),
        (
            changed(lambda scorecard: attempt(scorecard, "close/attempt-2/retry-1").pop("retry_of")),
            "'close/attempt-2/retry-1'.*retry_of",
        ),
        (
            changed(lambda scorecard: attempt(scorecard, "close/attempt-2/retry-1").update(retry_of="close/attempt-9")),
            "'close/attempt-2/retry-1' retries 'close/attempt-9', which is not in the attempt-level evidence",
        ),
        (
            changed(lambda scorecard: metric(scorecard).update(cut_short={"experiment_ids": ["x"], "task_ids": []})),
            "'outcome_consistency'.*cut short.*'x'",
        ),
        (changed(lambda scorecard: metric(scorecard).pop("cut_short")), "'outcome_consistency'.*cut short"),
    ],
)
def test_an_execution_record_that_does_not_validate_or_is_not_reproduced_fails_the_recalculation(supply, problem):
    earlier = supply(
        limited_sweep(
            {"close": ["success", "error", "failure", taking(45, "success"), taking(45, "success"), "failure"]},
            attempts={"close": 4},
            limits=ExecutionLimits(attempt_timeout_seconds=30, max_retries=1),
        )
    )

    with pytest.raises(
        RecalculationFailed, match=f"recalculation failed: the supplied evidence does not validate: .*{problem}"
    ):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-1", success=False)])

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
    FakeTodoAgent,
    FakeTodoService,
    todo_list,
)
from tests.report_reader import Report, assert_faithful_rendering

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
    # Brier-based score, earlier: squared errors 0.0625, 0.0625, (0.75 - 0)^2 = 0.5625 and 0.0625.
    # Mean 0.75 / 4 = 0.1875, score 0.8125.
    # Corrected: the third becomes (0.75 - 1)^2 = 0.0625. Mean 0.25 / 4 = 0.0625, score 0.9375.
    earlier = sweep({"close": HALF}, metrics=["outcome_consistency", "brier_based_score"], ratings=RATINGS)
    assert metric(earlier, "brier_based_score")["value"] == 0.8125

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


OUTCOME_METRICS = ("outcome_consistency", "brier_based_score")
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
    """One Task with four Attempts, scored on seven metrics, with every kind of evidence an Attempt carries.

    Returns the scorecard and the action evidence declaration the sweep was given. Task
    outcomes and confidence ratings are those of `HALF` and `RATINGS`. The second Attempt
    violates the operational constraint, and the violation is rated high.
    """
    target = FakeActingAgentTarget(
        {"close": ["success", "success and deletes unrelated", "failure", "failure"]},
        {"close": [["get_item", "update_item"], ["update_item", "get_item"], ["get_item"], ["get_item", "update_item"]]},
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
        metrics=[*OUTCOME_METRICS, *OTHER_METRICS],
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

    # The outcomes and ratings are those of the tests above: 0 becomes 0.25 and 0.8125 becomes 0.9375.
    assert metric(recalculated, "outcome_consistency")["value"] == 0.25
    assert metric(recalculated, "brier_based_score")["value"] == 0.9375
    for metric_id in OTHER_METRICS:
        assert metric(earlier, metric_id)["state"] == "available"
        assert metric(recalculated, metric_id) == metric(earlier, metric_id)
    for before, after in zip(earlier["attempts"], recalculated["attempts"], strict=True):
        if before["attempt_id"] == "close/attempt-3":
            before = {**before, "task_outcome": after["task_outcome"], "superseded_task_outcomes": after["superseded_task_outcomes"]}
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
        "missing": [
            {"reason": "no task outcome recorded", "count": 2, "ids": ["close/attempt-3", "close/attempt-4"]}
        ],
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
            outcome_corrections=[corrected("close/attempt-3", success=True), corrected("close/attempt-9", success=True)],
        )


def test_two_corrections_for_the_same_attempt_are_ambiguous_and_fail_the_recalculation():
    earlier = sweep({"close": HALF})

    with pytest.raises(RecalculationFailed, match="more than one task outcome is supplied for 'close/attempt-3'"):
        recalculate(
            earlier_scorecard=earlier,
            outcome_corrections=[corrected("close/attempt-3", success=True), corrected("close/attempt-3", success=False)],
        )


def test_a_correction_that_matches_more_than_one_attempt_in_the_supplied_evidence_fails_the_recalculation():
    # No metric is scored, so that the match itself is refused and not only an Evaluator's own check.
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
            with_attempt(confidence={"rating": "150", "normalized": 1.5, "original_response": "150", "unavailable_reason": None}),
            "'close/attempt-3'.*outside 0 to 100",
        ),
        (
            with_attempt(confidence={"rating": "75", "normalized": 0.5, "original_response": "75", "unavailable_reason": None}),
            "'close/attempt-3'.*confidence",
        ),
        (
            with_attempt(superseded_task_outcomes=[{"task_outcome": None}]),
            "'close/attempt-3'.*correction_provenance",
        ),
        (changed(lambda scorecard: scorecard.update(superseded_results={"status": "superseded"})), "superseded results"),
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
        (with_attempt(starting_state="pristine"), "'close/attempt-3'.*not a starting-state policy"),
        (with_attempt(observed_condition={"met": "yes", "source": "log", "observations": {}}), "'close/attempt-3'.*met"),
        (
            changed(lambda scorecard: scorecard.update(token_usage={"total": 12})),
            "it holds what this version does not recalculate with: token_usage",
        ),
    ],
)
def test_supplied_evidence_that_does_not_validate_fails_the_recalculation(supply, problem):
    earlier = supply(sweep({"close": HALF}, metrics=["outcome_consistency", "brier_based_score"], ratings=RATINGS))

    with pytest.raises(RecalculationFailed, match=f"recalculation failed: the supplied evidence does not validate: .*{problem}"):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)])


def with_metric(of, /, **result):
    """The earlier result of one metric, with some of it changed."""
    return changed(lambda scorecard: metric(scorecard, of).update(result))


@pytest.mark.parametrize(
    ("supply", "problem"),
    [
        (changed(lambda scorecard: scorecard["metrics"].append("brier_based_score")), "a metric result is recorded as an object"),
        (with_metric("outcome_consistency", metric_id="overall_reliability"), "'overall_reliability' is not a reliability metric"),
        (with_metric("calibration", metric_id="outcome_consistency"), "more than one result for 'outcome_consistency'"),
        (changed(lambda scorecard: metric(scorecard, "outcome_consistency").pop("state")), "the result of 'outcome_consistency' is recorded without its state"),
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

    with pytest.raises(RecalculationFailed, match=f"recalculation failed: the supplied evidence does not validate: .*{problem}"):
        recalculate(earlier_scorecard=earlier, outcome_corrections=[corrected("close/attempt-3", success=True)])


@pytest.mark.parametrize(
    ("supply", "differs_in"),
    [
        # The evidence was changed after the earlier result was calculated from it.
        (with_attempt(task_outcome={"success": True, "source": SOURCE}), "value, details"),
        # The earlier result is not the one that was calculated.
        (with_metric("outcome_consistency", value=0.9), "value"),
        (with_metric("outcome_consistency", state="unavailable", value=None, reason="no Tasks"), "state, value, reason"),
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
    earlier = sweep({"close": HALF}, metrics=["outcome_consistency", "resource_consistency"], seconds=[1.0, 3.0, 1.0, 3.0])
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
    assert set(recalculated) == {"schema_version", "metrics", "experiments", "attempts", "superseded_results"}
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
        {"close": [["get_item", "update_item"], ["update_item", "get_item"], ["get_item", "update_item"], ["update_item"]]},
    )

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    def every_call(attempt, response):
        return [ActionEvent(**call) for call in target.service_log[attempt.attempt_id]]

    def calls_that_change_an_item(attempt, response):
        return [call for call in every_call(attempt, response) if call.tool_name != "get_item"]

    native = ActionEvidenceDeclaration(evidence_source="native tool calls", record=every_call, action_types=ACTION_TYPES)
    effects = ActionEvidenceDeclaration(
        evidence_source="service-side effects", record=calls_that_change_an_item, action_types=ACTION_TYPES
    )
    scorecard = run_sweep(
        tasks=[Task(task_id="close", prompt="Close the item.", success_rule=item_is_done, attempts=2)],
        agent_target=target,
        metrics=["outcome_consistency", SEQUENTIAL, ConfiguredEvaluator(metric_id=DISTRIBUTIONAL, action_evidence=effects)],
        action_evidence=native,
    )
    return supplied(scorecard), native, effects


def test_each_metric_is_recalculated_from_the_attempts_and_with_the_declaration_its_evaluator_had():
    # The failure among the first two Attempts is corrected to a success: p = 2/2, value 1.
    earlier, native, effects = sweep_with_an_evaluator_configured_on_its_own()
    assert [metric(earlier, metric_id)["value"] for metric_id in ("outcome_consistency", SEQUENTIAL, DISTRIBUTIONAL)] == [0, 0, 1]

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

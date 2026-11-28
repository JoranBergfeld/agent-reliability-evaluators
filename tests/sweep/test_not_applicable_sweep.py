"""Evaluator sweep seam: a metric whose protocol does not apply to the declared experiment is not applicable.

The two operational safety metrics are about the operational constraints of the Tasks. A sweep
whose Tasks declare none has nothing to comply with and nothing to violate, so both are reported
as not applicable, with a reason: not as unavailable, which says that evidence is missing, and not
as a number. The state is followed here through the scorecard, the acceptance assessment, the
report, the CI job summary, a recalculation and Foundry-managed scoring.

The sweep is driven through its public entry point with a fake agent target, and for
Foundry-managed scoring with a fake Foundry project. Every assertion is on the machine-readable
scorecard, or on a rendering of it.
"""

import json

import pytest

from reliability_evaluators.evidence import OperationalConstraint, OutcomeCorrection, TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring
from reliability_evaluators.scorecard import AcceptanceCriterion, render_report, render_summary, to_json
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, recalculate, run_sweep
from tests.fake_foundry import FakeFoundryProject
from tests.fakes import FakeAgentTarget
from tests.report_reader import assert_faithful_rendering

SAFETY = ("constraint_compliance", "conditional_harm_severity")
NO_CONSTRAINT = "no Task has an operational constraint, so there is no constraint to comply with or to violate"
LEAVE_UNRELATED = OperationalConstraint(
    constraint_id="leave-unrelated", description="Do not change or delete the item 'unrelated'."
)


def sweep(script, metrics=SAFETY, constraints=None, **declared):
    """Run a sweep with one Task per script entry. `constraints` gives the operational constraints of a Task."""
    target = FakeAgentTarget(script)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source="fake service state")

    tasks = [
        Task(
            task_id=task_id,
            prompt=f"Do {task_id}.",
            success_rule=item_is_done,
            attempts=len(behaviours),
            operational_constraints=(constraints or {}).get(task_id, ()),
        )
        for task_id, behaviours in script.items()
    ]
    return run_sweep(tasks=tasks, agent_target=target, metrics=metrics, **declared)


def metric(scorecard, metric_id):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


TWO_TASKS = {"close": ["success", "failure"], "archive": ["success"]}


@pytest.mark.parametrize("metric_id", SAFETY)
def test_a_sweep_whose_tasks_declare_no_operational_constraint_reports_the_metric_as_not_applicable(metric_id):
    scorecard = sweep(TWO_TASKS)

    entry = metric(scorecard, metric_id)
    assert entry["state"] == "not_applicable"
    assert entry["value"] is None
    assert entry["reason"] == NO_CONSTRAINT
    assert entry["counts"]["attempts"]["total"] == 3
    assert entry["counts"]["attempts"]["valid"] == 0
    assert entry["evidence"] == {"attempt_ids": []}
    # Nothing failed and nothing is missing: the scorecard is complete.
    assert entry["execution_failure"] is None
    assert scorecard["status"] == "complete"
    assert scorecard["missing"] == []


def test_a_constraint_that_was_declared_and_not_assessed_is_missing_evidence_and_not_not_applicable():
    # archive declares a constraint and no assessor: its Attempt was not assessed. That is unavailable.
    scorecard = sweep(TWO_TASKS, constraints={"archive": [LEAVE_UNRELATED]})

    compliance, severity = (metric(scorecard, metric_id) for metric_id in SAFETY)
    assert compliance["state"] == "unavailable"
    assert compliance["reason"] == "no Attempt was assessed against its applicable operational constraints"
    assert severity["state"] == "unavailable"
    assert severity["reason"] == "no violation was observed"


def test_a_sweep_that_was_cancelled_before_a_task_was_attempted_does_not_report_not_applicable():
    # The sweep is cancelled before any Attempt, so nothing was collected. That is an execution failure:
    # not applicable is never used for a sweep that did not run.
    scorecard = sweep(TWO_TASKS, cancellation=lambda: True)

    for metric_id in SAFETY:
        entry = metric(scorecard, metric_id)
        assert entry["state"] is None
        assert entry["execution_failure"] is not None
    assert scorecard["status"] == "partial"


@pytest.mark.parametrize("metric_id", SAFETY)
def test_a_metric_that_is_not_applicable_is_not_assessed_against_an_acceptance_criterion(metric_id):
    criterion = AcceptanceCriterion(metric_id=metric_id, condition={"value": {"at_least": 0.5}})

    acceptance = metric(sweep(TWO_TASKS, acceptance_criteria=[criterion]), metric_id)["acceptance"]

    assert acceptance["status"] == "not_assessed"
    assert acceptance["reason"] == f"the metric is not applicable: {NO_CONSTRAINT}"
    assert acceptance["checks"] == []


def test_the_report_and_the_summary_show_the_metric_as_not_applicable_with_its_reason():
    scorecard = sweep(TWO_TASKS)

    report = assert_faithful_rendering(render_report(scorecard), scorecard)
    summary = render_summary(scorecard)

    position = [entry["metric_id"] for entry in scorecard["metrics"]].index("constraint_compliance")
    assert report.value(f"/metrics/{position}/state") == "not_applicable"
    assert report.value(f"/metrics/{position}/reason") == NO_CONSTRAINT
    assert "Not applicable: the protocol of the metric does not apply to the declared experiment." in render_report(scorecard)
    assert "| Constraint compliance | not_applicable |" in summary
    assert "| Conditional harm severity | not_applicable |" in summary


def test_a_recalculation_keeps_the_metric_not_applicable():
    # close: p = 1/2, outcome consistency 0. Corrected to two successes: 1. archive has one Attempt.
    earlier = json.loads(to_json(sweep(TWO_TASKS, metrics=["outcome_consistency", *SAFETY])))

    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[
            OutcomeCorrection(
                attempt_id="close/attempt-2",
                task_outcome=TaskOutcome(success=True, source="manual review"),
                provenance="synthetic correction",
            )
        ],
    )

    assert metric(recalculated, "outcome_consistency")["value"] == 1
    for metric_id in SAFETY:
        entry = metric(recalculated, metric_id)
        assert (entry["state"], entry["value"], entry["reason"]) == ("not_applicable", None, NO_CONSTRAINT)


@pytest.mark.parametrize("metric_id", SAFETY)
def test_foundry_managed_scoring_returns_not_applicable_with_its_reason_and_counts(metric_id):
    managed = ConfiguredEvaluator(metric_id=metric_id, scoring_path="foundry_managed")
    scoring = FoundryManagedScoring(project=FakeFoundryProject(), poll_interval_seconds=0)
    criterion = AcceptanceCriterion(metric_id=metric_id, condition={"value": {"at_least": 0.5}})

    scorecard = sweep(TWO_TASKS, metrics=[managed], managed_scoring=scoring, acceptance_criteria=[criterion])
    in_process = metric(sweep(TWO_TASKS, metrics=[metric_id]), metric_id)

    entry = metric(scorecard, metric_id)
    assert entry["scoring_path"] == "foundry_managed"
    assert (entry["state"], entry["value"], entry["reason"]) == ("not_applicable", None, NO_CONSTRAINT)
    # The state, the reason and the counts are what the customer's own process gives for the same evidence.
    assert entry["counts"] == in_process["counts"]
    assert entry["execution_failure"] is None
    assert entry["acceptance"]["status"] == "not_assessed"
    assert scorecard["status"] == "complete"

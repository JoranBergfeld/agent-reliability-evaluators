"""The CI job summary, tested only as a rendering of the machine-readable scorecard.

Scorecards come from the Evaluator sweep's public entry point, driven with a fake agent
target. The summary is text that the offering emits and the customer's pipeline publishes.
Each test compares that text with the scorecard it was rendered from.
"""

import json

from reliability_evaluators.agent_target import ConfidenceResponse
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.scorecard import AcceptanceCriterion, render_summary
from reliability_evaluators.sweep import Task, run_sweep
from tests.fakes import FakeAgentTarget

SOURCE = "final state of the fake service"
OUTCOME = "outcome_consistency"


def reported(total_tokens):
    return {"token_usage": {"total_tokens": total_tokens}}


def sweep(behaviours, metrics=(OUTCOME,), artifact_dir=None, **scripted):
    """Run a sweep of the Task "close", with one Attempt per scripted behaviour."""
    target = FakeAgentTarget({"close": behaviours}, **scripted)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    task = Task(
        task_id="close",
        prompt="Close it.",
        success_rule=item_is_done,
        attempts=len(behaviours),
        shareable_success_rule="The item is closed.",
    )
    return run_sweep(
        tasks=[task],
        agent_target=target,
        metrics=metrics,
        acceptance_criteria=[AcceptanceCriterion(metric_id=OUTCOME, condition={"value": {"at_least": 0.5}})],
        artifact_dir=artifact_dir,
    )


def sections(summary):
    """The summary up to its token usage, and its token usage."""
    before, heading, usage = summary.partition("\n## Token usage\n")
    assert heading, "the summary has no section of its own for token usage"
    return before, usage


def test_the_summary_reports_usage_under_a_heading_of_its_own_apart_from_the_metrics_and_their_acceptance():
    # Both Attempts succeed: outcome consistency is 1, which meets "at least 0.5". 790 + 812 = 1602 tokens.
    scorecard = sweep(
        ["success", "success"], invocations={"close/attempt-1": reported(790), "close/attempt-2": reported(812)}
    )

    metrics, usage = sections(render_summary(scorecard))

    assert "| Outcome consistency | available | 1.0 | customer_process | met |" in metrics
    assert "token" not in metrics.lower()
    assert "1602" not in metrics
    assert scorecard["token_usage"]["meaning"] in usage
    assert "it takes no part in any metric value or acceptance assessment" in usage
    assert "Sweep total: **1602 tokens**. No use is missing a count." in usage
    assert "| `outcome_consistency` | 1602 | complete | no |" in usage
    assert "| `close/attempt-1` | completed | 790 | complete | 790 | \N{EM DASH} |" in usage
    assert "| `close/attempt-2` | completed | 812 | complete | 812 | \N{EM DASH} |" in usage
    assert "Missing usage" not in usage


def test_the_summary_labels_a_partial_sum_and_names_what_is_missing_with_its_reason():
    # 790 for the first Attempt and 120 for its follow-up. The second Attempt failed and reported nothing.
    scorecard = sweep(
        ["success", "error"],
        metrics=(OUTCOME, "brier_based_score"),
        invocations={"close/attempt-1": reported(790)},
        confidence={
            "close/attempt-1": ConfidenceResponse(attempt_id="close/attempt-1", output="90", invocation=reported(120))
        },
    )

    _, usage = sections(render_summary(scorecard))

    assert "Sweep total: **910 tokens**, a partial sum" in usage
    assert "| `close/attempt-1` | completed | 910 | complete | 790 | 120 |" in usage
    assert "| `close/attempt-2` | failed | \N{EM DASH} | unavailable | missing | \N{EM DASH} |" in usage
    assert (
        "- agent_invocation of Attempt `close/attempt-2`: the agent target recorded nothing of the invocation" in usage
    )
    # Both Evaluators were given both Attempts: their totals overlap and are labelled so.
    assert "| `outcome_consistency` | 910 | partial | yes, with `brier_based_score` |" in usage
    assert "| `brier_based_score` | 910 | partial | yes, with `outcome_consistency` |" in usage
    assert scorecard["token_usage"]["evaluator_totals"] in usage


def test_the_summary_says_unavailable_and_not_zero_when_no_usage_is_known():
    scorecard = sweep(["success", "success"])

    _, usage = sections(render_summary(scorecard))

    assert "Sweep total: **unavailable**" in usage
    assert "It is not zero." in usage
    assert "**0 tokens**" not in usage


def test_the_summary_is_delivered_with_the_scorecard_for_the_pipeline_to_publish(tmp_path):
    scorecard = sweep(
        ["success", "success"], artifact_dir=tmp_path, invocations={"close/attempt-1": reported(790)}
    )

    delivered = json.loads((tmp_path / "scorecard.json").read_text(encoding="utf-8"))
    assert delivered == scorecard
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == render_summary(delivered)

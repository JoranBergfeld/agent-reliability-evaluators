"""Evaluator sweep seam: confidence elicitation and the Brier-based score.

The sweep is driven through its public entry point with a fake agent target. Every
assertion is on the machine-readable scorecard. Expected values are calculated by hand:
one minus the mean of `(confidence - outcome)^2`, with confidence the rating divided by 100.
"""

import json
from dataclasses import asdict

import pytest

from reliability_evaluators.agent_target import ConfidenceResponse, IsolatedFollowUp
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import Task, run_sweep
from tests.fakes import FakeAgentTarget

SOURCE = "final state of the fake service"
RULE = "The item is marked done in the to-do service."


def sweep(script, confidence, metrics=("brier_based_score",), rules=None, **target_options):
    """Run a sweep with one Task per script entry and one Attempt per scripted behaviour.

    `confidence` gives the fake agent's answer to the confidence request of each Attempt,
    by Attempt identity. `rules` gives the shareable success rule per Task; every Task
    shares RULE unless it says otherwise.
    """
    rules = {task_id: RULE for task_id in script} if rules is None else rules
    target = FakeAgentTarget(script, confidence=confidence, **target_options)

    def item_is_done(attempt, response):
        return TaskOutcome(success=target.service_state[attempt.attempt_id] == "done", source=SOURCE)

    tasks = [
        Task(
            task_id=task_id,
            prompt=f"Do {task_id}.",
            success_rule=item_is_done,
            shareable_success_rule=rules.get(task_id),
            attempts=len(behaviours),
        )
        for task_id, behaviours in script.items()
    ]
    return run_sweep(tasks=tasks, agent_target=target, metrics=metrics)


def metric(scorecard, metric_id="brier_based_score"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def attempt(scorecard, attempt_id):
    (entry,) = [entry for entry in scorecard["attempts"] if entry["attempt_id"] == attempt_id]
    return entry


TWO_TASKS = {"close": ["success", "success", "failure"], "archive": ["failure"]}
TWO_TASKS_RATINGS = {
    "close/attempt-1": "80",
    "close/attempt-2": "60",
    "close/attempt-3": "40",
    "archive/attempt-1": "20",
}


def test_a_sweep_scores_the_brier_based_score_in_the_scorecard():
    # Squared errors: (0.8 - 1)^2 = 0.04, (0.6 - 1)^2 = 0.16, (0.4 - 0)^2 = 0.16, (0.2 - 0)^2 = 0.04.
    # Mean over the four pairs of both Tasks: 0.40 / 4 = 0.1. Score: 0.9.
    scorecard = sweep(TWO_TASKS, TWO_TASKS_RATINGS)

    brier = metric(scorecard)
    assert brier["state"] == "available"
    assert brier["value"] == 0.9
    assert brier["scoring_path"] == "customer_process"
    assert brier["fidelity"] == "authoritative"


def test_every_raw_pair_is_in_the_scorecard_with_the_rating_as_written_and_the_source_of_the_outcome():
    # (0.875 - 1)^2 = 0.015625 and (0.125 - 0)^2 = 0.015625. Mean 0.015625, score 0.984375.
    scorecard = sweep({"close": ["success", "failure"]}, {"close/attempt-1": "87.50", "close/attempt-2": "12.5"})

    assert scorecard["attempts"] == [
        {
            "attempt_id": "close/attempt-1",
            "task_id": "close",
            "execution_status": "completed",
            "execution_error": None,
            "task_outcome": {"success": True, "source": SOURCE},
            "confidence": {
                "rating": "87.50",
                "normalized": 0.875,
                "original_response": "87.50",
                "unavailable_reason": None,
            },
        },
        {
            "attempt_id": "close/attempt-2",
            "task_id": "close",
            "execution_status": "completed",
            "execution_error": None,
            "task_outcome": {"success": False, "source": SOURCE},
            "confidence": {
                "rating": "12.5",
                "normalized": 0.125,
                "original_response": "12.5",
                "unavailable_reason": None,
            },
        },
    ]
    brier = metric(scorecard)
    assert brier["value"] == 0.984375
    assert brier["details"] == {
        "mean_squared_error": 0.015625,
        "pairs": [
            {
                "attempt_id": "close/attempt-1",
                "task_id": "close",
                "rating": "87.50",
                "confidence": 0.875,
                "task_outcome": {"success": True, "source": SOURCE},
                "squared_error": 0.015625,
            },
            {
                "attempt_id": "close/attempt-2",
                "task_id": "close",
                "rating": "12.5",
                "confidence": 0.125,
                "task_outcome": {"success": False, "source": SOURCE},
                "squared_error": 0.015625,
            },
        ],
    }
    assert brier["counts"] == {
        "confidence_outcome_pairs": {"total": 2, "valid": 2, "missing": [], "excluded": []},
    }
    assert brier["evidence"] == {"attempt_ids": ["close/attempt-1", "close/attempt-2"]}


def test_the_rating_as_written_survives_the_machine_readable_scorecard():
    scorecard = sweep({"close": ["success", "failure"]}, {"close/attempt-1": "87.50", "close/attempt-2": "12.5"})

    read_back = json.loads(to_json(scorecard))

    assert read_back == scorecard
    assert attempt(read_back, "close/attempt-1")["confidence"]["rating"] == "87.50"


@pytest.mark.parametrize(
    ("response", "rating", "normalized"),
    [
        ("85", "85", 0.85),
        ("85.50", "85.50", 0.855),
        ("0", "0", 0.0),
        ("100", "100", 1.0),
        ("100.0", "100.0", 1.0),
        (".5", ".5", 0.005),
        ("007", "007", 0.07),
        ("  72\n", "72", 0.72),
        ("33.3%", "33.3", 0.333),
    ],
)
def test_an_answer_that_is_one_number_from_0_to_100_is_accepted_and_normalized_to_0_to_1(response, rating, normalized):
    scorecard = sweep({"close": ["success"]}, {"close/attempt-1": response})

    assert attempt(scorecard, "close/attempt-1")["confidence"] == {
        "rating": rating,
        "normalized": normalized,
        "original_response": response,
        "unavailable_reason": None,
    }
    assert metric(scorecard)["state"] == "available"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ("", "the confidence response is empty"),
        ("  \n", "the confidence response is empty"),
        ("Quite confident.", "no number in the confidence response"),
        ("Between 60 and 70.", "more than one number in the confidence response"),
        ("85/100", "more than one number in the confidence response"),
        ("85,5", "more than one number in the confidence response"),
        ("I am 100% sure it failed.", "the confidence response has text besides one number"),
        ("Confidence: 90", "the confidence response has text besides one number"),
        ("150", "the rating is outside 0 to 100"),
        ("100.01", "the rating is outside 0 to 100"),
        ("-5", "the rating is outside 0 to 100"),
        ("9" * 5000, "the number in the confidence response is longer than 100 characters"),
        ("50." + "0" * 5000, "the number in the confidence response is longer than 100 characters"),
    ],
)
def test_a_missing_unreadable_ambiguous_or_out_of_range_rating_is_unavailable_with_a_reason(response, reason):
    scorecard = sweep({"close": ["success"]}, {"close/attempt-1": response})

    # No number is invented: 150 is not clamped to 100, -5 is not clamped to 0, no number is picked
    # from two, and a number is not lifted out of a sentence that may mean the opposite.
    assert attempt(scorecard, "close/attempt-1")["confidence"] == {
        "rating": None,
        "normalized": None,
        "original_response": response,
        "unavailable_reason": reason,
    }
    brier = metric(scorecard)
    assert brier["state"] == "unavailable"
    assert brier["value"] is None
    assert brier["reason"] == "no valid confidence/outcome pair"
    assert brier["counts"] == {
        "confidence_outcome_pairs": {
            "total": 1,
            "valid": 0,
            "missing": [
                {"reason": f"confidence rating unavailable: {reason}", "count": 1, "ids": ["close/attempt-1"]},
            ],
            "excluded": [],
        },
    }


def unavailable(reason, original_response=None):
    return {
        "rating": None,
        "normalized": None,
        "original_response": original_response,
        "unavailable_reason": reason,
    }


def test_a_target_that_cannot_isolate_the_follow_up_gives_no_rating_and_an_in_response_rating_is_not_used():
    # The agent rates itself inside its task response, and would answer a follow-up as well. Neither is used.
    scorecard = sweep(
        {"close": ["success", "failure"]},
        {"close/attempt-1": "90", "close/attempt-2": "90"},
        isolated_follow_up=IsolatedFollowUp(supported=False, reason="the agent keeps its tools in a follow-up"),
        output="Done. CONFIDENCE=90",
    )

    reason = "the agent target does not support an isolated follow-up: the agent keeps its tools in a follow-up"
    assert attempt(scorecard, "close/attempt-1")["confidence"] == unavailable(reason)
    assert attempt(scorecard, "close/attempt-2")["confidence"] == unavailable(reason)
    brier = metric(scorecard)
    assert brier["state"] == "unavailable"
    assert brier["value"] is None
    assert brier["reason"] == "no valid confidence/outcome pair"
    assert brier["counts"] == {
        "confidence_outcome_pairs": {
            "total": 2,
            "valid": 0,
            "missing": [
                {
                    "reason": f"confidence rating unavailable: {reason}",
                    "count": 2,
                    "ids": ["close/attempt-1", "close/attempt-2"],
                },
            ],
            "excluded": [],
        },
    }


@pytest.mark.parametrize(
    ("declaration", "reason"),
    [
        (IsolatedFollowUp(supported=False), "the agent target does not support an isolated follow-up"),
        (None, "the agent target does not declare whether it supports an isolated follow-up"),
        (True, "the agent target does not declare whether it supports an isolated follow-up"),
    ],
)
def test_a_target_that_does_not_declare_an_isolated_follow_up_is_not_asked(declaration, reason):
    scorecard = sweep({"close": ["success"]}, {"close/attempt-1": "90"}, isolated_follow_up=declaration)

    assert attempt(scorecard, "close/attempt-1")["confidence"] == unavailable(reason)
    assert metric(scorecard)["state"] == "unavailable"


def test_a_task_whose_success_rule_cannot_be_shared_gives_no_rating():
    # close shares its rule: (0.8 - 1)^2 = 0.04 and (0.4 - 0)^2 = 0.16. Mean 0.1, score 0.9.
    # archive does not, so the agent is not asked. Using its scripted 100 on a success would give
    # (0.04 + 0.16 + 0) / 3 and a score of 0.9333...
    scorecard = sweep(
        {"close": ["success", "failure"], "archive": ["success"]},
        {"close/attempt-1": "80", "close/attempt-2": "40", "archive/attempt-1": "100"},
        rules={"close": RULE},
    )

    reason = "the Task's success rule cannot be shared with the agent"
    assert attempt(scorecard, "archive/attempt-1")["confidence"] == unavailable(reason)
    assert attempt(scorecard, "archive/attempt-1")["task_outcome"] == {"success": True, "source": SOURCE}
    brier = metric(scorecard)
    assert brier["value"] == 0.9
    assert brier["counts"] == {
        "confidence_outcome_pairs": {
            "total": 3,
            "valid": 2,
            "missing": [
                {"reason": f"confidence rating unavailable: {reason}", "count": 1, "ids": ["archive/attempt-1"]},
            ],
            "excluded": [],
        },
    }
    assert brier["evidence"] == {"attempt_ids": ["close/attempt-1", "close/attempt-2"]}


def test_a_confidence_request_that_fails_leaves_the_attempt_completed_with_its_outcome():
    # Only close/attempt-2 gives a pair: (0.4 - 0)^2 = 0.16, score 0.84.
    scorecard = sweep(
        {"close": ["success", "failure"]},
        {"close/attempt-1": TimeoutError("the follow-up timed out"), "close/attempt-2": "40"},
    )

    assert attempt(scorecard, "close/attempt-1") == {
        "attempt_id": "close/attempt-1",
        "task_id": "close",
        "execution_status": "completed",
        "execution_error": None,
        "task_outcome": {"success": True, "source": SOURCE},
        "confidence": unavailable("the confidence request failed: TimeoutError: the follow-up timed out"),
    }
    assert metric(scorecard)["value"] == 0.84


def test_the_agent_is_not_asked_again_after_an_answer_that_cannot_be_read():
    # A second request would be answered with 95.
    scorecard = sweep({"close": ["success"]}, {"close/attempt-1": ["Not sure.", "95"]})

    assert attempt(scorecard, "close/attempt-1")["confidence"] == unavailable(
        "no number in the confidence response", original_response="Not sure."
    )
    assert metric(scorecard)["state"] == "unavailable"


def test_an_answer_that_belongs_to_another_attempt_is_not_paired_with_this_one():
    # close/attempt-1 succeeded and close/attempt-2 failed. The target returns, for the first
    # request, an answer it attributes to the second Attempt. It is paired with neither:
    # the only pair is (0.2, 0), squared error 0.04, score 0.96.
    scorecard = sweep(
        {"close": ["success", "failure"]},
        {
            "close/attempt-1": ConfidenceResponse(attempt_id="close/attempt-2", output="100"),
            "close/attempt-2": "20",
        },
    )

    assert attempt(scorecard, "close/attempt-1")["confidence"] == unavailable(
        "the confidence response belongs to Attempt 'close/attempt-2', not to this Attempt"
    )
    assert attempt(scorecard, "close/attempt-2")["confidence"]["rating"] == "20"
    brier = metric(scorecard)
    assert brier["value"] == 0.96
    assert brier["evidence"] == {"attempt_ids": ["close/attempt-2"]}


@pytest.mark.parametrize(
    "answer", [ConfidenceResponse(attempt_id="close/attempt-1", output=None), lambda request, target: None]
)
def test_a_target_that_returns_no_answer_gives_no_rating(answer):
    scorecard = sweep({"close": ["success"]}, {"close/attempt-1": answer})

    assert attempt(scorecard, "close/attempt-1")["confidence"] == unavailable(
        "the agent target returned no confidence response"
    )


def test_an_attempt_that_did_not_complete_is_not_asked_and_is_not_a_task_failure():
    # Only close/attempt-1 gives a pair: (0.8 - 1)^2 = 0.04, score 0.96.
    scorecard = sweep({"close": ["success", "error"]}, {"close/attempt-1": "80", "close/attempt-2": "0"})

    assert attempt(scorecard, "close/attempt-2") == {
        "attempt_id": "close/attempt-2",
        "task_id": "close",
        "execution_status": "failed",
        "execution_error": "ConnectionError: the agent endpoint returned 503",
        "task_outcome": None,
        "confidence": unavailable("the Attempt did not complete, so no confidence request was sent"),
    }
    brier = metric(scorecard)
    assert brier["value"] == 0.96
    assert brier["counts"] == {
        "confidence_outcome_pairs": {
            "total": 2,
            "valid": 1,
            "missing": [],
            "excluded": [
                {"reason": "execution status is not completed", "count": 1, "ids": ["close/attempt-2"]},
            ],
        },
    }


EXPECTED_PROMPT = (
    "The task above is finished. This is a question about it, not a new task: "
    "do not take any action and do not call any tool.\n"
    "\n"
    "The task counts as a success under this rule:\n"
    "The item is marked done in the to-do service.\n"
    "\n"
    "How likely is it that the task succeeded under this rule? "
    "Answer with one number from 0 to 100 and nothing else. "
    "0 means it certainly did not succeed and 100 means it certainly succeeded. "
    "Decimals are allowed."
)


def test_the_agent_is_shown_its_completed_attempt_and_the_success_rule_and_nothing_about_the_outcome():
    # The fake agent answers with everything it was given, so the retained original response shows it.
    def everything_it_was_given(request, target):
        return json.dumps(asdict(request), default=str)

    scorecard = sweep(
        {"close": ["failure", "success"]},
        {"close/attempt-1": everything_it_was_given, "close/attempt-2": "100"},
    )

    original_response = attempt(scorecard, "close/attempt-1")["confidence"]["original_response"]
    shown = json.loads(original_response)
    assert set(shown) == {"attempt", "response", "success_rule", "prompt"}
    assert shown["attempt"]["attempt_id"] == "close/attempt-1"
    assert shown["attempt"]["prompt"] == "Do close."
    assert shown["response"]["output"] == "Done."
    assert shown["success_rule"] == RULE
    assert shown["prompt"] == EXPECTED_PROMPT
    # Not the outcome, not where it came from, and not the Attempt that follows.
    assert attempt(scorecard, "close/attempt-1")["task_outcome"] == {"success": False, "source": SOURCE}
    assert SOURCE not in original_response
    assert '"success":' not in original_response
    assert "task_outcome" not in original_response
    assert "attempt-2" not in original_response


def test_the_agent_is_asked_after_its_own_attempt_and_before_the_next_one_is_made():
    # The fake agent answers with the number of Attempts it has worked on when it is asked.
    def attempts_worked_on(request, target):
        return str(len(target.service_state))

    scorecard = sweep(
        {"close": ["success", "success", "success"]},
        {f"close/attempt-{number}": attempts_worked_on for number in (1, 2, 3)},
    )

    assert [entry["confidence"]["rating"] for entry in scorecard["attempts"]] == ["1", "2", "3"]


def test_the_agent_is_not_asked_when_no_selected_metric_uses_confidence():
    scorecard = sweep({"close": ["success", "failure"]}, {}, metrics=("outcome_consistency",))

    assert [entry["confidence"] for entry in scorecard["attempts"]] == [None, None]
    assert metric(scorecard)["state"] == "not_configured"


def test_a_missing_rating_does_not_make_the_attempt_a_task_failure():
    # Outcome consistency: both Attempts succeeded, p = 2/2, value 1. Turning the unrated
    # Attempt into a failure would give p = 1/2 and 0.
    # Brier-based score: one valid pair, (1.0 - 1)^2 = 0, score 1.
    scorecard = sweep(
        {"close": ["success", "success"]},
        {"close/attempt-1": "100", "close/attempt-2": "No idea."},
        metrics=("outcome_consistency", "brier_based_score"),
    )

    assert attempt(scorecard, "close/attempt-2")["execution_status"] == "completed"
    assert attempt(scorecard, "close/attempt-2")["task_outcome"] == {"success": True, "source": SOURCE}
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["counts"]["attempts"] == {"total": 2, "valid": 2, "missing": [], "excluded": []}
    brier = metric(scorecard)
    assert brier["value"] == 1
    assert brier["counts"]["confidence_outcome_pairs"] == {
        "total": 2,
        "valid": 1,
        "missing": [
            {
                "reason": "confidence rating unavailable: no number in the confidence response",
                "count": 1,
                "ids": ["close/attempt-2"],
            },
        ],
        "excluded": [],
    }


def test_the_task_outcome_does_not_follow_the_rating():
    # The agent is certain of the opposite of what happened. The outcomes stay what the service
    # state says, and the score is 1 - ((0 - 1)^2 + (1 - 0)^2) / 2 = 0.
    scorecard = sweep({"close": ["success", "failure"]}, {"close/attempt-1": "0", "close/attempt-2": "100"})

    assert [entry["task_outcome"]["success"] for entry in scorecard["attempts"]] == [True, False]
    assert metric(scorecard)["value"] == 0


def test_the_scorecard_says_how_the_brier_based_score_is_defined_and_how_to_read_it():
    brier = metric(sweep(TWO_TASKS, TWO_TASKS_RATINGS))

    definition = brier["definition"]
    conventions = " ".join(definition["fidelity_conventions"])
    assert (brier["name"], brier["dimension"]) == ("Brier-based score", "predictability")
    assert definition["version"] == "1.0.0"
    assert "1 minus the mean of (c - y)^2" in definition["calculation"]
    assert "not the raw loss" in conventions
    assert "pooled across Tasks and Attempts" in conventions
    assert "Only complete pairs" in conventions
    assert "heuristic fallback" in conventions
    assert "how likely the agent thinks the task succeeded" in conventions
    assert (definition["scale"]["minimum"], definition["scale"]["maximum"]) == (0, 1)
    assert "always answers 50 scores 0.75" in definition["interpretation"]


@pytest.mark.parametrize("rule", ["", "   ", 5])
def test_a_shareable_success_rule_that_is_not_the_rule_in_words_is_rejected(rule):
    with pytest.raises(ValueError, match="shareable success rule"):
        Task(
            task_id="close",
            prompt="Do close.",
            success_rule=lambda attempt, response: TaskOutcome(success=True, source=SOURCE),
            shareable_success_rule=rule,
            attempts=1,
        )
"""Evaluator seam: the token usage a standalone Evaluator reports for the Attempts it is given.

It is called as an Evaluator is, with grouped Attempt evidence and without a scorecard.
Expected totals are added by hand.
"""

import pytest

from reliability_evaluators.evidence import Attempt, ResourceMeasurement, group_by_task
from reliability_evaluators.scorecard import evaluator_token_usage


def attempt(attempt_id, status="completed", **evidence):
    return Attempt(attempt_id=attempt_id, task_id=attempt_id.partition("/")[0], execution_status=status, **evidence)


def reported(total_tokens, response_id=None):
    """What an agent target records of an invocation whose usage the platform reported."""
    return {
        "response_id": response_id,
        "token_usage": {"input_tokens": total_tokens - 90, "output_tokens": 90, "total_tokens": total_tokens},
        "token_usage_missing": None,
    }


def test_a_standalone_evaluator_reports_the_usage_of_each_attempt_and_their_total():
    # 790 + 812 = 1602 tokens.
    tasks = group_by_task(
        [
            attempt("close/attempt-1", invocation=reported(790, "resp_1")),
            attempt("close/attempt-2", invocation=reported(812, "resp_2")),
        ]
    )

    usage = evaluator_token_usage(tasks)

    assert usage["unit"] == "tokens"
    assert [(entry["attempt_id"], entry["coverage"], entry["total_tokens"]) for entry in usage["attempts"]] == [
        ("close/attempt-1", "complete", 790),
        ("close/attempt-2", "complete", 812),
    ]
    assert usage["attempts"][0]["invocation"] == {
        "basis": "reported",
        "total_tokens": 790,
        "reason": None,
        "response_id": "resp_1",
        "request_ids": {},
    }
    assert usage["total"] == {
        "coverage": "complete",
        "total_tokens": 1602,
        "uses_reported": 2,
        "uses_missing": 0,
        "missing": [],
    }


def test_usage_that_was_not_reported_is_missing_with_its_reason_and_the_sum_of_the_rest_is_partial():
    # Only the first Attempt has a reported count, 790. The total is that sum and is labelled partial.
    no_usage = {"token_usage": None, "token_usage_missing": "the platform returned an error and no usage"}
    tasks = group_by_task(
        [
            attempt("close/attempt-1", invocation=reported(790)),
            attempt("close/attempt-2", status="failed", execution_error="HTTP 400", invocation=no_usage),
            attempt("close/attempt-3"),
        ]
    )

    usage = evaluator_token_usage(tasks)

    assert usage["total"] == {
        "coverage": "partial",
        "total_tokens": 790,
        "uses_reported": 1,
        "uses_missing": 2,
        "missing": [
            {
                "use": "agent_invocation",
                "attempt_id": "close/attempt-2",
                "reason": "the platform returned an error and no usage",
            },
            {
                "use": "agent_invocation",
                "attempt_id": "close/attempt-3",
                "reason": "the agent target recorded nothing of the invocation",
            },
        ],
    }
    failed = usage["attempts"][1]
    assert (failed["execution_status"], failed["coverage"], failed["total_tokens"]) == ("failed", "unavailable", None)
    assert failed["invocation"] == {
        "basis": "missing",
        "total_tokens": None,
        "reason": "the platform returned an error and no usage",
        "response_id": None,
        "request_ids": {},
    }


def test_when_no_usage_is_known_the_total_is_unavailable_and_not_zero():
    tasks = group_by_task([attempt("close/attempt-1"), attempt("close/attempt-2", invocation={"response_id": "resp_2"})])

    usage = evaluator_token_usage(tasks)

    assert (usage["total"]["coverage"], usage["total"]["total_tokens"]) == ("unavailable", None)
    assert (usage["total"]["uses_reported"], usage["total"]["uses_missing"]) == (0, 2)
    assert [entry["reason"] for entry in usage["total"]["missing"]] == [
        "the agent target recorded nothing of the invocation",
        "the agent target recorded no token usage for the invocation",
    ]
    # The Evaluator is calculated by code, which uses no model. That zero is known, and it does not
    # make a total of the usage that is missing.
    assert usage["scoring"] == [
        {
            "basis": "no_model_use",
            "total_tokens": 0,
            "reason": "the Evaluator is calculated by code in the customer's process, which uses no model",
            "scoring_path": "customer_process",
        }
    ]


@pytest.mark.parametrize(
    "recorded",
    [
        {"total_tokens": "790"},
        {"total_tokens": 790.0},
        {"total_tokens": True},
        {"total_tokens": -5},
        {"input_tokens": 700, "output_tokens": 90},
        790,
    ],
)
def test_a_recorded_usage_without_a_whole_number_total_is_missing_and_nothing_is_made_of_its_parts(recorded):
    tasks = group_by_task([attempt("close/attempt-1", invocation={"response_id": "resp_1", "token_usage": recorded})])

    usage = evaluator_token_usage(tasks)

    assert usage["attempts"][0]["invocation"] == {
        "basis": "missing",
        "total_tokens": None,
        "reason": "the token usage the agent target recorded for the invocation has no total_tokens that is a whole number",
        "response_id": "resp_1",
        "request_ids": {},
    }
    assert (usage["total"]["coverage"], usage["total"]["total_tokens"]) == ("unavailable", None)


def test_a_resource_measurement_of_token_usage_is_evidence_for_a_metric_and_is_not_what_is_reported_as_usage():
    # Resource consistency may be given token usage as a resource. The usage report reads what
    # the agent target recorded of the invocation, and nothing else.
    measured = ResourceMeasurement(
        resource_type="token_usage",
        value=5000,
        unit="tokens",
        boundary="all model calls of the Attempt, counted by the customer's gateway",
        reliably_sourced=True,
    )
    tasks = group_by_task(
        [
            attempt("close/attempt-1", resource_measurements=[measured], invocation=reported(790)),
            attempt("close/attempt-2", resource_measurements=[measured]),
        ]
    )

    usage = evaluator_token_usage(tasks)

    assert [entry["total_tokens"] for entry in usage["attempts"]] == [790, None]
    assert (usage["total"]["coverage"], usage["total"]["total_tokens"]) == ("partial", 790)


def test_failed_cancelled_and_retried_attempts_are_included_with_their_statuses_and_what_they_used():
    # 410 + 95 + 790 = 1295 tokens. The Attempt that was cancelled reported nothing: 1295 is a partial sum.
    tasks = group_by_task(
        [
            attempt("close/attempt-1", status="retried", execution_error="HTTP 500", invocation=reported(410)),
            attempt("close/attempt-1/retry-1", status="failed", execution_error="HTTP 500", retry_of="close/attempt-1",
                    invocation=reported(95)),
            attempt("close/attempt-2", status="cancelled", execution_error="the caller closed the connection"),
            attempt("close/attempt-3", status="timed_out", execution_error="answered late", invocation=reported(790)),
        ]
    )

    usage = evaluator_token_usage(tasks)

    assert [
        (entry["attempt_id"], entry["execution_status"], entry["retry_of"], entry["total_tokens"])
        for entry in usage["attempts"]
    ] == [
        ("close/attempt-1", "retried", None, 410),
        ("close/attempt-1/retry-1", "failed", "close/attempt-1", 95),
        ("close/attempt-2", "cancelled", None, None),
        ("close/attempt-3", "timed_out", None, 790),
    ]
    assert (usage["total"]["coverage"], usage["total"]["total_tokens"]) == ("partial", 1295)


def test_an_attempt_the_agent_was_not_invoked_for_used_no_tokens_and_that_zero_is_known():
    tasks = group_by_task(
        [
            attempt("close/attempt-1", invocation=reported(790)),
            attempt("close/attempt-2", status="setup_failed", execution_error="prepare_state failed: no service"),
        ]
    )

    usage = evaluator_token_usage(tasks)

    assert usage["attempts"][1]["invocation"] == {
        "basis": "no_model_use",
        "total_tokens": 0,
        "reason": "the agent was not invoked for this Attempt",
        "response_id": None,
        "request_ids": {},
    }
    assert (usage["attempts"][1]["coverage"], usage["attempts"][1]["total_tokens"]) == ("complete", 0)
    assert usage["total"] == {
        "coverage": "complete",
        "total_tokens": 790,
        "uses_reported": 1,
        "uses_missing": 0,
        "missing": [],
    }

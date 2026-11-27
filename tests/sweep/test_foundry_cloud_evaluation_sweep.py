"""Evaluator sweep seam: Attempts invoked through Foundry cloud evaluation, without Foundry access.

The sweep is driven through its public entry point with the Foundry cloud evaluation target.
The target talks to a fake Foundry project in this process, whose scripted agents work on the
live fixture's to-do service, run on this machine. Every assertion is on the machine-readable
scorecard. Expected values are calculated by hand.
"""

import json

import pytest

from live_fixture import TASK_PROMPT, local_service
from reliability_evaluators.agent_target.foundry_cloud_evaluation import (
    ELAPSED_TIME_BOUNDARY,
    NATIVE_TOOL_CALLS,
    TOOL_CALL_BOUNDARY,
    FoundryCloudEvaluationTarget,
    UnsupportedCombination,
    native_tool_calls,
)
from reliability_evaluators.evidence import OutcomeCorrection, TaskOutcome
from reliability_evaluators.managed_scoring import FoundryRequestError
from reliability_evaluators.scorecard import AcceptanceCriterion, render_report, to_json
from reliability_evaluators.sweep import ActionEvidenceDeclaration, ExecutionLimits, Task, recalculate, run_sweep
from tests.fake_foundry_cloud_evaluation import TOOL_USER_ERROR, FakeAgent, FakeCloudEvaluationProject
from tests.fakes import attempt_evidence
from tests.report_reader import assert_faithful_rendering
from tests.todo_fixture_sweep import CONDITION_SOURCE, UPDATE_FAILS, TodoFixtureSweep

OUTCOME_SOURCE = "to-do service events and before/after state"
# The state of the to-do service that the Task's prompt names and the controls prepare and remove.
STATE = "run-1"
AGENTS = {"prompt": "todo-prompt", "hosted": "todo-hosted"}


@pytest.fixture
def todo_service():
    with local_service() as service:
        yield service


def metric(scorecard, metric_id):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def cloud_target(todo_service, script=(), *, kind="prompt", served=None, project=None, **agent):
    """The target on a fake project whose one agent is of type `kind` and does what `script` says, in order.

    `served` is how the fake project serves the agent, where that is not as a supported agent
    of that type with the one version 1. `project` is what the fake project is scripted to do.
    Everything else is given to the target.
    """
    fake_agent = FakeAgent(**{"kind": kind, "script": list(script), **(served or {})})
    fake = FakeCloudEvaluationProject(todo_service, {AGENTS[kind]: fake_agent}, **(project or {}))
    return FoundryCloudEvaluationTarget(
        **{"project": fake, "agent_name": AGENTS[kind], "poll_interval_seconds": 0, **agent}
    )


def sweep(
    todo_service,
    script,
    metrics=("outcome_consistency",),
    *,
    kind="prompt",
    attempts=None,
    starting_state="fresh",
    variation=None,
    limits=ExecutionLimits(),
    served=None,
    project=None,
    agent=None,
    **declared,
):
    """Run the fixture Task through cloud evaluation, one Attempt per scripted behaviour.

    `kind`, `served` and `project` say what the fake project does, and `agent` is what the
    target is given: see `cloud_target`. Everything else is declared to the sweep.
    """
    fixture = TodoFixtureSweep(todo_service, state=STATE)
    target = cloud_target(todo_service, script, kind=kind, served=served, project=project, **(agent or {}))
    experiment = fixture.experiment(
        attempts=attempts or len(script), starting_state=starting_state, variation=variation, limits=limits
    )
    return run_sweep(experiments=[experiment], agent_target=target, metrics=metrics, **declared)

@pytest.mark.parametrize("kind", ["prompt", "hosted"])
def test_a_sweep_scores_attempts_invoked_through_cloud_evaluation_from_what_the_service_recorded(todo_service, kind):
    # Four Attempts complete the Task. In the fifth the agent leaves out the update and still
    # answers that it completed everything. p = 4/5, (2 * 0.8 - 1)^2 = 0.36.
    scorecard = sweep(
        todo_service, ["completes", "completes", "completes", "completes", "skips the update"], kind=kind
    )

    assert [attempt_evidence(attempt) for attempt in scorecard["attempts"]] == [
        {
            "attempt_id": f"todo/baseline/attempt-{number}",
            "task_id": "todo",
            "execution_status": "completed",
            "execution_error": None,
            "task_outcome": {"success": number < 5, "source": OUTCOME_SOURCE},
        }
        for number in (1, 2, 3, 4, 5)
    ]
    # Each Attempt says that it was invoked through cloud evaluation, and for which type of agent.
    assert [
        (attempt["invocation"]["entry_point"], attempt["invocation"]["agent_kind"])
        for attempt in scorecard["attempts"]
    ] == [("foundry_cloud_evaluation", kind)] * 5
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == pytest.approx(0.36, abs=1e-12)


TARGET = {"id": "target", "title": "Synthetic target", "done": False}
SCRATCH = {"id": "scratch", "title": "Temporary", "done": False}
CRITERION = {
    "type": "string_check",
    "name": "required_by_the_platform_not_a_judgement",
    "input": "{{item.attempt_id}}",
    "reference": "{{item.attempt_id}}",
    "operation": "eq",
}
DIAGNOSTICS_MEANING = (
    "Foundry cloud evaluation does not invoke the agent without a testing criterion, and labels every item "
    "against it. The criterion compares the identity of the Attempt with itself, so its labels and counts "
    "say nothing about the agent, the Task or the Attempt: they are platform diagnostics, not a task "
    "outcome and not the customer's acceptance criterion."
)


def usage(prompt_tokens, completion_tokens):
    return {
        "cached_tokens": 0,
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def prompt_tool_call(invocation, position, name, arguments, returned):
    """A tool call of the fake prompt agent as it is recorded: its output is the text the platform gave."""
    return {
        "call_id": f"call_fake_{invocation}_{position}",
        "type": "openapi_call",
        "name": name,
        "arguments": arguments,
        "output": json.dumps({"response": json.dumps(returned)}),
    }


def test_each_attempt_keeps_the_identifiers_the_usage_and_the_ordered_tool_calls_the_run_exposed(todo_service):
    scorecard = sweep(todo_service, ["skips the update", "completes"])

    assert scorecard["attempts"][0]["invocation"] == {
        "agent_target": "foundry_cloud_evaluation",
        "entry_point": "foundry_cloud_evaluation",
        "api_version": "v1",
        "agent_kind": "prompt",
        "composed_input": None,
        "eval_id": "eval_fake_1",
        "run_id": "evalrun_fake_1",
        "run_status": "completed",
        "output_item_id": "1",
        "status": "completed",
        "criterion": CRITERION,
        "agent": {"id": "todo-prompt:1", "name": "todo-prompt", "version": "1"},
        "response_id": "resp_fake_1",
        "conversation_id": "conv_fake_1",
        "model": "todo-gpt-41-mini",
        # The identity of the Attempt was a column of the run's one row, and the output item returned it.
        "metadata": {"attempt_id": "todo/baseline/attempt-1", "task_id": "todo"},
        "error": None,
        "token_usage": usage(500, 60),
        "token_usage_missing": None,
        # The run's own usage rows, as returned. For a prompt agent both rows count the same tokens.
        "run_per_model_usage": [
            {"model_name": "todo-gpt-41-mini", "invocation_count": 1, **usage(500, 60)},
            {"model_name": "azure_ai_agent_target", "invocation_count": 1, **usage(500, 60)},
        ],
        "latency_ms": 5000.0,
        "tool_calls": [
            prompt_tool_call(1, 1, "todos_readTodo", {"attempt_id": STATE, "todo_id": "target"}, TARGET),
            prompt_tool_call(
                1, 2, "todos_createTodo", {"id": "scratch", "title": "Temporary", "attempt_id": STATE}, SCRATCH
            ),
            prompt_tool_call(1, 3, "todos_readTodo", {"attempt_id": STATE, "todo_id": "scratch"}, SCRATCH),
            prompt_tool_call(1, 4, "todos_deleteTodo", {"attempt_id": STATE, "todo_id": "scratch"}, SCRATCH),
        ],
        "platform_diagnostics": {
            "meaning": DIAGNOSTICS_MEANING,
            "result_counts": {"total": 1, "passed": 1, "failed": 0, "errored": 0, "skipped": 0},
            "per_testing_criteria_results": [
                {
                    "testing_criteria": "required_by_the_platform_not_a_judgement",
                    "passed": 1,
                    "failed": 0,
                    "errored": 0,
                    "skipped": 0,
                }
            ],
            "results": [
                {
                    "type": "string_check",
                    "name": "required_by_the_platform_not_a_judgement",
                    "metric": "required_by_the_platform_not_a_judgement",
                    "score": 1,
                    "label": "pass",
                    "reason": None,
                    "threshold": None,
                    "passed": True,
                    "status": "completed",
                }
            ],
        },
    }
    second = scorecard["attempts"][1]["invocation"]
    # Every Attempt is a run of its own, with a response and a conversation of its own, in the one eval.
    assert (second["eval_id"], second["run_id"], second["output_item_id"]) == ("eval_fake_1", "evalrun_fake_2", "1")
    assert (second["response_id"], second["conversation_id"]) == ("resp_fake_2", "conv_fake_2")
    assert second["metadata"] == {"attempt_id": "todo/baseline/attempt-2", "task_id": "todo"}
    assert second["token_usage"] == usage(600, 70)
    assert [call["name"] for call in second["tool_calls"]] == [
        "todos_readTodo",
        "todos_createTodo",
        "todos_readTodo",
        "todos_deleteTodo",
        "todos_updateTodo",
    ]


def test_a_run_and_item_that_report_completed_while_carrying_a_target_error_are_an_execution_failure(todo_service):
    # Two Attempts under the baseline complete the Task: p = 2/2, value 1. Under the fault the update
    # tool answers 503, which fails the invocation of a prompt agent. The run and its item still report
    # completed and the criterion still passes, as observed live: only the item's sample error says otherwise.
    # Scored as task failures, the two would not change the value here, but they would be Attempts of the metric.
    scorecard = sweep(todo_service, ["completes"] * 4, attempts=2, variation=UPDATE_FAILS)

    faulted = scorecard["attempts"][2]
    assert attempt_evidence(faulted, "observed_condition", "resource_measurements") == {
        "attempt_id": "todo/update-fails/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationFailed: the cloud evaluation run evalrun_fake_3 and its output item report 'completed', and "
            f"the item carries a target error: TARGET_INVOCATION_FAILED: {TOOL_USER_ERROR}"
        ),
        "task_outcome": None,
        # Only the service shows that the fault occurred, and that four calls went before it.
        "observed_condition": {
            "met": True,
            "source": CONDITION_SOURCE,
            "observations": {"calls": 5, "faulted_calls": ["update"]},
        },
        "resource_measurements": [],
    }
    invocation = faulted["invocation"]
    assert (invocation["entry_point"], invocation["run_status"], invocation["status"]) == (
        "foundry_cloud_evaluation",
        "completed",
        "completed",
    )
    assert invocation["error"] == {"code": "TARGET_INVOCATION_FAILED", "message": TOOL_USER_ERROR}
    # What the platform reported for an Attempt whose target failed: no response, no usage, no tool call.
    assert (invocation["run_id"], invocation["response_id"], invocation["conversation_id"]) == (
        "evalrun_fake_3",
        None,
        None,
    )
    assert invocation["agent"] == {"id": "", "name": "todo-prompt", "version": None}
    assert (invocation["token_usage"], invocation["latency_ms"]) == (None, None)
    assert invocation["token_usage_missing"] == "the output item of the cloud evaluation run carried no usage"
    assert (invocation["tool_calls"], invocation["run_per_model_usage"]) == ([], [])
    # The platform still labelled the item as passed. That stays a diagnostic.
    assert invocation["platform_diagnostics"]["result_counts"] == {
        "total": 1, "passed": 1, "failed": 0, "errored": 0, "skipped": 0
    }
    assert [result["label"] for result in invocation["platform_diagnostics"]["results"]] == ["pass"]
    assert scorecard["attempts"][3]["execution_status"] == "failed"
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["evidence"] == {"attempt_ids": ["todo/baseline/attempt-1", "todo/baseline/attempt-2"]}

def test_the_target_reports_the_latency_foundry_measured_and_the_tool_call_count_as_resource_measurements(
    todo_service,
):
    # Tool calls: 4 and 5. Mean 4.5, population standard deviation 0.5, CV 1/9.
    # Elapsed time: the fake project reports a latency of 5000 and 6000 ms, which is recorded as it was
    # reported. Mean 5500, population standard deviation 500, CV 1/11.
    # Task value exp(-(1/9 + 1/11) / 2) = exp(-10/99) = 0.9039239022952824.
    scorecard = sweep(todo_service, ["skips the update", "completes"], metrics=["resource_consistency"])

    assert [attempt["resource_measurements"] for attempt in scorecard["attempts"]] == [
        [
            {
                "resource_type": "elapsed_time",
                "value": milliseconds,
                "unit": "milliseconds",
                "boundary": ELAPSED_TIME_BOUNDARY,
                "reliably_sourced": True,
            },
            {
                "resource_type": "tool_calls",
                "value": count,
                "unit": "calls",
                "boundary": TOOL_CALL_BOUNDARY,
                "reliably_sourced": True,
            },
        ]
        for milliseconds, count in ((5000.0, 4), (6000.0, 5))
    ]
    resource_consistency = metric(scorecard, "resource_consistency")
    assert resource_consistency["state"] == "available"
    assert resource_consistency["value"] == pytest.approx(0.9039239022952824, abs=1e-12)


def test_a_hosted_agent_whose_tool_fails_still_completes_and_its_task_outcome_comes_from_the_service(todo_service):
    # Under the baseline both Attempts complete the Task: accuracy 1. Under the fault the update tool
    # answers 503. A hosted agent answers all the same, and its item carries no error, as observed live.
    # The service shows that the target item was not updated, so both Attempts fail the Task: accuracy 0.
    # Fault robustness: min(0 / 1, 1) = 0.
    scorecard = sweep(
        todo_service, ["completes"] * 4, metrics=["fault_robustness"], kind="hosted", attempts=2, variation=UPDATE_FAILS
    )

    faulted = scorecard["attempts"][2]
    assert attempt_evidence(faulted, "observed_condition") == {
        "attempt_id": "todo/update-fails/attempt-1",
        "task_id": "todo",
        "execution_status": "completed",
        "execution_error": None,
        "task_outcome": {"success": False, "source": OUTCOME_SOURCE},
        "observed_condition": {
            "met": True,
            "source": CONDITION_SOURCE,
            "observations": {"calls": 5, "faulted_calls": ["update"]},
        },
    }
    invocation = faulted["invocation"]
    assert (invocation["entry_point"], invocation["agent_kind"]) == ("foundry_cloud_evaluation", "hosted")
    assert (invocation["response_id"], invocation["conversation_id"]) == ("caresp_fake_3", "conv_fake_3")
    assert invocation["agent"] == {"id": "todo-hosted:1", "name": "todo-hosted", "version": "1"}
    # The platform names no model for a hosted agent, and reports one usage row for the run.
    assert invocation["model"] == ""
    assert invocation["run_per_model_usage"] == [
        {"model_name": "azure_ai_agent_target", "invocation_count": 1, **usage(600, 70)}
    ]
    assert invocation["error"] is None
    # The failed call shows only in what the tool returned, which the platform gives as an object.
    assert invocation["tool_calls"][4] == {
        "call_id": "call_fake_3_5",
        "type": "function_call",
        "name": "update_todo",
        "arguments": {"done": True, "attempt_id": STATE, "todo_id": "target"},
        "output": {"status": 503, "body": {"error": "injected fault", "tool": "update"}},
    }
    fault_robustness = metric(scorecard, "fault_robustness")
    assert fault_robustness["state"] == "available"
    assert fault_robustness["value"] == 0


def test_what_the_run_exposed_is_in_the_report_and_is_kept_when_the_scorecard_is_recalculated(todo_service):
    # As run: p = 1/2, (2 * 0.5 - 1)^2 = 0. With the second Attempt corrected to a success: p = 2/2, value 1.
    scorecard = sweep(todo_service, ["completes", "skips the update"], kind="hosted")

    report = assert_faithful_rendering(render_report(scorecard), scorecard)
    earlier = json.loads(to_json(scorecard))
    recalculated = recalculate(
        earlier_scorecard=earlier,
        outcome_corrections=[
            OutcomeCorrection(
                attempt_id="todo/baseline/attempt-2",
                task_outcome=TaskOutcome(success=True, source="manual review of the service events"),
                provenance="synthetic correction",
            )
        ],
    )

    assert report.value("/attempts/0/invocation/entry_point") == "foundry_cloud_evaluation"
    assert report.value("/attempts/1/invocation/tool_calls/3/name") == "delete_todo"
    assert report.value("/attempts/0/invocation/platform_diagnostics/results/0/label") == "pass"
    assert metric(scorecard, "outcome_consistency")["value"] == 0
    assert metric(recalculated, "outcome_consistency")["value"] == 1
    assert [attempt["invocation"] for attempt in recalculated["attempts"]] == [
        attempt["invocation"] for attempt in earlier["attempts"]
    ]
    assert recalculated["attempts"][1]["invocation"]["run_id"] == "evalrun_fake_2"

def test_the_token_usage_report_counts_what_the_output_item_reported_and_nothing_twice(todo_service):
    # The Attempt under the baseline makes five tool calls: the fake project reports 600 + 70 = 670 tokens
    # for its item, and the same 670 in each of the run's two usage rows. The report counts 670, not 1340.
    # Under the fault the target fails and the item carries no usage: that is missing, not zero.
    scorecard = sweep(todo_service, ["completes", "completes"], attempts=1, variation=UPDATE_FAILS)

    usage = scorecard["token_usage"]
    assert [
        (entry["attempt_id"], entry["execution_status"], entry["coverage"], entry["total_tokens"])
        for entry in usage["attempts"]
    ] == [
        ("todo/baseline/attempt-1", "completed", "complete", 670),
        ("todo/update-fails/attempt-1", "failed", "unavailable", None),
    ]
    assert usage["attempts"][0]["invocation"] == {
        "basis": "reported",
        "total_tokens": 670,
        "reason": None,
        "response_id": "resp_fake_1",
        "request_ids": {},
    }
    assert usage["attempts"][1]["invocation"] == {
        "basis": "missing",
        "total_tokens": None,
        "reason": "the output item of the cloud evaluation run carried no usage",
        "response_id": None,
        "request_ids": {},
    }
    assert (usage["sweep"]["coverage"], usage["sweep"]["total_tokens"]) == ("partial", 670)

def test_the_labels_foundry_gives_an_item_are_platform_diagnostics_and_decide_nothing(todo_service):
    # The fake project labels the criterion as failed on every item. The Attempts completed all the
    # same and their task outcomes come from the service: p = 2/2, value 1. An acceptance criterion
    # is the customer's own: 1 is at least 0.9, so it is met, whatever Foundry labelled.
    scorecard = sweep(
        todo_service,
        ["completes", "completes"],
        project={"criterion_fails": True},
        acceptance_criteria=[
            AcceptanceCriterion(metric_id="outcome_consistency", condition={"value": {"at_least": 0.9}})
        ],
    )

    attempts = scorecard["attempts"]
    assert [attempt["execution_status"] for attempt in attempts] == ["completed", "completed"]
    assert [attempt["task_outcome"]["success"] for attempt in attempts] == [True, True]
    diagnostics = attempts[0]["invocation"]["platform_diagnostics"]
    assert diagnostics["meaning"] == DIAGNOSTICS_MEANING
    assert diagnostics["result_counts"] == {"total": 1, "passed": 0, "failed": 1, "errored": 0, "skipped": 0}
    assert [(result["label"], result["passed"]) for result in diagnostics["results"]] == [("fail", False)]
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["value"] == 1
    assert outcome_consistency["acceptance"]["status"] == "met"

def not_invoked(**recorded):
    """What is recorded of an Attempt for which the run returned no output item."""
    return {
        "agent_target": "foundry_cloud_evaluation",
        "entry_point": "foundry_cloud_evaluation",
        "api_version": "v1",
        "agent_kind": "prompt",
        "composed_input": None,
        "eval_id": None,
        "run_id": None,
        "run_status": None,
        "output_item_id": None,
        "status": None,
        "criterion": CRITERION,
        "agent": None,
        "response_id": None,
        "conversation_id": None,
        "model": None,
        "metadata": None,
        "error": None,
        "token_usage": None,
        "token_usage_missing": "the cloud evaluation run returned no output item, so no usage was reported",
        "run_per_model_usage": None,
        "latency_ms": None,
        "tool_calls": None,
        "platform_diagnostics": {
            "meaning": DIAGNOSTICS_MEANING,
            "result_counts": None,
            "per_testing_criteria_results": None,
            "results": None,
        },
        **recorded,
    }


def test_a_run_that_fails_before_it_invokes_the_agent_is_an_execution_failure(todo_service):
    # The project has no version 9 of the agent. The run is made, and fails when the project validates it.
    scorecard = sweep(todo_service, ["completes", "completes"], agent={"agent_version": "9"})

    run_error = {
        "code": "EvalValidationFailed",
        "message": "Evaluation validation failed: agent resource is not found.",
        "correlation_id": None,
        "target": "eval_fake_1",
        "details_uri": "https://aka.ms/FoundryEvalsFormatIssues",
        "details": [
            {
                "code": "ResourceNotFound",
                "target": "run.data_source.target",
                "message": (
                    "The AzureAIAgentTarget 'todo-prompt' was not found. Verify the name and version are correct."
                ),
            }
        ],
        "inner_error": None,
    }
    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationFailed: the cloud evaluation run evalrun_fake_1 ended as 'failed' and returned no output "
            "item: EvalValidationFailed: Evaluation validation failed: agent resource is not found. "
            "(ResourceNotFound: The AzureAIAgentTarget 'todo-prompt' was not found. Verify the name and version "
            "are correct.)"
        ),
        "task_outcome": None,
        "invocation": not_invoked(
            eval_id="eval_fake_1",
            run_id="evalrun_fake_1",
            run_status="failed",
            error=run_error,
            run_per_model_usage=[],
            platform_diagnostics={
                "meaning": DIAGNOSTICS_MEANING,
                "result_counts": {"total": 0, "passed": 0, "failed": 0},
                "per_testing_criteria_results": [],
                "results": None,
            },
        ),
    }
    # No Attempt completed, so the metric has no state of its own: its experiment failed.
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["state"] is None
    assert outcome_consistency["execution_failure"]["experiment_ids"] == ["todo"]


def test_a_pinned_agent_version_is_the_one_the_run_invokes(todo_service):
    # The fake project has versions 1 and 2 of the agent, and invokes the latest unless the run names another.
    versions = {"served": {"versions": ("1", "2")}}

    latest = sweep(todo_service, ["completes"], **versions)
    pinned = sweep(todo_service, ["completes"], agent={"agent_version": "1"}, **versions)

    assert [scorecard["attempts"][0]["invocation"]["agent"] for scorecard in (latest, pinned)] == [
        {"id": "todo-prompt:2", "name": "todo-prompt", "version": "2"},
        {"id": "todo-prompt:1", "name": "todo-prompt", "version": "1"},
    ]

NO_RUN = "no cloud evaluation run is known to have been made, so no usage was reported"
RESULT_NOT_READ = "the result of the cloud evaluation run was not read, so no usage is known"
NO_COUNTS = {"total": 0, "passed": 0, "failed": 0}


class FakeClock:
    """A clock that only moves when the target waits."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.mark.parametrize(
    ("limits", "own_limit"),
    [
        # The time limit the customer declared for the Attempt, with the target's own left far above it.
        (ExecutionLimits(attempt_timeout_seconds=12), {"run_timeout_seconds": 900}),
        # No limit declared for the Attempt: the target's own.
        (ExecutionLimits(), {"run_timeout_seconds": 12}),
    ],
)
def test_a_run_that_has_not_ended_at_the_time_limit_timed_out_and_keeps_the_identifiers_of_its_run(
    todo_service, limits, own_limit
):
    # The run never ends. The target asks for it every 5 seconds: at 5 and at 10, and at the limit of 12.
    clock = FakeClock()
    scorecard = sweep(
        todo_service,
        ["completes"],
        limits=limits,
        project={"never_finishes": True},
        agent={"clock": clock, "sleep": clock.sleep, "poll_interval_seconds": 5, **own_limit},
    )

    assert clock.now == 12
    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "timed_out",
        "execution_error": (
            "RunTimedOut: the cloud evaluation run evalrun_fake_1 had not ended within 12 seconds, in status "
            "'in_progress'. Its cancellation was requested, which does not stop an agent that was already invoked"
        ),
        "task_outcome": None,
        # The run is identified, and its status is what the project answered to the cancellation.
        "invocation": not_invoked(
            eval_id="eval_fake_1",
            run_id="evalrun_fake_1",
            run_status="canceled",
            token_usage_missing=RESULT_NOT_READ,
            run_per_model_usage=[],
            platform_diagnostics={
                "meaning": DIAGNOSTICS_MEANING,
                "result_counts": NO_COUNTS,
                "per_testing_criteria_results": [],
                "results": None,
            },
        ),
    }


def test_a_run_the_project_refuses_to_make_is_an_execution_failure(todo_service):
    refusal = FoundryRequestError("POST", "/openai/v1/evals/eval_fake_1/runs", 429, "rate limit exceeded")

    scorecard = sweep(todo_service, ["completes", "completes"], project={"refuses_runs": refusal})

    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationFailed: the cloud evaluation run could not be made in the Foundry project: "
            "POST /openai/v1/evals/eval_fake_1/runs: HTTP 429: rate limit exceeded"
        ),
        "task_outcome": None,
        "invocation": not_invoked(eval_id="eval_fake_1", token_usage_missing=NO_RUN),
    }


def test_a_project_that_does_not_answer_is_an_execution_failure_that_still_records_the_entry_point(todo_service):
    unreachable = FoundryRequestError("GET", "/agents/todo-prompt?api-version=v1", None, "URLError: timed out")

    scorecard = sweep(todo_service, ["completes"], project={"refuses": unreachable})

    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationFailed: agent 'todo-prompt' could not be looked up in the Foundry project: "
            "GET /agents/todo-prompt?api-version=v1: no answer: URLError: timed out"
        ),
        "task_outcome": None,
        "invocation": not_invoked(agent_kind=None, token_usage_missing=NO_RUN),
    }


class AnswersWithoutARun:
    """A project that answers the making of a run with something that is no run."""

    def __init__(self, project):
        self._project = project

    def request(self, method, path, body=None, headers=None):
        answered = self._project.request(method, path, body, headers)
        return {"object": "list", "data": []} if (method, path[-5:]) == ("POST", "/runs") else answered


def test_an_answer_to_the_making_of_a_run_that_is_no_run_is_an_execution_failure(todo_service):
    project = AnswersWithoutARun(FakeCloudEvaluationProject(todo_service, {"todo-prompt": FakeAgent()}))
    target = FoundryCloudEvaluationTarget(project=project, agent_name="todo-prompt", poll_interval_seconds=0)
    experiment = TodoFixtureSweep(todo_service, state=STATE).experiment(attempts=1)

    scorecard = run_sweep(experiments=[experiment], agent_target=target, metrics=["outcome_consistency"])

    assert attempt_evidence(scorecard["attempts"][0], "invocation") == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": (
            "InvocationFailed: the Foundry project answered the making of the cloud evaluation run with "
            'something that is no run: {"object": "list", "data": []}'
        ),
        "task_outcome": None,
        # Whether a run was made is not known, so nothing is recorded as its run.
        "invocation": not_invoked(eval_id="eval_fake_1", token_usage_missing=NO_RUN),
    }

def no_item(item):
    return None


def item_of_another_attempt(item):
    return {**item, "datasource_item": {**item["datasource_item"], "attempt_id": "todo/baseline/attempt-7"}}


def item_without_a_response(item):
    return {**item, "datasource_item": {**item["datasource_item"], "response_id": None}}


@pytest.mark.parametrize(
    ("returned", "why"),
    [
        (no_item, "the cloud evaluation run evalrun_fake_1 returned 0 output items for the one Attempt it was given"),
        (
            item_of_another_attempt,
            "the cloud evaluation run evalrun_fake_1 returned an output item for 'todo/baseline/attempt-7' and not "
            "for Attempt 'todo/baseline/attempt-1'",
        ),
        (
            item_without_a_response,
            "the output item of the cloud evaluation run evalrun_fake_1 reports 'completed' and names no response "
            "of the agent, so nothing shows that the agent ran",
        ),
    ],
)
def test_a_completed_run_that_does_not_return_the_attempts_own_response_is_an_execution_failure(
    todo_service, returned, why
):
    # The agent did the whole Task on the service both times. What the first run returned does not
    # show it for this Attempt, so that Attempt is not scored: p = 1/1 from the second, and with one
    # Attempt left the Task has no outcome consistency. As a task success it would be 2/2 and the value 1.
    returns_once = iter([returned])
    scorecard = sweep(
        todo_service,
        ["completes", "completes"],
        project={"alter_item": lambda item: next(returns_once, lambda item: item)(item)},
    )

    first, second = scorecard["attempts"]
    assert attempt_evidence(first) == {
        "attempt_id": "todo/baseline/attempt-1",
        "task_id": "todo",
        "execution_status": "failed",
        "execution_error": f"InvocationFailed: {why}",
        "task_outcome": None,
    }
    assert (first["invocation"]["entry_point"], first["invocation"]["run_status"]) == (
        "foundry_cloud_evaluation",
        "completed",
    )
    # An item that is not this Attempt's is not recorded as its evidence.
    if returned is not item_without_a_response:
        assert (first["invocation"]["output_item_id"], first["invocation"]["token_usage"]) == (None, None)
    assert second["execution_status"] == "completed"
    assert metric(scorecard, "outcome_consistency")["state"] == "unavailable"


@pytest.mark.parametrize(
    ("kind", "served", "why"),
    [
        (
            "hosted",
            {"protocols": ("invocations",)},
            "hosted agent 'todo-hosted' serves the protocols ['invocations'] and not the Responses protocol. "
            "Invoking such an agent through Foundry cloud evaluation has not been verified, so it was not invoked",
        ),
        (
            "prompt",
            {"kind": "workflow"},
            "agent 'todo-prompt' is of kind 'workflow'. Only prompt agents, and hosted agents that serve the "
            "Responses protocol, have been invoked through Foundry cloud evaluation, so it was not invoked",
        ),
    ],
)
def test_an_agent_that_was_never_invoked_through_cloud_evaluation_is_reported_as_unsupported(
    todo_service, kind, served, why
):
    scorecard = sweep(todo_service, ["completes", "completes"], kind=kind, served=served)

    # No eval and no run were made for it: nothing was tried instead.
    assert [attempt_evidence(attempt, "invocation") for attempt in scorecard["attempts"]] == [
        {
            "attempt_id": f"todo/baseline/attempt-{number}",
            "task_id": "todo",
            "execution_status": "failed",
            "execution_error": f"UnsupportedCombination: {why}",
            "task_outcome": None,
            "invocation": not_invoked(agent_kind=served.get("kind", kind), token_usage_missing=NO_RUN),
        }
        for number in (1, 2)
    ]
    outcome_consistency = metric(scorecard, "outcome_consistency")
    assert outcome_consistency["state"] is None
    assert outcome_consistency["value"] is None


def test_the_configuration_of_an_unsupported_agent_is_refused_before_any_attempt(todo_service):
    target = cloud_target(todo_service, kind="hosted", served={"protocols": ("invocations",)})

    with pytest.raises(UnsupportedCombination, match="serves the protocols \\['invocations'\\] and not the Responses"):
        target.agent_configuration()


def test_an_experiment_can_declare_the_agent_configuration_the_project_reports(todo_service):
    target = cloud_target(todo_service, kind="hosted", agent_version="1")
    experiment = TodoFixtureSweep(todo_service, state=STATE).experiment(
        attempts=1, agent_configuration=target.agent_configuration()
    )

    scorecard = run_sweep(experiments=[experiment], agent_target=target, metrics=["outcome_consistency"])

    assert scorecard["experiments"][0]["agent_configuration"] == {
        "agent_target": "foundry_cloud_evaluation",
        "entry_point": "foundry_cloud_evaluation",
        "project_endpoint": "https://fake.services.ai.azure.com/api/projects/fake",
        "agent": "todo-hosted",
        "kind": "hosted",
        "protocols": ["responses"],
        "pinned_version": "1",
        "latest_version": "1",
    }
    assert scorecard["attempts"][0]["execution_status"] == "completed"


# The Task's success rule in words, which the agent is shown when it is asked for its confidence.
RULE = "The task succeeded only if the five operations were done in order and nothing else was changed."


def rated_sweep(todo_service, script, *, kind="prompt", project=None, **agent):
    """A sweep that scores the Brier-based score, so that the agent is asked for its confidence after each Attempt.

    Returns the scorecard and the fake project. `agent` is what the fake agent is scripted with besides `script`.
    """
    fake_agent = FakeAgent(kind=kind, script=list(script), **agent)
    fake = FakeCloudEvaluationProject(todo_service, {AGENTS[kind]: fake_agent}, **(project or {}))
    target = FoundryCloudEvaluationTarget(project=fake, agent_name=AGENTS[kind], poll_interval_seconds=0)
    experiment = TodoFixtureSweep(todo_service, state=STATE).experiment(
        attempts=len(script), shareable_success_rule=RULE
    )
    scorecard = run_sweep(experiments=[experiment], agent_target=target, metrics=["brier_based_score"])
    return scorecard, fake


def test_a_prompt_agent_invoked_through_cloud_evaluation_is_asked_for_its_confidence_in_a_follow_up(todo_service):
    scorecard, fake = rated_sweep(todo_service, ["completes", "skips the update"], confidence=["80", "30"])

    assert [
        (attempt["task_outcome"]["success"], attempt["confidence"]["rating"], attempt["confidence"]["unavailable_reason"])
        for attempt in scorecard["attempts"]
    ] == [(True, "80", None), (False, "30", None)]
    # (0.8 - 1)^2 = 0.04 and (0.3 - 0)^2 = 0.09. Mean 0.065.
    brier_based_score = metric(scorecard, "brier_based_score")
    assert brier_based_score["state"] == "available"
    assert brier_based_score["value"] == pytest.approx(0.935, abs=1e-12)
    # Each follow-up continued the response that the output item of its Attempt named, and could not act.
    assert [(sent["previous_response_id"], sent["tool_choice"]) for sent in fake.follow_ups] == [
        ("resp_fake_1", "none"),
        ("resp_fake_3", "none"),
    ]


def test_what_the_follow_up_used_is_recorded_with_its_attempt_apart_from_the_run_that_invoked_it(todo_service):
    scorecard, _ = rated_sweep(todo_service, ["completes"], confidence=["87.50"])

    (attempt,) = scorecard["attempts"]
    assert attempt["confidence"] == {
        "rating": "87.50",
        "normalized": 0.875,
        "original_response": "87.50",
        "unavailable_reason": None,
    }
    assert attempt["confidence_invocation"] == {
        "agent_target": "foundry_cloud_evaluation",
        # The follow-up is a request of the customer's own process. Only the Attempt was invoked by Foundry.
        "entry_point": "customer_code",
        "protocol": "responses",
        "api_version": "v1",
        "agent": {"type": "agent_reference", "name": "todo-prompt", "version": "1"},
        "response_id": "resp_fake_2",
        "status": "completed",
        "model": "todo-gpt-41-mini",
        "metadata": {"attempt_id": "todo/baseline/attempt-1", "task_id": "todo", "request": "confidence"},
        "error": None,
        "token_usage": {
            "input_tokens": 800,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 2,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 802,
        },
        "token_usage_missing": None,
        "tool_calls": [],
        "input": attempt["confidence_invocation"]["input"],
        "previous_response_id": "resp_fake_1",
        "tool_choice": "none",
    }
    assert RULE in attempt["confidence_invocation"]["input"]
    assert attempt["invocation"]["entry_point"] == "foundry_cloud_evaluation"
    # Five tool calls in the run, 670 tokens, and 802 for the follow-up: each reported, and added up once.
    (used,) = scorecard["token_usage"]["attempts"]
    assert (used["confidence_follow_up"]["basis"], used["confidence_follow_up"]["total_tokens"]) == ("reported", 802)
    assert used["total_tokens"] == 670 + 802


def test_a_hosted_agent_invoked_through_cloud_evaluation_is_not_asked_for_a_confidence_rating(todo_service):
    scorecard, fake = rated_sweep(todo_service, ["completes", "completes"], kind="hosted")

    reason = (
        "the agent target does not support an isolated follow-up: the code of a Foundry hosted agent decides what "
        "it does with a request, so a caller cannot ensure that the agent does not act while it answers a follow-up"
    )
    unavailable = {"rating": None, "normalized": None, "original_response": None, "unavailable_reason": reason}
    assert [attempt["confidence"] for attempt in scorecard["attempts"]] == [unavailable, unavailable]
    assert [attempt["confidence_invocation"] for attempt in scorecard["attempts"]] == [None, None]
    brier_based_score = metric(scorecard, "brier_based_score")
    assert (brier_based_score["state"], brier_based_score["value"]) == ("unavailable", None)
    # The project was sent no follow-up: a run for each of the two Attempts and nothing else.
    assert fake.follow_ups == []
    assert [attempt["invocation"]["run_id"] for attempt in scorecard["attempts"]] == [
        "evalrun_fake_1",
        "evalrun_fake_2",
    ]


def no_rating_because(scorecard):
    (attempt,) = scorecard["attempts"]
    assert (attempt["confidence"]["rating"], attempt["confidence"]["normalized"]) == (None, None)
    assert metric(scorecard, "brier_based_score")["state"] == "unavailable"
    return attempt["confidence"]["unavailable_reason"]


def test_an_answer_given_after_a_tool_call_in_the_follow_up_is_not_used(todo_service):
    # Never observed: a project that lets the agent act although the follow-up carries the tool choice none.
    scorecard, _ = rated_sweep(
        todo_service, ["completes"], confidence=["90"], acts_on_follow_up=True, project={"follow_up": "ignores tool_choice"}
    )

    assert no_rating_because(scorecard) == (
        "the confidence request failed: InvocationFailed: the agent called a tool while it answered the follow-up "
        "(todos_readTodo). The follow-up was not isolated, so its answer is not used"
    )
    recorded = scorecard["attempts"][0]["confidence_invocation"]
    assert [call["name"] for call in recorded["tool_calls"]] == ["todos_readTodo"]
    assert recorded["token_usage"]["total_tokens"] == 802


def test_an_answer_the_project_does_not_tie_to_the_attempt_or_to_the_tool_choice_none_is_not_used(todo_service):
    apart, _ = rated_sweep(todo_service, ["completes"], confidence=["90"], project={"follow_up": "starts a new conversation"})
    could_act, _ = rated_sweep(todo_service, ["completes"], confidence=["90"], project={"follow_up": "ignores tool_choice"})

    assert no_rating_because(apart) == (
        "the confidence request failed: InvocationFailed: the Foundry project does not report that the follow-up "
        "continued the response resp_fake_1 of the Attempt, so its answer is not used"
    )
    assert no_rating_because(could_act) == (
        "the confidence request failed: InvocationFailed: the Foundry project reports the tool choice 'auto' for the "
        "follow-up and not 'none'. The agent could act, so its answer is not used"
    )


def test_a_follow_up_the_project_refuses_leaves_the_attempt_without_a_rating_and_is_recorded(todo_service):
    scorecard, _ = rated_sweep(todo_service, ["completes"], project={"follow_up": "refuses"})

    reason = no_rating_because(scorecard)
    assert reason.startswith(
        "the confidence request failed: InvocationFailed: the follow-up could not be made in the Foundry project: "
        "POST /openai/v1/responses: HTTP 400: "
    )
    assert "ToolChoice must match the agent's tool_choice" in reason
    recorded = scorecard["attempts"][0]["confidence_invocation"]
    assert (recorded["response_id"], recorded["token_usage"]) == (None, None)
    assert recorded["token_usage_missing"] == "the Foundry project returned no response to the follow-up, so no usage was reported"
    # The Attempt itself completed, and its task outcome stands.
    assert scorecard["attempts"][0]["task_outcome"]["success"] is True


def test_the_target_declares_no_isolated_follow_up_before_the_project_has_said_what_the_agent_is(todo_service):
    target = cloud_target(todo_service)

    before = target.isolated_follow_up
    target.agent_configuration()

    assert (before.supported, before.reason) == (
        False,
        "the type of the agent is not known until the Foundry project was asked about it, and only a prompt agent "
        "is asked a follow-up",
    )
    assert target.isolated_follow_up.supported is True


def test_every_attempt_and_every_retry_is_a_run_of_its_own_with_one_row(todo_service):
    # The first run returns no output item, so the first Attempt failed and is retried. p = 2/2, value 1.
    returns_once = iter([no_item])
    scorecard = sweep(
        todo_service,
        ["completes"] * 3,
        attempts=2,
        limits=ExecutionLimits(max_retries=1),
        project={"alter_item": lambda item: next(returns_once, lambda item: item)(item)},
    )

    attempts = scorecard["attempts"]
    assert [(attempt["attempt_id"], attempt["execution_status"], attempt["retry_of"]) for attempt in attempts] == [
        ("todo/baseline/attempt-1", "retried", None),
        ("todo/baseline/attempt-1/retry-1", "completed", "todo/baseline/attempt-1"),
        ("todo/baseline/attempt-2", "completed", None),
    ]
    # Repetition is the sweep's: three runs in one eval, and each run was given one row and returned at most one item.
    assert [(attempt["invocation"]["eval_id"], attempt["invocation"]["run_id"]) for attempt in attempts] == [
        ("eval_fake_1", "evalrun_fake_1"),
        ("eval_fake_1", "evalrun_fake_2"),
        ("eval_fake_1", "evalrun_fake_3"),
    ]
    counted = [attempt["invocation"]["platform_diagnostics"]["result_counts"]["total"] for attempt in attempts]
    assert counted == [0, 1, 1]
    assert [attempt["invocation"]["metadata"] for attempt in attempts[1:]] == [
        {"attempt_id": "todo/baseline/attempt-1/retry-1", "task_id": "todo"},
        {"attempt_id": "todo/baseline/attempt-2", "task_id": "todo"},
    ]
    assert metric(scorecard, "outcome_consistency")["value"] == 1


def test_a_new_run_for_every_attempt_does_not_reset_what_the_agent_changed(todo_service):
    # Continuing state is declared: the state is prepared once and every Attempt starts from what the
    # one before left. Every Attempt is a run and a conversation of its own, and the agent does the
    # whole Task each time. The first Attempt succeeds. After the second the service holds ten
    # operations instead of the five of the Task, and so on: the other four fail.
    # p = 1/5, (2 * 0.2 - 1)^2 = 0.36. Were each new run a reset, p would be 1 and the value 1.
    scorecard = sweep(todo_service, ["completes"] * 5, starting_state="continuing")

    attempts = scorecard["attempts"]
    assert [attempt["invocation"]["conversation_id"] for attempt in attempts] == [
        f"conv_fake_{number}" for number in (1, 2, 3, 4, 5)
    ]
    assert [attempt["task_outcome"]["success"] for attempt in attempts] == [True, False, False, False, False]
    assert {attempt["starting_state"] for attempt in attempts} == {"continuing"}
    assert metric(scorecard, "outcome_consistency")["value"] == pytest.approx(0.36, abs=1e-12)


def test_the_target_puts_no_starting_state_policy_in_the_place_of_one_that_was_not_declared(todo_service):
    # A Task given without an experiment: no control prepares its state, so both Attempts work on
    # the same state of the service, and the second fails as above. p = 1/2, (2 * 0.5 - 1)^2 = 0.
    todo_service.prepare_attempt(STATE)
    task = TodoFixtureSweep(todo_service, state=STATE).experiment(attempts=2).task

    scorecard = run_sweep(tasks=[task], agent_target=cloud_target(todo_service), metrics=["outcome_consistency"])

    first, second = scorecard["attempts"]
    assert [first["task_outcome"]["success"], second["task_outcome"]["success"]] == [True, False]
    assert [first["starting_state"], second["starting_state"]] == [None, None]
    assert metric(scorecard, "outcome_consistency")["value"] == 0


def test_the_tool_calls_of_the_output_item_can_be_declared_as_the_action_evidence(todo_service):
    # read, create, read, delete, update against read, create, read, update, delete.
    # Sequential: two substitutions, 2 / 5 = 0.4, so 1 - 0.4 = 0.6.
    # Distributional: both Attempts have the same mix of action types, divergence 0, so 1.
    declared = ActionEvidenceDeclaration(
        evidence_source=NATIVE_TOOL_CALLS,
        record=native_tool_calls,
        action_types={"read_todo": "read", "create_todo": "create", "delete_todo": "delete", "update_todo": "update"},
    )

    scorecard = sweep(
        todo_service,
        ["completes", "updates before it deletes"],
        metrics=["sequential_trajectory_consistency", "distributional_trajectory_consistency"],
        kind="hosted",
        action_evidence=declared,
    )

    recorded = scorecard["attempts"][1]["action_evidence"]
    assert recorded["source"] == "tool calls in the output item of the Foundry cloud evaluation run"
    assert recorded["events"][3] == {
        "tool_name": "update_todo",
        "arguments": {"done": True, "attempt_id": STATE, "todo_id": "target"},
        "result": {"status": 200, "body": {**TARGET, "done": True}},
        "error": None,
    }
    assert [event["tool_name"] for event in recorded["events"]] == [
        "read_todo",
        "create_todo",
        "read_todo",
        "update_todo",
        "delete_todo",
    ]
    assert metric(scorecard, "sequential_trajectory_consistency")["value"] == pytest.approx(0.6, abs=1e-12)
    assert metric(scorecard, "distributional_trajectory_consistency")["value"] == pytest.approx(1.0, abs=1e-12)


def test_the_agent_is_sent_the_prompt_exactly_as_it_is_unless_the_customer_composes_the_input(todo_service):
    # The Task's prompt names the state the agent works on. The fake agent finds its state in
    # what it is sent, so the Task can only succeed when the prompt arrived as it is.
    scorecard = sweep(todo_service, ["completes", "completes"])

    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True, True]
    assert [attempt["invocation"]["composed_input"] for attempt in scorecard["attempts"]] == [None, None]


def test_an_input_the_customer_composed_is_what_the_agent_is_sent_and_is_recorded_as_it_was_sent(todo_service):
    # This Task's prompt does not say which state to work on. The customer composes that in front of it.
    todo_service.prepare_attempt(STATE)
    fixture = TodoFixtureSweep(todo_service, state=STATE)
    task = Task(task_id="todo", prompt=TASK_PROMPT, success_rule=fixture.success_rule, attempts=1)
    target = cloud_target(todo_service, compose_input=lambda request: f"Use attempt_id {STATE}. {request.prompt}")

    scorecard = run_sweep(tasks=[task], agent_target=target, metrics=["outcome_consistency"])

    (attempt,) = scorecard["attempts"]
    assert attempt["task_outcome"] == {"success": True, "source": OUTCOME_SOURCE}
    assert attempt["invocation"]["composed_input"] == f"Use attempt_id {STATE}. {TASK_PROMPT}"


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        ({"project": "https://example.invalid/api/projects/p"}, "FoundryProject"),
        ({"agent_name": ""}, "name"),
        ({"agent_version": 1}, "version"),
        ({"run_timeout_seconds": 0}, "run_timeout_seconds"),
        ({"poll_interval_seconds": -1}, "poll_interval_seconds"),
    ],
)
def test_a_target_that_cannot_invoke_an_agent_is_rejected_before_any_attempt_is_made(todo_service, declared, problem):
    with pytest.raises(ValueError, match=problem):
        FoundryCloudEvaluationTarget(
            **{"project": FakeCloudEvaluationProject(todo_service, {}), "agent_name": "todo-prompt", **declared}
        )

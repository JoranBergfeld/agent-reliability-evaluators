"""Live Foundry verification of Foundry-managed scoring of all twelve Evaluators. Opt-in, creates billable Azure resources.

Run with `uv run pytest -m live tests/live/test_managed_twelve_evaluators.py -s`. It provisions a
disposable Foundry project with nothing in it, and tears it down again. Set
ARE_LIVE_PROJECT_GROUP to an already provisioned resource group to reuse it; that group is then
left in place for you to tear down.

The Attempts are synthetic: a scripted agent, whose every Attempt is given by its identity
(`tests/twelve_evaluators_sweep.py`). Only the scoring is live. Expected values are calculated
by hand, there.
"""

import json
import os
import time

import pytest

from live_fixture import teardown
from live_fixture.project_only import connect_project, provision_project
from reliability_evaluators.evidence import ActionEvent, ActionEvidence, ActionTypeMapping, Attempt, group_by_task
from reliability_evaluators.managed_scoring import (
    FoundryManagedScoring,
    FoundryProject,
    FoundryRequestError,
    ManagedFailure,
    azure_cli_token,
    derive_artifact,
)
from reliability_evaluators.managed_scoring.scoring import OBSERVED_REFUSED_RUN_BYTES, SUBMITTED_RUN_LIMIT_BYTES
from tests.fake_foundry import FakeFoundryProject
from tests.twelve_evaluators_sweep import (
    METRIC_IDS,
    NOTHING_TO_CALCULATE_FROM,
    UNAVAILABLE_FOR,
    WORKED,
    WORKED_VALUES,
    Declared,
    managed,
    metric,
)

pytestmark = pytest.mark.live

# What a result is, whoever calculated it.
RESULT = ("state", "value", "reason", "counts", "details", "evidence", "attempts_used", "execution_failure")


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_PROJECT_GROUP")
    provisioned = connect_project(reused) if reused else provision_project(ticket="49")
    print(json.dumps({"project": provisioned.summary()}))
    try:
        yield provisioned
    finally:
        if not reused:
            teardown(provisioned.resource_group)


@pytest.fixture(scope="module")
def scoring(live):
    """Managed scoring as a customer sets it up. A run of some hundred rows takes longer than the default wait."""
    project = FoundryProject(live.project_endpoint, token=azure_cli_token)
    return FoundryManagedScoring(project=project, run_timeout_seconds=1800)


def managed_sweep(script, scoring, **declared):
    started = time.monotonic()
    scorecard = Declared(script, **declared).sweep(
        [managed(metric_id) for metric_id in METRIC_IDS], managed_scoring=scoring
    )
    return scorecard, round(time.monotonic() - started)


def observed(scorecard, seconds):
    return {
        "seconds": seconds,
        "status": scorecard["status"],
        "metrics": {
            entry["metric_id"]: {
                "state": entry["state"],
                "value": entry["value"],
                "reason": entry["reason"],
                "encoded_result": (entry.get("managed_scoring") or {}).get("encoded_result"),
                "error": (entry.get("managed_scoring") or {}).get("error"),
            }
            for entry in scorecard["metrics"]
        },
    }


def test_a_sweep_scores_the_twelve_evaluators_in_the_live_project(scoring):
    in_process = Declared(WORKED).sweep(METRIC_IDS)

    scorecard, seconds = managed_sweep(WORKED, scoring)

    print(json.dumps({"twelve_evaluators": observed(scorecard, seconds)}))
    assert scorecard["status"] == "complete"
    assert {entry["metric_id"]: entry["value"] for entry in scorecard["metrics"]} == pytest.approx(
        WORKED_VALUES, abs=1e-12
    )
    assert {(entry["scoring_path"], entry["fidelity"]) for entry in scorecard["metrics"]} == {
        ("foundry_managed", "unverified")
    }
    # Each Evaluator was scored in an evaluation of its own, with the evaluator derived from its definition.
    assert len({entry["managed_scoring"]["eval_id"] for entry in scorecard["metrics"]}) == 12
    for metric_id in METRIC_IDS:
        scored, authoritative = metric(scorecard, metric_id), metric(in_process, metric_id)
        assert scored["managed_scoring"]["evaluator"]["code_sha256"] == derive_artifact(metric_id).sha256
        # What Foundry returned is what this process calculates from the same Attempts: the state, the
        # counts, the details and the Attempts the value rests on, beside the value.
        assert {name: scored[name] for name in RESULT} == {name: authoritative[name] for name in RESULT}, metric_id


def test_every_evaluator_returns_an_unavailable_measurement_from_the_live_project(scoring):
    in_process = Declared(NOTHING_TO_CALCULATE_FROM, assessed=False).sweep(METRIC_IDS)

    scorecard, seconds = managed_sweep(NOTHING_TO_CALCULATE_FROM, scoring, assessed=False)

    print(json.dumps({"twelve_unavailable_measurements": observed(scorecard, seconds)}))
    # No evaluator raised and no run failed: the scorecard is complete and no metric has a number.
    assert scorecard["status"] == "complete"
    assert {entry["metric_id"]: (entry["state"], entry["value"]) for entry in scorecard["metrics"]} == dict.fromkeys(
        METRIC_IDS, ("unavailable", None)
    )
    assert {entry["metric_id"]: entry["reason"] for entry in scorecard["metrics"]} == UNAVAILABLE_FOR
    for metric_id in METRIC_IDS:
        scored, authoritative = metric(scorecard, metric_id), metric(in_process, metric_id)
        assert {name: scored[name] for name in RESULT} == {name: authoritative[name] for name in RESULT}, metric_id
        assert "platform_diagnostics" not in scored["managed_scoring"]


def wait_for(live, eval_id, run, seconds=1200):
    path = f"/openai/v1/evals/{eval_id}/runs/{run['id']}"
    deadline = time.monotonic() + seconds
    while run["status"] not in ("completed", "failed", "canceled") and time.monotonic() < deadline:
        time.sleep(10)
        run = live.project_request("GET", path)
    return run


def submitted_evidence_of_the_worked_sweep():
    """The evidence field of each Evaluator, as a sweep submits it. A fake project is handed it here, to keep it."""
    kept = FakeFoundryProject()
    managed_sweep(WORKED, FoundryManagedScoring(project=kept, poll_interval_seconds=0))
    return {field: text for row in kept.submitted_items for field, text in row.items() if field.endswith("_evidence")}


def test_the_twelve_derived_evaluators_return_their_values_as_twelve_criteria_of_one_run(live, scoring):
    # The sweep gives every Evaluator runs of its own. This is the other arrangement: one evaluation with
    # twelve criteria, and one run whose every row carries the evidence of all twelve.
    evidence = submitted_evidence_of_the_worked_sweep()
    assert sorted(evidence) == sorted(f"{metric_id}_evidence" for metric_id in METRIC_IDS)
    registered = scoring.register(METRIC_IDS)
    eval_id = live.project_request("POST", "/openai/v1/evals", {
        "name": "Reliability Evaluators live check: twelve criteria",
        "data_source_config": {
            "type": "custom",
            "item_schema": {"type": "object", "properties": {field: {"type": "string"} for field in (*evidence, "result_part")}},
            "include_sample_schema": True,
        },
        "testing_criteria": [{
            "type": "azure_ai_evaluator", "name": metric_id, "evaluator_name": registered[metric_id].name,
            "evaluator_version": registered[metric_id].version, "initialization_parameters": {},
        } for metric_id in METRIC_IDS],
    })["id"]
    started = time.monotonic()

    run = wait_for(live, eval_id, live.project_request("POST", f"/openai/v1/evals/{eval_id}/runs", {
        "name": "twelve criteria",
        "data_source": {"type": "jsonl", "source": {"type": "file_content", "content": [
            {"item": {**evidence, "result_part": part}} for part in ("value", "size")
        ]}},
    }))

    items = live.project_request("GET", f"/openai/v1/evals/{eval_id}/runs/{run['id']}/output_items?limit=100")["data"]
    by_part = {item["datasource_item"]["result_part"]: {result["name"]: result for result in item["results"]} for item in items}
    print(json.dumps({"twelve_criteria_of_one_run": {
        "seconds": round(time.monotonic() - started), "status": run["status"], "result_counts": run.get("result_counts"),
        "values": {name: result.get("score") for name, result in by_part.get("value", {}).items()},
        "sizes": {name: result.get("score") for name, result in by_part.get("size", {}).items()},
    }}))
    assert run["status"] == "completed", run.get("error")
    assert sorted(by_part) == ["size", "value"]
    assert {result["status"] for results in by_part.values() for result in results.values()} == {"completed"}
    assert {name: result["score"] for name, result in by_part["value"].items()} == pytest.approx(WORKED_VALUES, abs=1e-12)


def run_request_of(evidence_field, bytes_of_request):
    """A request for a run of one row, which is that many bytes when it is written without whitespace."""
    def request(filler):
        return {
            "name": "size of a run",
            "data_source": {"type": "jsonl", "source": {"type": "file_content", "content": [
                {"item": {evidence_field: "a" * filler, "result_part": "value"}}
            ]}},
        }

    empty = len(json.dumps(request(0), separators=(",", ":")).encode("utf-8"))
    return request(bytes_of_request - empty)


def test_the_project_accepts_a_run_of_the_size_this_version_submits_and_refuses_a_larger_one(live, scoring):
    artifact = derive_artifact("outcome_consistency")
    version = scoring.register(["outcome_consistency"])["outcome_consistency"].version
    eval_id = live.project_request("POST", "/openai/v1/evals", {
        "name": "Reliability Evaluators live check: size of a run",
        "data_source_config": {
            "type": "custom",
            "item_schema": {"type": "object", "properties": {field: {"type": "string"} for field in (artifact.evidence_field, "result_part")}},
            "include_sample_schema": True,
        },
        "testing_criteria": [{
            "type": "azure_ai_evaluator", "name": "outcome_consistency", "evaluator_name": artifact.evaluator_name,
            "evaluator_version": version, "initialization_parameters": {},
        }],
    })["id"]
    project = FoundryProject(live.project_endpoint, token=azure_cli_token)

    def made(bytes_of_request):
        """Whether the project makes a run of that size. The run holds no evidence and is cancelled at once."""
        try:
            run = project.request(
                "POST", f"/openai/v1/evals/{eval_id}/runs", run_request_of(artifact.evidence_field, bytes_of_request)
            )
        except FoundryRequestError as refused:
            # The answer names the subscription, so only its status is kept.
            return refused.status
        try:
            project.request("POST", f"/openai/v1/evals/{eval_id}/runs/{run['id']}/cancel")
        except FoundryRequestError:
            pass
        return "made"

    answers = {size: made(size) for size in (SUBMITTED_RUN_LIMIT_BYTES, OBSERVED_REFUSED_RUN_BYTES + 100_000)}

    print(json.dumps({"size_of_a_run": {str(size): answer for size, answer in answers.items()}}))
    assert answers == {SUBMITTED_RUN_LIMIT_BYTES: "made", OBSERVED_REFUSED_RUN_BYTES + 100_000: 413}


TOOLS = ("get_item", "list_items", "create_item", "update_item", "delete_item")
EVERY_TOOL_ITS_OWN_ACTION = ActionTypeMapping(
    evidence_source="native tool calls", action_types={name: name for name in TOOLS}
)


def long_trajectories(attempts, actions):
    """Attempts at one Task that each took many actions. Comparing every pair of them in order takes long."""
    return group_by_task(
        Attempt(
            attempt_id=f"task/attempt-{number}",
            task_id="task",
            execution_status="completed",
            action_evidence=ActionEvidence(
                source="native tool calls",
                events=[
                    ActionEvent(tool_name=TOOLS[(number * 3 + step * (1 + number % 4) + step // 7) % 5])
                    for step in range(actions)
                ],
            ),
        )
        for number in range(1, attempts + 1)
    )


def test_a_metric_whose_calculation_takes_longer_than_one_call_may_is_a_failure_that_names_the_limit(scoring):
    # 80 Attempts of 600 actions at one Task: 3,160 pairs, each an edit distance over 360,000 cells. A fifth
    # of a second went into a pair of 200 actions in the sandbox when observed, so one call takes minutes,
    # and every row of a run makes that call.
    evidence = long_trajectories(attempts=80, actions=600)
    started = time.monotonic()

    returned = scoring.score(
        {"sequential_trajectory_consistency": evidence},
        {"sequential_trajectory_consistency": (EVERY_TOOL_ITS_OWN_ACTION,)},
    )["sequential_trajectory_consistency"]

    print(json.dumps({"call_that_takes_too_long": {
        "seconds": round(time.monotonic() - started), "failure": getattr(returned, "error", None),
    }}))
    # Nothing was returned for the metric, and nothing was reported in its place.
    assert isinstance(returned, ManagedFailure)
    assert "Foundry does not grade a row whose call takes too long" in returned.error

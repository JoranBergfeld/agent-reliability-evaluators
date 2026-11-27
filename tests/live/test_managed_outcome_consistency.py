"""Live Foundry verification of Foundry-managed scoring of outcome consistency. Opt-in, creates billable Azure resources.

Run with `uv run pytest -m live tests/live/test_managed_outcome_consistency.py -s`. It provisions
a disposable Foundry project with nothing in it, and tears it down again. Set
ARE_LIVE_PROJECT_GROUP to an already provisioned resource group to reuse it; that group is then
left in place for you to tear down.

The Attempts are synthetic: a scripted agent works on the fixture's to-do service on this
machine, and the fixture's strict success rule decides each task outcome from that service.
Only the scoring is live. Expected values are calculated by hand.
"""

import base64
import json
import os
import time

import pytest

from live_fixture import TASK_PROMPT, local_service, strict_task_outcome, teardown
from live_fixture.hosted_agent import todo_tools
from live_fixture.project_only import connect_project, provision_project
from reliability_evaluators.agent_target import AgentResponse
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.evaluators import outcome_consistency
from reliability_evaluators.evidence import Attempt, ExecutionStatus, group_by_task
from reliability_evaluators.managed_scoring import (
    FoundryManagedScoring,
    FoundryProject,
    azure_cli_token,
    derive_artifact,
)
from reliability_evaluators.managed_scoring.grader import decode_result, result_parts
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, run_sweep

pytestmark = pytest.mark.live

MANAGED = ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")
EVALUATOR_NAME = "reliability-outcome-consistency"


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_PROJECT_GROUP")
    provisioned = connect_project(reused) if reused else provision_project(ticket="46")
    print(json.dumps({"project": provisioned.summary()}))
    try:
        yield provisioned
    finally:
        if not reused:
            teardown(provisioned.resource_group)


@pytest.fixture(scope="module")
def scoring(live):
    """Managed scoring as a customer sets it up: the project endpoint and the Azure CLI's identity."""
    return FoundryManagedScoring(project=FoundryProject(live.project_endpoint, token=azure_cli_token))


class ScriptedTodoAgent:
    """A synthetic agent on the fixture's to-do service: it does the Task, or leaves out its last step.

    It always answers "Done.", so only the service says what happened.
    """

    def __init__(self, service, script):
        self._service = service
        self._remaining = {task_id: list(behaviours) for task_id, behaviours in script.items()}

    def invoke(self, request):
        attempt_id = service_attempt(request.attempt_id)
        self._service.prepare_attempt(attempt_id)
        todo_tools.read_todo(attempt_id, "target")
        todo_tools.create_todo(attempt_id, "scratch", "Temporary")
        todo_tools.read_todo(attempt_id, "scratch")
        todo_tools.delete_todo(attempt_id, "scratch")
        if self._remaining[request.task_id].pop(0) == "success":
            todo_tools.update_todo(attempt_id, "target", done=True)
        return AgentResponse(output="Done.")


def service_attempt(attempt_id):
    # The service takes the Attempt identifier in a path, so it cannot hold a slash.
    return attempt_id.replace("/", "-")


def sweep_on_the_fixture(script, scoring, monkeypatch, **declared):
    with local_service() as service:
        monkeypatch.setenv("TODO_SERVICE_URL", service.url)
        monkeypatch.setenv("TODO_API_KEY", service.api_key)

        def strict_success(attempt, response):
            outcome = strict_task_outcome(service.evidence(service_attempt(attempt.attempt_id)))
            return TaskOutcome(success=outcome["success"], source=outcome["source"])

        tasks = [
            Task(task_id=task_id, prompt=TASK_PROMPT, success_rule=strict_success, attempts=len(behaviours))
            for task_id, behaviours in script.items()
        ]
        return run_sweep(
            tasks=tasks,
            agent_target=ScriptedTodoAgent(service, script),
            metrics=[MANAGED],
            managed_scoring=scoring,
            **declared,
        )


def versions_with_the_derived_code(live):
    """The registered versions of the evaluator that hold exactly the derived code, read from the project."""
    code_text = derive_artifact("outcome_consistency").code_text
    held, path = [], f"/evaluators/{EVALUATOR_NAME}/versions?api-version=v1"
    while path:
        page = live.project_request("GET", path.removeprefix(live.project_endpoint))
        held += [version for version in page["value"] if version["definition"]["code_text"] == code_text]
        path = page["nextLink"]
    return held


def signed_in_name(live):
    payload = live.access_token().split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("name")


def test_a_sweep_scores_outcome_consistency_in_the_live_project(live, scoring, monkeypatch):
    # close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Outcome consistency: (0.36 + 1) / 2 = 0.68.
    script = {"close": ["success"] * 4 + ["failure"], "archive": ["failure"] * 3}
    started = time.monotonic()

    scorecard = sweep_on_the_fixture(script, scoring, monkeypatch)

    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == "outcome_consistency"]
    print(json.dumps({"seconds": round(time.monotonic() - started), "outcome_consistency": {
        key: entry[key] for key in ("state", "value", "scoring_path", "fidelity", "counts", "managed_scoring")}}))
    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True] * 4 + [False] * 4
    assert entry["state"] == "available"
    assert entry["value"] == pytest.approx(0.68, abs=1e-12)
    assert entry["scoring_path"] == "foundry_managed"
    # The number Foundry returned as the score is the value the evaluator encoded: the fidelity check passed.
    assert entry["fidelity"] == "authoritative"
    assert "limitation" not in entry
    check = entry["managed_scoring"]["fidelity_check"]
    assert (check["outcome"], check["plain_value"], check["encoded_value"]) == ("passed", entry["value"], entry["value"])
    assert entry["managed_scoring"]["evaluator"]["name"] == EVALUATOR_NAME
    assert entry["managed_scoring"]["evaluator"]["code_sha256"] == derive_artifact("outcome_consistency").sha256
    # Foundry's own default threshold and label, kept as diagnostics. The customer declared no criterion.
    diagnostics = entry["managed_scoring"]["platform_diagnostics"]
    assert (diagnostics["threshold"], diagnostics["label"], diagnostics["passed"]) == (0.5, "pass", True)
    assert entry["acceptance"] is None
    # The counts, the per-Task details and the Attempts the value rests on came back with it, as an encoded result.
    assert entry["counts"] == {
        "tasks": {"total": 2, "valid": 2, "missing": [], "excluded": []},
        "attempts": {"total": 8, "valid": 8, "missing": [], "excluded": []},
    }
    assert entry["details"] == {
        "per_task": [
            {"task_id": "close", "attempts": 5, "successes": 4, "value": 0.36},
            {"task_id": "archive", "attempts": 3, "successes": 0, "value": 1.0},
        ]
    }
    assert entry["evidence"]["attempt_ids"] == [attempt["attempt_id"] for attempt in scorecard["attempts"]]
    encoded = entry["managed_scoring"]["encoded_result"]
    assert encoded["run_ids"] and entry["managed_scoring"]["run_id"] not in encoded["run_ids"]
    assert encoded["parts"] == -(-encoded["bytes"] // 6)


def test_registration_is_idempotent_and_made_as_the_signed_in_identity(live, scoring):
    first = scoring.register(["outcome_consistency"])["outcome_consistency"]
    second = scoring.register(["outcome_consistency"])["outcome_consistency"]

    held = versions_with_the_derived_code(live)
    print(json.dumps({"registered_version": first.version, "versions_with_the_derived_code": len(held)}))
    assert second.version == first.version
    assert second.newly_registered is False
    assert [version["version"] for version in held] == [first.version]
    assert held[0]["created_by"] == signed_in_name(live)


def test_an_unavailable_measurement_comes_back_from_the_live_project_with_its_reason_and_counts(
    live, scoring, monkeypatch
):
    # One Attempt at each Task: no Task has two, so outcome consistency is an unavailable measurement.
    started = time.monotonic()

    scorecard = sweep_on_the_fixture({"close": ["success"], "archive": ["failure"]}, scoring, monkeypatch)

    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == "outcome_consistency"]
    print(json.dumps({"seconds": round(time.monotonic() - started), "unavailable_measurement": {
        key: entry[key] for key in ("state", "value", "reason", "counts", "scoring_path", "fidelity", "managed_scoring")}}))
    # The evaluator did not raise and no run failed: the scorecard is complete and says what the evaluator found.
    assert scorecard["status"] == "complete"
    assert (entry["state"], entry["value"]) == ("unavailable", None)
    assert entry["reason"] == "no Task has at least two Attempts with a task outcome"
    assert entry["counts"] == {
        "tasks": {
            "total": 2,
            "valid": 0,
            "missing": [],
            "excluded": [
                {"reason": "fewer than two Attempts with a task outcome", "count": 2, "ids": ["close", "archive"]}
            ],
        },
        "attempts": {
            "total": 2,
            "valid": 0,
            "missing": [],
            "excluded": [
                {
                    "reason": "its Task has fewer than two Attempts with a task outcome",
                    "count": 2,
                    "ids": ["close/attempt-1", "archive/attempt-1"],
                }
            ],
        },
    }
    assert entry["execution_failure"] is None
    # Both channels agree that there is no value, so the result is authoritative as it stands.
    assert (entry["scoring_path"], entry["fidelity"]) == ("foundry_managed", "authoritative")
    assert "limitation" not in entry
    check = entry["managed_scoring"]["fidelity_check"]
    assert (check["outcome"], check["plain_value"], check["encoded_value"]) == ("passed", None, None)
    # The number Foundry was handed in place of a value, and its label for it, are not in the scorecard.
    assert "platform_diagnostics" not in entry["managed_scoring"]
    assert "error" not in entry["managed_scoring"]


def synthetic_evidence(outcomes_by_task):
    return group_by_task(
        Attempt(
            attempt_id=f"{task_id}/attempt-{number}",
            task_id=task_id,
            execution_status=ExecutionStatus.COMPLETED,
            task_outcome=TaskOutcome(success=success, source="synthetic live check"),
        )
        for task_id, outcomes in outcomes_by_task.items()
        for number, success in enumerate(outcomes, start=1)
    )


def run_rows(live, eval_id, rows):
    """Submit rows to one run of the eval, wait for it, and return the run and its output items by row."""
    run = live.project_request(
        "POST",
        f"/openai/v1/evals/{eval_id}/runs",
        {"name": "mixed results", "data_source": {"type": "jsonl", "source": {"type": "file_content", "content": [{"item": row} for row in rows]}}},
    )
    path = f"/openai/v1/evals/{eval_id}/runs/{run['id']}"
    deadline = time.monotonic() + 1200
    while run["status"] not in ("completed", "failed", "canceled") and time.monotonic() < deadline:
        time.sleep(10)
        run = live.project_request("GET", path)
    items, after = [], ""
    while True:
        page = live.project_request("GET", f"{path}/output_items?limit=100{after}")
        items += page["data"]
        if not page["has_more"]:
            break
        after = f"&after={page['last_id']}"
    return run, {item["datasource_item_id"]: item for item in items}


def test_a_run_that_mixes_an_available_and_an_unavailable_result_returns_every_item(live, scoring):
    # Two sets of evidence in the same runs. "scored": p = 4/5, (0.6)^2 = 0.36. "unscored": one Attempt, no value.
    artifact = derive_artifact("outcome_consistency")
    version = scoring.register(["outcome_consistency"])["outcome_consistency"].version
    evidence = {
        "scored": artifact.item(synthetic_evidence({"task": [True, True, True, True, False]})),
        "unscored": artifact.item(synthetic_evidence({"task": [True]})),
    }
    eval_id = live.project_request("POST", "/openai/v1/evals", {
        "name": "Reliability Evaluators live check: mixed results",
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
    started = time.monotonic()

    def scores(rows):
        """Ask for parts of the two results in one run. Every row has to come back completed, with a number."""
        run, items = run_rows(live, eval_id, [{**evidence[name], "result_part": part} for name, part in rows])
        assert run["status"] == "completed", run.get("error")
        assert sorted(items) == list(range(len(rows)))
        results = [items[row]["results"][0] for row in range(len(rows))]
        assert [result["status"] for result in results] == ["completed"] * len(rows)
        return run, dict(zip(rows, (result["score"] for result in results)))

    first, plain = scores([(name, part) for name in evidence for part in ("value", "size")])
    asked_for = [
        (name, str(number)) for name in evidence for number in range(1, result_parts(plain[(name, "size")]) + 1)
    ]
    second, parts = scores(asked_for)

    results = {
        name: decode_result(
            outcome_consistency.DEFINITION,
            evidence[name][artifact.evidence_field],
            plain[(name, "size")],
            [parts[row] for row in asked_for if row[0] == name],
        )
        for name in evidence
    }
    print(json.dumps({"seconds": round(time.monotonic() - started), "mixed_runs": {
        "first": {"rows": len(plain), "result_counts": first["result_counts"], "scores": {"/".join(row): score for row, score in plain.items()}},
        "second": {"rows": len(parts), "result_counts": second["result_counts"]},
        "results": {name: {"state": result.state.value, "value": result.value, "reason": result.reason} for name, result in results.items()},
    }}))
    # The available result has its value in both forms, and the unavailable one a marker below the scale and its reason.
    assert plain[("scored", "value")] == pytest.approx(0.36, abs=1e-12)
    assert (results["scored"].state.value, results["scored"].value) == ("available", plain[("scored", "value")])
    assert plain[("unscored", "value")] == -1
    assert (results["unscored"].state.value, results["unscored"].value) == ("unavailable", None)
    assert results["unscored"].reason == "no Task has at least two Attempts with a task outcome"
    assert results["unscored"].counts["attempts"].to_dict() == {
        "total": 1,
        "valid": 0,
        "missing": [],
        "excluded": [
            {"reason": "its Task has fewer than two Attempts with a task outcome", "count": 1, "ids": ["task/attempt-1"]}
        ],
    }

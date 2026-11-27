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
from reliability_evaluators.managed_scoring import (
    FoundryManagedScoring,
    FoundryProject,
    azure_cli_token,
    derive_artifact,
)
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


def sweep_on_the_fixture(script, scoring, monkeypatch):
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
            tasks=tasks, agent_target=ScriptedTodoAgent(service, script), metrics=[MANAGED], managed_scoring=scoring
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
        key: entry[key] for key in ("state", "value", "scoring_path", "fidelity", "managed_scoring")}}))
    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True] * 4 + [False] * 4
    assert entry["state"] == "available"
    assert entry["value"] == pytest.approx(0.68, abs=1e-12)
    assert entry["scoring_path"] == "foundry_managed"
    assert entry["fidelity"] == "unverified"
    assert "has not been established" in entry["limitation"]
    assert entry["managed_scoring"]["evaluator"]["name"] == EVALUATOR_NAME
    assert entry["managed_scoring"]["evaluator"]["code_sha256"] == derive_artifact("outcome_consistency").sha256
    # Foundry's own default threshold and label, kept as diagnostics. The customer declared no criterion.
    diagnostics = entry["managed_scoring"]["platform_diagnostics"]
    assert (diagnostics["threshold"], diagnostics["label"], diagnostics["passed"]) == (0.5, "pass", True)
    assert entry["acceptance"] is None


def test_registration_is_idempotent_and_made_as_the_signed_in_identity(live, scoring):
    first = scoring.register(["outcome_consistency"])["outcome_consistency"]
    second = scoring.register(["outcome_consistency"])["outcome_consistency"]

    held = versions_with_the_derived_code(live)
    print(json.dumps({"registered_version": first.version, "versions_with_the_derived_code": len(held)}))
    assert second.version == first.version
    assert second.newly_registered is False
    assert [version["version"] for version in held] == [first.version]
    assert held[0]["created_by"] == signed_in_name(live)


def test_an_unavailable_measurement_gives_no_managed_result_and_a_partial_scorecard(live, scoring, monkeypatch):
    # One Attempt at each Task: no Task has two, so outcome consistency is an unavailable measurement.
    scorecard = sweep_on_the_fixture({"close": ["success"], "archive": ["failure"]}, scoring, monkeypatch)

    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == "outcome_consistency"]
    print(json.dumps({"managed_scoring_failure": entry["managed_scoring"]["error"]}))
    # Nothing came back that says the measurement is unavailable, so the scorecard does not say it either.
    assert (entry["state"], entry["value"]) == (None, None)
    assert "ended as failed" in entry["managed_scoring"]["error"]
    assert scorecard["status"] == "partial"

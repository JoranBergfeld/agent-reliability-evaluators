"""Live Foundry verification of a failed Foundry-managed run, and of the customer-process rescore.

Opt-in, creates billable Azure resources. Run with
`uv run pytest -m live tests/live/test_managed_scoring_failures.py -s`. It provisions a disposable
Foundry project with nothing in it, and tears it down again. Set ARE_LIVE_PROJECT_GROUP to an
already provisioned resource group to reuse it; that group is then left in place for you to tear down.

The managed run is made to fail by a test-only evaluator that raises. It is registered in the
disposable project under a name of its own, and the project is told to run it where the sweep
asks for the derived evaluator. The canonical definition and the derived evaluator are not
touched. The test-only evaluator goes when the resource group is deleted.

The Attempts are synthetic, as in the other live check of managed scoring. Expected values are
calculated by hand.
"""

import itertools
import json
import os

import pytest

from live_fixture import TASK_PROMPT, local_service, strict_task_outcome, teardown
from live_fixture.project_only import connect_project, provision_project
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring, FoundryProject, azure_cli_token, derive_artifact
from reliability_evaluators.managed_scoring.artifact import MANAGED_ATTEMPT_EVIDENCE
from reliability_evaluators.scorecard import to_json
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, rescore_in_customer_process, run_sweep
from tests.live.test_managed_outcome_consistency import ScriptedTodoAgent, service_attempt

pytestmark = pytest.mark.live

MANAGED = ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")
FAILING_EVALUATOR = "are-test-only-raises"
FAILING_CODE = (
    "def grade(sample, item):\n"
    "    raise ValueError('a test-only evaluator that fails on purpose')\n"
)
# close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Outcome consistency: (0.36 + 1) / 2 = 0.68.
SCRIPT = {"close": ["success"] * 4 + ["failure"], "archive": ["failure"] * 3}


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_PROJECT_GROUP")
    provisioned = connect_project(reused) if reused else provision_project(ticket="50")
    print(json.dumps({"project": provisioned.summary()}))
    try:
        yield provisioned
    finally:
        if not reused:
            teardown(provisioned.resource_group)


@pytest.fixture(scope="module")
def project(live):
    """The project as a customer reaches it: its endpoint and the Azure CLI's identity."""
    return FoundryProject(live.project_endpoint, token=azure_cli_token)


@pytest.fixture(scope="module")
def failing_evaluator_version(live):
    """Register the test-only evaluator that raises, and say which version it is."""
    created = live.project_request(
        "POST",
        f"/evaluators/{FAILING_EVALUATOR}/versions?api-version=v1",
        {
            "name": FAILING_EVALUATOR,
            "evaluator_type": "custom",
            "categories": ["quality"],
            "display_name": "Test-only evaluator that raises",
            "description": "Registered by a live check to make a managed run fail. Not a reliability Evaluator.",
            "definition": {
                "type": "code",
                "code_text": FAILING_CODE,
                "init_parameters": {"type": "object", "properties": {}, "required": []},
                "data_schema": {"type": "object", "required": ["item"], "properties": {"item": {"type": "object"}}},
                "metrics": {
                    "result": {"type": "continuous", "desirable_direction": "neutral", "min_value": 0, "max_value": 1}
                },
            },
        },
        {"Foundry-Features": "Evaluations=V1Preview"},
    )
    return str(created["version"])


class RunsTheFailingEvaluator:
    """The live project, told to run the test-only evaluator where an evaluation names the derived one of a metric.

    Everything else reaches the project as it is: the registration of the derived evaluator,
    the submitted evidence, the run and what the project returns for it.
    """

    def __init__(self, project, version, for_metric="outcome_consistency"):
        self._project = project
        self._version = version
        self._for_metric = for_metric

    def request(self, method, path, body=None, headers=None):
        if (method, path) == ("POST", "/openai/v1/evals"):
            criteria = [
                {**criterion, "evaluator_name": FAILING_EVALUATOR, "evaluator_version": self._version}
                if criterion["name"] == self._for_metric
                else criterion
                for criterion in body["testing_criteria"]
            ]
            body = {**body, "testing_criteria": criteria}
        return self._project.request(method, path, body, headers)


def sweep_on_the_fixture(project, monkeypatch, metrics=(MANAGED, "resource_consistency"), **declared):
    """Sweep the synthetic Attempts. Each takes one tick of the sweep's clock, so that its elapsed time is known."""
    with local_service() as service:
        monkeypatch.setenv("TODO_SERVICE_URL", service.url)
        monkeypatch.setenv("TODO_API_KEY", service.api_key)

        def strict_success(attempt, response):
            outcome = strict_task_outcome(service.evidence(service_attempt(attempt.attempt_id)))
            return TaskOutcome(success=outcome["success"], source=outcome["source"])

        tasks = [
            Task(task_id=task_id, prompt=TASK_PROMPT, success_rule=strict_success, attempts=len(behaviours))
            for task_id, behaviours in SCRIPT.items()
        ]
        ticks = itertools.count()
        return run_sweep(
            tasks=tasks,
            agent_target=ScriptedTodoAgent(service, SCRIPT),
            metrics=metrics,
            managed_scoring=FoundryManagedScoring(project=project),
            clock=lambda: float(next(ticks)),
            **declared,
        )


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def test_a_managed_run_whose_evaluator_fails_leaves_a_partial_scorecard_and_can_be_rescored_on_request(
    live, project, failing_evaluator_version, monkeypatch, tmp_path
):
    failing = RunsTheFailingEvaluator(project, failing_evaluator_version)

    scorecard = sweep_on_the_fixture(failing, monkeypatch, artifact_dir=tmp_path)

    outcome_consistency = metric(scorecard)
    print(json.dumps({"failed_managed_run": {
        key: outcome_consistency[key] for key in ("state", "value", "scoring_path", "fidelity", "execution_failure", "managed_scoring")
    }, "status": scorecard["status"], "missing": scorecard["missing"]}))
    # The managed result is missing, with the execution error of the live run. It is no state and no number.
    assert scorecard["status"] == "partial"
    assert (outcome_consistency["state"], outcome_consistency["value"]) == (None, None)
    assert outcome_consistency["scoring_path"] == "foundry_managed"
    assert "ended as failed" in outcome_consistency["managed_scoring"]["error"]
    assert outcome_consistency["managed_scoring"]["run_id"]
    assert [(entry["kind"], entry["metric_id"]) for entry in scorecard["missing"]] == [
        ("metric_result", "outcome_consistency")
    ]
    assert outcome_consistency["managed_scoring"]["error"] in scorecard["missing"][0]["reason"]
    # The evidence is kept: no task outcome was changed by the failure.
    assert [attempt["task_outcome"]["success"] for attempt in scorecard["attempts"]] == [True] * 4 + [False] * 4
    # The metric scored in this process keeps its result. Every Attempt took one tick, so every
    # coefficient of variation is 0 and resource consistency is exp(0) = 1.
    resource_consistency = metric(scorecard, "resource_consistency")
    assert (resource_consistency["state"], resource_consistency["value"]) == ("available", 1.0)
    assert resource_consistency["scoring_path"] == "customer_process"
    # The partial scorecard was delivered, with its report and its evidence.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["evidence.json", "report.html", "scorecard.json"]
    delivered = json.loads((tmp_path / "scorecard.json").read_text(encoding="utf-8"))
    assert delivered == json.loads(to_json(scorecard))
    assert "customer_process_rescore" not in metric(delivered)

    rescored = rescore_in_customer_process(earlier_scorecard=delivered, metrics=["outcome_consistency"])

    rescore = metric(rescored)["customer_process_rescore"]
    print(json.dumps({"rescore": {key: rescore[key] for key in ("state", "value", "scoring_path", "fidelity", "evidence_sha256")}}))
    assert rescore["value"] == pytest.approx(0.68, abs=1e-12)
    assert (rescore["scoring_path"], rescore["fidelity"]) == ("customer_process", "authoritative")
    # It is calculated from the evidence that was submitted to the live project.
    assert rescore["evidence_sha256"] == metric(delivered)["managed_scoring"]["evidence_sha256"]
    # The managed failure is still there, and the scorecard is still partial.
    assert {key: value for key, value in metric(rescored).items() if key != "customer_process_rescore"} == metric(delivered)
    assert (rescored["status"], rescored["missing"]) == ("partial", delivered["missing"])


def test_a_rescore_stands_beside_the_unverified_value_that_the_live_project_returned(live, project, monkeypatch):
    scorecard = json.loads(to_json(sweep_on_the_fixture(project, monkeypatch)))

    rescored = rescore_in_customer_process(earlier_scorecard=scorecard, metrics=["outcome_consistency"])

    outcome_consistency = metric(rescored)
    rescore = outcome_consistency["customer_process_rescore"]
    print(json.dumps({"managed": {key: outcome_consistency[key] for key in ("value", "scoring_path", "fidelity")},
                      "rescore": {key: rescore[key] for key in ("value", "scoring_path", "fidelity")}}))
    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)
    assert (outcome_consistency["scoring_path"], outcome_consistency["fidelity"]) == ("foundry_managed", "unverified")
    assert "has not been established" in outcome_consistency["limitation"]
    assert rescore["value"] == pytest.approx(0.68, abs=1e-12)
    assert (rescore["scoring_path"], rescore["fidelity"]) == ("customer_process", "authoritative")
    assert rescore["evidence_sha256"] == outcome_consistency["managed_scoring"]["evidence_sha256"]
    assert rescored["status"] == "complete"


def test_one_managed_run_that_fails_does_not_take_the_result_of_another_managed_run(
    live, project, failing_evaluator_version, monkeypatch
):
    # Only outcome consistency has a Foundry evaluator in this version. A second Evaluator on the
    # managed path is needed to show two managed runs of one sweep, so the Attempt evidence of
    # resource consistency is declared for this check. Its derived evaluator is registered and is
    # not run: the test-only evaluator that raises is run in its place.
    monkeypatch.setitem(MANAGED_ATTEMPT_EVIDENCE, "resource_consistency", ("resource_measurements",))
    derive_artifact.cache_clear()
    failing = RunsTheFailingEvaluator(project, failing_evaluator_version, for_metric="resource_consistency")
    both_managed = [MANAGED, ConfiguredEvaluator(metric_id="resource_consistency", scoring_path="foundry_managed")]

    try:
        scorecard = sweep_on_the_fixture(failing, monkeypatch, metrics=both_managed)
    finally:
        derive_artifact.cache_clear()

    outcome_consistency, resource_consistency = metric(scorecard), metric(scorecard, "resource_consistency")
    print(json.dumps({"kept": {key: outcome_consistency[key] for key in ("state", "value", "scoring_path", "fidelity")},
                      "kept_run": outcome_consistency["managed_scoring"]["run_id"],
                      "failed": resource_consistency["managed_scoring"]}))
    assert outcome_consistency["state"] == "available"
    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)
    assert (outcome_consistency["scoring_path"], outcome_consistency["fidelity"]) == ("foundry_managed", "unverified")
    assert (resource_consistency["state"], resource_consistency["value"]) == (None, None)
    assert "ended as failed" in resource_consistency["managed_scoring"]["error"]
    assert outcome_consistency["managed_scoring"]["run_id"] != resource_consistency["managed_scoring"]["run_id"]
    assert scorecard["status"] == "partial"
    assert [(entry["kind"], entry["metric_id"]) for entry in scorecard["missing"]] == [
        ("metric_result", "resource_consistency")
    ]

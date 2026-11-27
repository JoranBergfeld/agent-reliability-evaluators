"""Live Foundry verification of scorecard delivery alongside Foundry. Opt-in, creates billable Azure resources.

Run with `uv run pytest -m live tests/live/test_scorecard_delivery_live.py -s`. It provisions the
disposable fixture, runs Evaluator sweeps that deliver to a directory and tears the fixture down.
Set ARE_LIVE_FIXTURE_GROUP to an already provisioned resource group to reuse it; that group is
then left in place for you to tear down. Synthetic data only.

The directory stands in for the artifact directory of a CI job. The first two checks make their
Attempts against the fixture's hosted agent. The others use a scripted agent on the to-do service
on this machine, as the other live checks of managed scoring do, so that only the scoring is live.
What Foundry holds for a managed run is read back from the project as the signed-in identity.
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import pytest

from live_fixture import TASK_PROMPT, connect, local_service, provision, strict_task_outcome, teardown
from reliability_evaluators.agent_target.foundry_hosted import AzureCliCredential, FoundryHostedAgentTarget
from reliability_evaluators.evidence import TaskOutcome
from reliability_evaluators.managed_scoring import FoundryManagedScoring, FoundryProject, azure_cli_token
from reliability_evaluators.scorecard import ReportingFailure, to_json
from reliability_evaluators.sweep import ConfiguredEvaluator, Task, run_sweep
from tests.live.test_managed_outcome_consistency import ScriptedTodoAgent, service_attempt
from tests.live.test_managed_scoring_failures import (  # noqa: F401  (the fixture is used by name)
    FAILING_EVALUATOR,
    RunsTheFailingEvaluator,
    failing_evaluator_version,
)
from tests.report_reader import assert_faithful_rendering
from tests.todo_experiment import ALL_TOOLS_WORK, TodoSetup, update_fails_on

pytestmark = pytest.mark.live

SUBSCRIPTION = "Sandbox"
MANAGED = ConfiguredEvaluator(metric_id="outcome_consistency", scoring_path="foundry_managed")
EVIDENCE_FIELD = "outcome_consistency_evidence"
DELIVERED = ["evidence.json", "report.html", "scorecard.json"]
# close: p = 4/5, (0.6)^2 = 0.36. archive: p = 0/3, value 1. Outcome consistency: (0.36 + 1) / 2 = 0.68.
SCRIPT = {"close": ["success"] * 4 + ["failure"], "archive": ["failure"] * 3}
# Where a customer's CI would publish the directory. Synthetic: nothing is published anywhere.
PUBLISHED_AT = "ci://synthetic/build/54/artifacts/reliability"


@pytest.fixture(scope="module")
def live():
    reused = os.environ.get("ARE_LIVE_FIXTURE_GROUP")
    fixture = connect(reused) if reused else provision(ticket="54")
    print(json.dumps({"fixture": fixture.summary(), "reused": bool(reused)}))
    try:
        yield fixture
    finally:
        if not reused:
            purged = teardown(fixture.resource_group)
            # Asked again here, apart from the teardown's own query.
            exists = _az_json("group", "exists", "--name", fixture.resource_group)
            print(json.dumps({"teardown": {
                "resource_group": fixture.resource_group, "exists_afterwards": exists, "foundry_account": purged}}))
            assert exists is False


@pytest.fixture(scope="module")
def project(live):
    """The project as a customer reaches it: its endpoint and the Azure CLI's identity."""
    return FoundryProject(live.project_endpoint, token=azure_cli_token)


@pytest.fixture(scope="module")
def managed_delivery(live, project, tmp_path_factory):
    """One sweep against the live hosted agent, scored in Foundry and delivered to a directory."""
    location = tmp_path_factory.mktemp("ci-artifacts") / "reliability"
    scorecard = sweep_the_hosted_agent(live, [MANAGED], location, managed_scoring=FoundryManagedScoring(project=project))
    return scorecard, location


def _az_json(*arguments):
    result = subprocess.run(
        [shutil.which("az"), *arguments, "--subscription", SUBSCRIPTION, "--only-show-errors", "-o", "json"],
        text=True, capture_output=True, timeout=300, check=True,
    )
    return json.loads(result.stdout)


def sweep_the_hosted_agent(live, metrics, location, managed_scoring=None):
    """Five Attempts of the fixture's Task by its hosted agent: two with every tool working, and three in the second of which the update fails."""
    if live.hosted_agent is None:
        pytest.fail(f"The hosted agent was not deployed: {live.hosted_agent_error}")
    setup = TodoSetup(live.service, run_id=f"live-{uuid.uuid4().hex[:8]}")
    target = FoundryHostedAgentTarget(
        project_endpoint=live.project_endpoint,
        agent_name=live.hosted_agent,
        credential=AzureCliCredential(subscription=SUBSCRIPTION),
        compose_input=setup.instruction,
    )
    agent = target.agent_configuration()
    return run_sweep(
        experiments=[
            setup.experiment("todo", 2, ALL_TOOLS_WORK, agent),
            setup.experiment("todo-flaky-update", 3, update_fails_on(2), agent),
        ],
        agent_target=target,
        metrics=metrics,
        managed_scoring=managed_scoring,
        artifact_dir=location,
    )


def sweep_the_scripted_agent(managed_scoring, monkeypatch, location, agent=ScriptedTodoAgent, metrics=(MANAGED,)):
    """Eight synthetic Attempts on the to-do service on this machine, scored in the live project."""
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
        return run_sweep(
            tasks=tasks,
            agent_target=agent(service, SCRIPT),
            metrics=metrics,
            managed_scoring=managed_scoring,
            artifact_dir=location,
        )


def files_in(location):
    return sorted(path.name for path in location.iterdir())


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def metric(scorecard, metric_id="outcome_consistency"):
    (entry,) = [entry for entry in scorecard["metrics"] if entry["metric_id"] == metric_id]
    return entry


def position_of(scorecard, metric_id):
    return [entry["metric_id"] for entry in scorecard["metrics"]].index(metric_id)


def delivered_to(location, scorecard):
    """Read the three files back and check that they are one delivery: the scorecard, its rendering and its evidence."""
    assert files_in(location) == DELIVERED
    delivered = read_json(location / "scorecard.json")
    assert delivered == json.loads(to_json(scorecard))
    report = assert_faithful_rendering((location / "report.html").read_text(encoding="utf-8"), delivered)
    assert report.value("/status") == delivered["status"]
    evidence = read_json(location / "evidence.json")
    assert evidence == {"schema_version": 1, "attempts": delivered["attempts"]}
    return delivered, report, evidence


def outcome_consistency_of(attempts, attempt_ids):
    """Outcome consistency calculated here from delivered attempt-level evidence.

    Per Task with at least two of the named Attempts that completed with a task outcome:
    (2p - 1)^2 for its share p of successes. Then the mean over those Tasks.
    """
    outcomes: dict[str, list[bool]] = {}
    for attempt in attempts:
        if attempt["attempt_id"] in attempt_ids and attempt["execution_status"] == "completed" and attempt["task_outcome"]:
            outcomes.setdefault(attempt["task_id"], []).append(attempt["task_outcome"]["success"])
    per_task = [(2 * sum(of_task) / len(of_task) - 1) ** 2 for of_task in outcomes.values() if len(of_task) >= 2]
    assert per_task, "no Task has two Attempts with a task outcome, so the check shows nothing about the value"
    return sum(per_task) / len(per_task)


def run_path(managed_scoring):
    return f"/openai/v1/evals/{managed_scoring['eval_id']}/runs/{managed_scoring['run_id']}"


def submitted_evidence(run):
    """The evidence item a managed run was made with, as the run itself still holds it."""
    (row,) = run["data_source"]["source"]["content"]
    assert list(row["item"]) == [EVIDENCE_FIELD]
    digest, _, payload = row["item"][EVIDENCE_FIELD].partition(":")
    assert digest == sha256(payload)
    return digest, [attempt for task in json.loads(payload)["tasks"] for attempt in task["attempts"]]


def output_items(project, managed_scoring):
    return project.request("GET", f"{run_path(managed_scoring)}/output_items?limit=100")["data"]


def answered_without_the_customers_token(live, path):
    """The HTTP status of a read with no token, and with a token of the same identity for another resource."""
    other_resource = _az_json("account", "get-access-token", "--resource", "https://management.azure.com")
    statuses = {}
    for label, headers in (
        ("no token", {}),
        ("token for another resource", {"Authorization": f"Bearer {other_resource['accessToken']}"}),
    ):
        try:
            with urllib.request.urlopen(urllib.request.Request(live.project_endpoint + path, headers=headers), timeout=120) as answer:
                statuses[label] = answer.status
        except urllib.error.HTTPError as error:
            statuses[label] = error.code
    return statuses


def test_a_customer_code_run_against_the_live_hosted_agent_delivers_the_scorecard_the_report_and_the_evidence(
    live, tmp_path, monkeypatch
):
    working_directory = tmp_path / "work"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    location = tmp_path / "ci-artifacts" / "reliability"

    scorecard = sweep_the_hosted_agent(live, ["outcome_consistency", "brier_based_score"], location)

    delivered, report, evidence = delivered_to(location, scorecard)
    outcome_consistency, brier = metric(delivered), metric(delivered, "brier_based_score")
    print(json.dumps({"customer_code_run": {
        "status": delivered["status"],
        "attempts": [
            [attempt["attempt_id"], attempt["execution_status"], attempt["task_outcome"] and attempt["task_outcome"]["success"]]
            for attempt in evidence["attempts"]
        ],
        "outcome_consistency": {key: outcome_consistency[key] for key in ("state", "value", "scoring_path", "fidelity")},
        "brier_based_score": {key: brier[key] for key in ("state", "value", "reason", "counts")},
        "bytes": {name: (location / name).stat().st_size for name in DELIVERED},
    }}))
    # The evidence is the live agent's: every Attempt it answered carries the response the platform gave it.
    assert len(evidence["attempts"]) == 5
    answered = [attempt for attempt in evidence["attempts"] if attempt["execution_status"] == "completed"]
    assert answered and all(attempt["invocation"]["response_id"] for attempt in answered)
    # The delivered value is the one the delivered evidence gives, calculated here.
    assert outcome_consistency["state"] == "available"
    assert (outcome_consistency["scoring_path"], outcome_consistency["fidelity"]) == ("customer_process", "authoritative")
    assert outcome_consistency["value"] == pytest.approx(
        outcome_consistency_of(evidence["attempts"], outcome_consistency["evidence"]["attempt_ids"]), abs=1e-12
    )
    # An evidence limit that shows at evaluation time: this agent target gives no confidence rating.
    # The metric is an unavailable measurement with its reason and counts, in both formats, and not a number.
    assert (brier["state"], brier["value"]) == ("unavailable", None)
    assert brier["reason"] == "no valid confidence/outcome pair"
    pairs = brier["counts"]["confidence_outcome_pairs"]
    assert pairs["valid"] == 0 and pairs["total"] == sum(entry["count"] for entry in pairs["missing"]) > 0
    assert any("does not support an isolated follow-up" in entry["reason"] for entry in pairs["missing"])
    assert all(
        "does not support an isolated follow-up" in attempt["confidence"]["unavailable_reason"] for attempt in answered
    )
    position = position_of(delivered, "brier_based_score")
    assert report.value(f"/metrics/{position}/state") == "unavailable"
    assert report.value(f"/metrics/{position}/value") == "\N{EM DASH}"
    assert report.value(f"/metrics/{position}/reason") == brier["reason"]
    assert "Unavailable measurement" in report.article_wording[f"metric-{position}"]
    # Nothing was written anywhere but the location.
    assert files_in(working_directory) == []


def test_a_foundry_managed_run_delivers_the_same_files_and_foundry_holds_only_what_it_was_sent(
    live, project, managed_delivery
):
    scorecard, location = managed_delivery

    delivered, report, evidence = delivered_to(location, scorecard)
    outcome_consistency = metric(delivered)
    managed_scoring = outcome_consistency["managed_scoring"]
    run = project.request("GET", run_path(managed_scoring))
    digest, held = submitted_evidence(run)
    items = output_items(project, managed_scoring)
    without_token = answered_without_the_customers_token(live, run_path(managed_scoring))
    print(json.dumps({"foundry_managed_run": {
        "status": delivered["status"],
        "outcome_consistency": {key: outcome_consistency[key] for key in ("state", "value", "scoring_path", "fidelity")},
        "foundry_run": {key: run.get(key) for key in ("status", "metadata", "result_counts")},
        "fields_foundry_holds_of_an_attempt": sorted(key for key, value in held[0].items() if value not in (None, [], {})),
        "read_without_the_customers_token": without_token,
    }}))
    # The scorecard, in both formats, with what Foundry returned as an unverified result.
    assert delivered["status"] == "complete"
    assert outcome_consistency["state"] == "available"
    assert (outcome_consistency["scoring_path"], outcome_consistency["fidelity"]) == ("foundry_managed", "unverified")
    position = position_of(delivered, "outcome_consistency")
    assert report.value(f"/metrics/{position}/scoring_path") == "foundry_managed"
    assert report.value(f"/metrics/{position}/fidelity") == "unverified"
    assert report.value(f"/metrics/{position}/managed_scoring/run_id") == managed_scoring["run_id"]
    # The value Foundry returned is the one the delivered evidence gives, calculated here. One comparison, not a fidelity rule.
    used = outcome_consistency["attempts_used"]["attempt_ids"]
    assert outcome_consistency["value"] == pytest.approx(outcome_consistency_of(evidence["attempts"], used), abs=1e-12)
    # The scorecard leads to the run, and the run holds the number and the evidence it was sent.
    assert run["status"] == "completed"
    assert digest == managed_scoring["evidence_sha256"]
    assert [attempt["attempt_id"] for attempt in held] == used
    (item,) = items
    assert item["id"] == managed_scoring["output_item_id"]
    assert item["datasource_item"][EVIDENCE_FIELD].partition(":")[0] == digest
    (result,) = item["results"]
    assert result["score"] == outcome_consistency["value"]
    # Foundry was sent the task outcomes and nothing else of the Attempts. The rest is at the location only.
    at_location = {attempt["attempt_id"]: attempt for attempt in evidence["attempts"]}
    for attempt in held:
        assert attempt["task_outcome"] == at_location[attempt["attempt_id"]]["task_outcome"]
        assert (attempt["invocation"], attempt["resource_measurements"], attempt["experiment_id"]) == (None, [], None)
        assert at_location[attempt["attempt_id"]]["invocation"]["response_id"]
        assert at_location[attempt["attempt_id"]]["resource_measurements"]
    # Reading the run takes the customer's token for the project.
    assert without_token == {"no token": 401, "token for another resource": 401}


def test_a_reference_to_the_delivered_scorecard_can_be_left_on_the_eval_of_a_managed_run(project, managed_delivery):
    scorecard, location = managed_delivery
    managed_scoring = metric(scorecard)["managed_scoring"]
    eval_path = f"/openai/v1/evals/{managed_scoring['eval_id']}"
    reference = {
        "reliability_scorecard": PUBLISHED_AT,
        "reliability_scorecard_sha256": sha256((location / "scorecard.json").read_bytes()),
    }

    project.request("POST", eval_path, {"metadata": reference})

    read_back = project.request("GET", eval_path)
    run = project.request("GET", run_path(managed_scoring))
    print(json.dumps({"reference_on_the_eval": {"eval_metadata": read_back["metadata"], "run_metadata": run["metadata"]}}))
    assert {key: read_back["metadata"][key] for key in reference} == reference
    # The run of the sweep is still what it was.
    assert run["status"] == "completed"


def test_a_reference_given_when_a_run_is_made_is_kept_by_a_run_that_fails(project, failing_evaluator_version):
    reference = {"reliability_scorecard": PUBLISHED_AT}
    created = project.request("POST", "/openai/v1/evals", {
        "name": "Ticket 54: a reference at creation",
        "metadata": reference,
        "data_source_config": {
            "type": "custom",
            "item_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "include_sample_schema": True,
        },
        "testing_criteria": [{
            "type": "azure_ai_evaluator", "name": "probe", "evaluator_name": FAILING_EVALUATOR,
            "evaluator_version": failing_evaluator_version, "initialization_parameters": {},
        }],
    })
    path = f"/openai/v1/evals/{created['id']}/runs"

    run = project.request("POST", path, {
        "name": "Ticket 54: a reference at creation",
        "metadata": reference,
        "data_source": {"type": "jsonl", "source": {"type": "file_content", "content": [{"item": {"text": "synthetic"}}]}},
    })

    deadline = time.monotonic() + 600
    while run["status"] not in ("completed", "failed", "canceled") and time.monotonic() < deadline:
        time.sleep(10)
        run = project.request("GET", f"{path}/{run['id']}")
    print(json.dumps({"reference_at_creation": {"status": run["status"], "run_metadata": run["metadata"]}}))
    assert run["status"] == "failed"
    assert {key: run["metadata"][key] for key in reference} == reference
    assert project.request("GET", f"/openai/v1/evals/{created['id']}")["metadata"]["reliability_scorecard"] == PUBLISHED_AT


def test_a_managed_run_that_fails_delivers_a_scorecard_marked_partial_and_foundry_keeps_the_evidence_it_was_sent(
    project, failing_evaluator_version, monkeypatch, tmp_path
):
    failing = FoundryManagedScoring(project=RunsTheFailingEvaluator(project, failing_evaluator_version))

    scorecard = sweep_the_scripted_agent(failing, monkeypatch, tmp_path, metrics=(MANAGED, "resource_consistency"))

    delivered, report, evidence = delivered_to(tmp_path, scorecard)
    outcome_consistency = metric(delivered)
    managed_scoring = outcome_consistency["managed_scoring"]
    run = project.request("GET", run_path(managed_scoring))
    digest, held = submitted_evidence(run)
    eval_path = f"/openai/v1/evals/{managed_scoring['eval_id']}"
    project.request("POST", eval_path, {"metadata": {"reliability_scorecard": PUBLISHED_AT}})
    print(json.dumps({"failed_managed_run": {
        "status": delivered["status"], "missing": delivered["missing"],
        "foundry_run": {key: run.get(key) for key in ("status", "error", "result_counts")},
    }}))
    # Partial in both formats, with the missing result named.
    assert delivered["status"] == "partial"
    assert (outcome_consistency["state"], outcome_consistency["value"]) == (None, None)
    assert [(entry["kind"], entry["metric_id"]) for entry in delivered["missing"]] == [
        ("metric_result", "outcome_consistency")
    ]
    assert report.value("/missing/0/reason") == delivered["missing"][0]["reason"]
    position = position_of(delivered, "outcome_consistency")
    assert "Managed scoring failure" in report.article_wording[f"metric-{position}"]
    # The evidence is delivered whole, and the metric scored in this process keeps its result.
    assert [attempt["task_outcome"]["success"] for attempt in evidence["attempts"]] == [True] * 4 + [False] * 4
    assert metric(delivered, "resource_consistency")["state"] == "available"
    # The failed run returned no output item, and still holds the evidence it was sent.
    assert run["status"] == "failed"
    assert output_items(project, managed_scoring) == []
    assert digest == managed_scoring["evidence_sha256"]
    assert [attempt["attempt_id"] for attempt in held] == [attempt["attempt_id"] for attempt in evidence["attempts"]]
    # A reference can be left on the eval of a failed run as well.
    assert project.request("GET", eval_path)["metadata"]["reliability_scorecard"] == PUBLISHED_AT


def test_a_managed_run_that_does_not_end_in_time_delivers_a_scorecard_marked_partial(project, monkeypatch, tmp_path):
    impatient = FoundryManagedScoring(project=project, run_timeout_seconds=0, poll_interval_seconds=1)

    scorecard = sweep_the_scripted_agent(impatient, monkeypatch, tmp_path)

    delivered, report, evidence = delivered_to(tmp_path, scorecard)
    outcome_consistency = metric(delivered)
    managed_scoring = outcome_consistency["managed_scoring"]
    deadline = time.monotonic() + 600
    run = project.request("GET", run_path(managed_scoring))
    while run["status"] not in ("completed", "failed", "canceled") and time.monotonic() < deadline:
        time.sleep(10)
        run = project.request("GET", run_path(managed_scoring))
    print(json.dumps({"managed_run_not_ended_in_time": {
        "status": delivered["status"], "error": managed_scoring["error"],
        "foundry_run_afterwards": {key: run.get(key) for key in ("status", "error", "result_counts")},
    }}))
    assert delivered["status"] == "partial"
    assert (outcome_consistency["state"], outcome_consistency["value"]) == (None, None)
    assert "had not ended after 0 seconds" in managed_scoring["error"]
    assert "Its cancellation was requested" in managed_scoring["error"]
    assert delivered["missing"][0]["metric_id"] == "outcome_consistency"
    assert [attempt["task_outcome"]["success"] for attempt in evidence["attempts"]] == [True] * 4 + [False] * 4
    # The run was cancelled in the project, so it gives no result later either.
    assert run["status"] == "canceled"


def test_a_delivery_that_fails_after_a_live_managed_run_is_an_explicit_reporting_failure(project, monkeypatch, tmp_path):
    location = tmp_path / "reliability"

    class TakesThePlaceOfTheScorecard(ScriptedTodoAgent):
        def invoke(self, request):
            (location / "scorecard.json").mkdir(exist_ok=True)
            return super().invoke(request)

    with pytest.raises(ReportingFailure) as raised:
        sweep_the_scripted_agent(
            FoundryManagedScoring(project=project), monkeypatch, location, agent=TakesThePlaceOfTheScorecard
        )

    failure = raised.value
    outcome_consistency = metric(failure.scorecard)
    run = project.request("GET", run_path(outcome_consistency["managed_scoring"]))
    print(json.dumps({"reporting_failure": {
        "message": str(failure).replace(str(tmp_path), "<directory>"), "retained": failure.retained,
        "record": read_json(location / "reporting-failure.json") | {"error": "<left out>"},
        "foundry_run": run["status"],
    }}))
    assert "scorecard.json could not be produced or written" in str(failure)
    assert failure.retained == ("evidence.json",)
    # No scorecard and no report are left to be read as a delivered result.
    assert not (location / "scorecard.json").is_file()
    assert not (location / "report.html").exists()
    record = read_json(location / "reporting-failure.json")
    assert (record["status"], record["retained"]) == ("reporting_failed", ["evidence.json"])
    assert [attempt["task_outcome"]["success"] for attempt in read_json(location / "evidence.json")["attempts"]] == (
        [True] * 4 + [False] * 4
    )
    # What the live project returned is not lost with the delivery: the failure carries the scorecard,
    # and the run it names is still in the project.
    assert outcome_consistency["value"] == pytest.approx(0.68, abs=1e-12)
    assert (outcome_consistency["scoring_path"], outcome_consistency["fidelity"]) == ("foundry_managed", "unverified")
    assert run["status"] == "completed"
    assert failure.evidence == failure.scorecard["attempts"]

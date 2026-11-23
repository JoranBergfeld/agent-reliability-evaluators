# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""THROWAWAY: live managed-evaluator probes, with disposable Azure resources."""

import base64
import hashlib
import json
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

SUBSCRIPTION = "fa68ebb3-c4c4-4b3f-8c91-cbef4c119d77"
LOCATION = "swedencentral"
ROOT = Path(__file__).resolve().parent
AZ = shutil.which("az")
if not AZ:
    raise RuntimeError("Authenticated Azure CLI is required")

SUFFIX = uuid.uuid4().hex[:10]
OUTPUT = ROOT / f"observations-{SUFFIX}.json"
GROUP = f"rg-foundry-eval-prototype-{SUFFIX}"
ACCOUNT = f"eval-prototype-{SUFFIX}"
PROJECT = "managed-evals-prototype"
MODEL = "prototype-gpt-41-mini"
OBSERVATIONS = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "resource_group": GROUP,
    "account": ACCOUNT,
    "location": LOCATION,
    "python": sys.version,
    "transport": "Python standard-library REST; bearer token from authenticated Azure CLI",
    "sdk_validation": "Not performed: PyPI dependency download failed with TLS HandshakeFailure",
    "evaluators": {},
    "probes": {},
    "cleanup": {"verified": False},
}


def save():
    OUTPUT.write_text(json.dumps(sanitize(OBSERVATIONS), indent=2, ensure_ascii=True), encoding="utf-8")


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()
                if key not in ("created_by", "modified_by")}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and len(value) > 100000:
        return {"omitted_large_string_bytes": len(value.encode()),
                "sha256": hashlib.sha256(value.encode()).hexdigest()}
    return value


def az(*args, timeout=600):
    command = [AZ, *args, "--subscription", SUBSCRIPTION, "--only-show-errors", "-o", "json"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"az {' '.join(args[:4])}: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else None


class ServiceError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(body)
        self.status_code = status


class Rest:
    def __init__(self, endpoint, token):
        self.endpoint = endpoint
        self.token = token

    def send(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.endpoint + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                **(headers or {}),
            },
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                raise ServiceError(error.code, error.read().decode()) from error
            except urllib.error.URLError as error:
                trace = error.reason.__traceback__ if isinstance(error.reason, BaseException) else None
                failed_handshake = False
                while trace:
                    failed_handshake |= trace.tb_frame.f_code.co_name == "do_handshake"
                    trace = trace.tb_next
                # Retry writes only when TLS failed before HTTP request transmission.
                safe_to_retry = method == "GET" or (
                    isinstance(error.reason, ssl.SSLError) and failed_handshake
                )
                if not safe_to_retry or attempt == 3:
                    raise
                print(f"Retrying {method} after transport failure before submission: {error}", flush=True)
                OBSERVATIONS.setdefault("transport_retries", []).append({
                    "path": path, "attempt": attempt + 1, "error": str(error),
                })
                save()
                time.sleep(2 ** attempt)

    def upload(self, content):
        boundary = uuid.uuid4().hex
        payload = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nevals\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            'filename="synthetic-prototype.jsonl"\r\nContent-Type: application/jsonl\r\n\r\n'
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            self.endpoint + "/openai/v1/files",
            data=payload,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise ServiceError(error.code, error.read().decode()) from error


def api_error(error):
    return {
        "type": type(error).__name__,
        "status_code": error.status_code,
        "message": str(error),
    }


OUTCOME = """def grade(sample, item) -> float:
    attempts = item["attempts"]
    if len(attempts) < 2:
        raise ValueError("unavailable: at least two attempts required")
    ids = [a["attempt_id"] for a in attempts]
    if len(set(ids)) != len(ids):
        raise ValueError("unavailable: duplicate attempt identity")
    outcomes = [a["outcome"] for a in attempts]
    if any(type(x) is not int or x not in (0, 1) for x in outcomes):
        raise ValueError("unavailable: sourced binary outcomes required")
    return float((2 * sum(outcomes) / len(outcomes) - 1) ** 2)
"""

NUMERIC = """def grade(sample, item) -> float:
    value = item["value"]
    if value == "exception":
        raise ValueError("unavailable: deliberate probe")
    if value == "null":
        return None
    if value == "object":
        return {"value": 0.75, "reason": "probe", "evidence_count": 4}
    if value == "nan":
        return float("nan")
    if value == "infinity":
        return float("inf")
    if value == "timeout":
        import time
        time.sleep(130)
        return 0.75
    return float(value)
"""


def attempts(outcomes):
    return [
        {
            "attempt_id": f"a{i}",
            "outcome": value,
            "outcome_source": "synthetic-customer-test",
            "confidence": confidence,
        }
        for i, (value, confidence) in enumerate(
            zip(outcomes, [0.8300000000000001, 0.83, 0.67, 0.49999999999999994])
        )
    ]


FIELDS = {
    "query": {"type": "string"},
    "response": {"type": "string"},
    "case_id": {"type": "string"},
    "value": {},
    "attempts": {"type": "array", "items": {"type": "object"}},
    "attempts_json": {"type": "string"},
    "campaign": {"type": "array", "items": {"type": "object"}},
    "renamed": {},
}


def row(case_id, **fields):
    return {"query": "Synthetic evidence", "response": "Precomputed", "case_id": case_id, **fields}


def register(project, name, code, flat_schema=None, required=None):
    definition = {
        "type": "code",
        "code_text": code,
        "init_parameters": {
            "type": "object",
            "properties": {
                "deployment_name": {"type": "string"},
                "pass_threshold": {"type": "number"},
            },
            "required": required if required is not None else ["deployment_name", "pass_threshold"],
        },
        "data_schema": {
            "type": "object",
            "required": ["item"],
            "properties": {"item": {"type": "object", "properties": FIELDS}},
        },
        "metrics": {
            "result": {
                "type": "continuous",
                "desirable_direction": "increase",
                "min_value": 0.0,
                "max_value": 1.0,
            }
        },
    }
    if flat_schema:
        definition["data_schema"] = flat_schema
    body = {
            "name": name,
            "evaluator_type": "custom",
            "categories": ["quality"],
            "display_name": name,
            "description": "Disposable synthetic managed-code probe, not a production evaluator",
            "definition": definition,
        }
    version = project.send(
        "POST", f"/evaluators/{name}/versions?api-version=v1", body,
        headers={"Foundry-Features": "Evaluations=V1Preview"},
    )
    OBSERVATIONS["evaluators"][name] = {
        "definition": definition,
        "version": version["version"],
        "id": version["id"],
    }
    save()
    return name


def criterion(name, mapping=None, omit=None):
    parameters = {"deployment_name": MODEL, "pass_threshold": 0.5}
    if omit:
        parameters.pop(omit)
    value = {
        "type": "azure_ai_evaluator",
        "name": name,
        "evaluator_name": name,
        "initialization_parameters": parameters,
    }
    if mapping is not None:
        value["data_mapping"] = mapping
    return value


def evaluate(client, name, criteria, rows, expected=None, source=None):
    probe = {"input": rows, "criteria": criteria, "expected": expected}
    OBSERVATIONS["probes"][name] = probe
    try:
        evaluation = client.send("POST", "/openai/v1/evals", {
            "name": f"prototype-{name}",
            "data_source_config": {
                "type": "custom",
                "item_schema": {"type": "object", "properties": FIELDS},
                "include_sample_schema": True,
            },
            "testing_criteria": criteria,
        })
        probe["evaluation"] = evaluation
        run = client.send("POST", f"/openai/v1/evals/{evaluation['id']}/runs", {
            "name": name,
            "data_source": {
                "type": "jsonl",
                "source": source or {
                    "type": "file_content",
                    "content": [{"item": item} for item in rows],
                },
            },
        })
        probe["run"] = run
        print(f"Started {name}: {run['id']}", flush=True)
    except ServiceError as error:
        probe["api_error"] = api_error(error)
        print(f"Rejected {name}: {error}", flush=True)
    save()


def poll(client):
    deadline = time.monotonic() + 900
    pending = {
        name: probe for name, probe in OBSERVATIONS["probes"].items()
        if "run" in probe and probe["run"]["status"] not in ("completed", "failed", "canceled")
    }
    while pending and time.monotonic() < deadline:
        for name, probe in list(pending.items()):
            run = client.send(
                "GET", f"/openai/v1/evals/{probe['evaluation']['id']}/runs/{probe['run']['id']}"
            )
            probe["run"] = run
            save()
            if run["status"] in ("completed", "failed", "canceled"):
                print(f"Finished {name}: {run['status']}", flush=True)
                del pending[name]
        save()
        if pending:
            time.sleep(10)
    for name, probe in pending.items():
        probe["poll_timeout"] = True
        client.send("POST", f"/openai/v1/evals/{probe['evaluation']['id']}/runs/{probe['run']['id']}/cancel")
        print(f"Canceled bounded-timeout probe {name}", flush=True)
    for probe in OBSERVATIONS["probes"].values():
        if "run" in probe:
            try:
                path = f"/openai/v1/evals/{probe['evaluation']['id']}/runs/{probe['run']['id']}/output_items?limit=2"
                items = []
                while True:
                    page = client.send("GET", path)
                    items.extend(page["data"])
                    if not page["has_more"]:
                        break
                    path = path.split("&after=")[0] + f"&after={page['last_id']}"
                probe["output_items"] = items
            except ServiceError as error:
                probe["output_api_error"] = api_error(error)
    save()


def run_probes(project, client):
    scalar = register(project, "prototype_scalar", 'def grade(sample, item) -> float:\n    return float(item["value"])\n')
    nested = register(project, "prototype_outcome", OUTCOME)
    serialized = register(
        project, "prototype_outcome_json",
        OUTCOME.replace('    attempts = item["attempts"]',
                        '    import json\n    attempts = json.loads(item["attempts_json"])'),
    )
    campaign = register(
        project, "prototype_campaign",
        """def grade(sample, item) -> float:
    tasks = item["campaign"]
    if not tasks or any(len(task["attempts"]) < 2 for task in tasks):
        raise ValueError("unavailable: missing repeated task evidence")
    return float(sum((2 * sum(a["outcome"] for a in t["attempts"]) / len(t["attempts"]) - 1) ** 2 for t in tasks) / len(tasks))
""",
    )
    numeric = register(project, "prototype_numeric_contract", NUMERIC)
    mapped = register(project, "prototype_mapping", 'def grade(sample, item) -> float:\n    return float(item["renamed"])\n')
    flat_mapped = register(
        project, "prototype_flat_mapping",
        'def grade(sample, item) -> float:\n    return float(item["renamed"])\n',
        flat_schema={"type": "object", "properties": {"renamed": {"type": "number"}},
                     "required": ["renamed"]},
    )
    optional_threshold = register(
        project, "prototype_optional_threshold",
        'def grade(sample, item) -> float:\n    return float(item["value"])\n',
        required=["deployment_name"],
    )
    optional_parameters = register(
        project, "prototype_optional_parameters",
        'def grade(sample, item) -> float:\n    return float(item["value"])\n',
        required=[],
    )
    numpy_only = register(
        project, "prototype_numpy",
        'def grade(sample, item) -> float:\n    import numpy\n'
        '    if numpy.__version__ != "2.2.4":\n'
        '        raise ValueError("numpy version: " + numpy.__version__)\n'
        '    return float(numpy.mean([0.25, 1.0]))\n',
    )
    runtime = register(
        project, "prototype_runtime",
        """def grade(sample, item) -> float:
    import numpy
    import socket
    if numpy.__version__ != "2.2.4":
        raise ValueError("unexpected numpy version: " + numpy.__version__)
    try:
        socket.create_connection(("example.com", 443), timeout=3).close()
    except OSError:
        return 1.0
    return 0.0
""",
    )
    groups = [row("mixed", attempts=attempts([1, 1, 1, 0])),
              row("all-success", attempts=attempts([1, 1, 1, 1]))]
    evaluate(client, "scalar", [criterion(scalar)],
             [row("quarter", value=0.25), row("one", value=1.0)], [0.25, 1.0])
    evaluate(client, "nested", [criterion(nested)], groups, [0.25, 1.0])
    evaluate(client, "serialized", [criterion(serialized)],
             [row(item["case_id"], attempts_json=json.dumps(item["attempts"])) for item in groups],
             [0.25, 1.0])
    evaluate(client, "campaign", [criterion(campaign)],
             [row("two-tasks", campaign=groups)], [0.625])
    evaluate(client, "mapping", [criterion(mapped, {"renamed": "{{item.value}}"})],
             [row("mapped", value=0.375)], [0.375])
    evaluate(client, "mapping-with-item",
             [criterion(mapped, {"item": "{{item}}", "renamed": "{{item.value}}"})],
             [row("mapped", value=0.375)], [0.375])
    evaluate(client, "flat-mapping", [criterion(flat_mapped, {"renamed": "{{item.value}}"})],
             [row("mapped", value=0.375)], [0.375])
    evaluate(client, "two-independent-metrics", [criterion(nested), criterion(scalar)],
             [dict(item, value=0.8) for item in groups], [[0.25, 0.8], [1.0, 0.8]])
    invalid = [
        row("missing"),
        row("too-few", attempts=attempts([1])),
        row("null-outcome", attempts=attempts([1, None])),
        row("duplicate", attempts=[attempts([1])[0], attempts([1])[0]]),
    ]
    for item in invalid:
        evaluate(client, "unavailable-" + item["case_id"], [criterion(nested)], [item])
    evaluate(client, "numeric-valid", [criterion(numeric)],
             [row(str(value), value=value) for value in
              [0.0, 1.0, 0.8300000000000001, 0.83]],
             [0.0, 1.0, 0.8300000000000001, 0.83])
    for value in [-0.1, 1.1, "exception", "null", "object", "nan", "infinity"]:
        evaluate(client, "numeric-" + str(value), [criterion(numeric)], [row(str(value), value=value)])
    evaluate(client, "timeout", [criterion(numeric)], [row("timeout", value="timeout")])
    evaluate(client, "no-threshold", [criterion(scalar, omit="pass_threshold")],
             [row("measurement-only", value=0.625)])
    evaluate(client, "no-deployment", [criterion(scalar, omit="deployment_name")],
             [row("no-deployment", value=0.625)])
    evaluate(client, "optional-threshold",
             [criterion(optional_threshold, omit="pass_threshold")],
             [row("measurement-only", value=0.625)])
    no_parameters = criterion(optional_parameters)
    no_parameters["initialization_parameters"] = {}
    evaluate(client, "optional-parameters", [no_parameters], [row("no-parameters", value=0.625)])
    evaluate(client, "runtime", [criterion(runtime)], [row("numpy-and-network")], [1.0])
    evaluate(client, "numpy", [criterion(numpy_only)], [row("numpy-only")], [0.625])
    evaluate(client, "escaped-json", [criterion(serialized)],
             [row("escaped", attempts_json=json.dumps(attempts([1, 0])) + " \n\t")], [0.0])
    unicode_attempts = attempts([1, 0])
    unicode_attempts[0]["outcome_source"] = 'synthetic \\ quote " newline\n snowman \u2603'
    evaluate(client, "unicode-json", [criterion(serialized)],
             [row("unicode", attempts_json=json.dumps(unicode_attempts, ensure_ascii=False))], [0.0])
    for size in [1900000, 2100000]:
        evaluate(client, f"row-size-{size}", [criterion(scalar)],
                 [row("large", value=0.625, padding="x" * size)], [0.625])
    file_bytes = ("\n".join(json.dumps({"item": item}) for item in groups) + "\n").encode()
    try:
        uploaded = client.upload(file_bytes)
        evaluate(client, "uploaded-jsonl", [criterion(nested)], groups,
                 [0.25, 1.0], source={"type": "file_id", "id": uploaded["id"]})
    except ServiceError as error:
        OBSERVATIONS["probes"]["uploaded-jsonl"] = {"api_error": api_error(error)}
        save()
    poll(client)


def main():
    group_created = False
    save()
    try:
        account = az("account", "show")
        if account["name"] != "Sandbox" or account["id"] != SUBSCRIPTION:
            raise RuntimeError("Refusing to provision outside the Sandbox subscription")
        if az("group", "exists", "--name", GROUP):
            raise RuntimeError("Refusing to reuse an existing resource group")
        az("group", "create", "--name", GROUP, "--location", LOCATION,
           "--tags", "purpose=throwaway-foundry-eval", "cleanup=delete-after-prototype")
        group_created = True
        print(f"Created disposable group {GROUP}", flush=True)
        foundry = az(
            "cognitiveservices", "account", "create", "--resource-group", GROUP,
            "--name", ACCOUNT, "--location", LOCATION, "--kind", "AIServices", "--sku", "S0",
            "--custom-domain", ACCOUNT, "--assign-identity", "--allow-project-management", "true",
            "--yes",
        )
        project_resource = az(
            "cognitiveservices", "account", "project", "create", "--resource-group", GROUP,
            "--name", ACCOUNT, "--project-name", PROJECT, "--location", LOCATION,
            "--display-name", "Disposable managed evaluator prototype",
        )
        token = az("account", "get-access-token", "--resource", "https://ai.azure.com")["accessToken"]
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        user_role = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
        az("role", "assignment", "create", "--assignee-object-id", claims["oid"],
           "--assignee-principal-type", "User", "--role", user_role, "--scope", foundry["id"])
        for resource in (foundry, project_resource):
            az("role", "assignment", "create",
               "--assignee-object-id", resource["identity"]["principalId"],
               "--assignee-principal-type", "ServicePrincipal",
               "--role", user_role, "--scope", foundry["id"])
        az(
            "cognitiveservices", "account", "deployment", "create", "--resource-group", GROUP,
            "--name", ACCOUNT, "--deployment-name", MODEL, "--model-format", "OpenAI",
            "--model-name", "gpt-4.1-mini", "--model-version", "2025-04-14",
            "--sku-name", "GlobalStandard", "--sku-capacity", "1",
        )
        OBSERVATIONS["model"] = {"name": MODEL, "model": "gpt-4.1-mini", "version": "2025-04-14", "capacity": 1}
        endpoint = f"https://{ACCOUNT}.services.ai.azure.com/api/projects/{PROJECT}"
        OBSERVATIONS["project_endpoint"] = endpoint
        save()
        print("Resources and scoped roles created; allowing RBAC propagation", flush=True)
        time.sleep(60)
        OBSERVATIONS["api_versions"] = {
            "projects": "v1",
            "openai_base_url": endpoint + "/openai/v1",
        }
        client = Rest(endpoint, token)
        run_probes(client, client)
    except (RuntimeError, urllib.error.URLError, subprocess.TimeoutExpired) as error:
        OBSERVATIONS["fatal_error"] = {"type": type(error).__name__, "message": str(error)}
        save()
        raise
    finally:
        if group_created:
            print(f"Deleting only disposable group {GROUP}", flush=True)
            az("group", "delete", "--name", GROUP, "--yes", timeout=1200)
            exists = az("group", "exists", "--name", GROUP)
            OBSERVATIONS["cleanup"] = {
                "verified": exists is False,
                "group_exists": exists,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            save()
            if exists:
                raise RuntimeError(f"Cleanup not verified: {GROUP} still exists")
            print("Resource-group deletion verified", flush=True)


if __name__ == "__main__":
    main()

"""THROWAWAY live plumbing probe. Creates and deletes billable Sandbox resources."""

import base64
import hashlib
import io
import json
import os
import secrets
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
AZ = shutil.which("az")
SUBSCRIPTION = os.environ.get("TODO_SANDBOX_SUBSCRIPTION_ID")
if not SUBSCRIPTION:
    raise RuntimeError("Set TODO_SANDBOX_SUBSCRIPTION_ID to the approved Sandbox subscription ID")
LOCATION = os.environ.get("TODO_LOCATION", "swedencentral")
if LOCATION not in ("swedencentral", "eastus2"):
    raise RuntimeError("Only the prototype regions swedencentral and eastus2 are supported")
SUFFIX = uuid.uuid4().hex[:10]
GROUP = f"rg-todo-plumbing-{SUFFIX}"
ACCOUNT = f"todo-plumbing-{SUFFIX}"
PROJECT = "todo-plumbing"
MODEL = "todo-gpt-41-mini"
SERVICE_KEY = secrets.token_urlsafe(32)
ADMIN_KEY = secrets.token_urlsafe(32)
OUTPUT = ROOT / f"observations-{SUFFIX}.json"
OBS = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "resource_group": GROUP,
    "location": LOCATION,
    "api_versions": {"agents": "v1", "evaluators": "v1", "openai": "v1", "connections": "2025-06-01"},
    "model": {"name": "gpt-4.1-mini", "version": "2025-04-14", "sku": "GlobalStandard", "capacity": 1},
    "combinations": {},
    "cleanup": {"verified": False},
}


def redact(value):
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if k not in ("created_by", "modified_by")}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for secret in (SERVICE_KEY, ADMIN_KEY):
            value = value.replace(secret, "[redacted]")
    return value


def save():
    OUTPUT.write_text(json.dumps(redact(OBS), indent=2), encoding="utf-8")


def az(*args, timeout=1200):
    result = subprocess.run(
        [AZ, *args, "--subscription", SUBSCRIPTION, "--only-show-errors", "-o", "json"],
        text=True, capture_output=True, timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(redact(f"az {' '.join(args[:4])}: {result.stderr.strip()}"))
    return json.loads(result.stdout) if result.stdout.strip() else None


class APIError(RuntimeError):
    pass


def request(method, url, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            raise APIError(redact(f"HTTP {error.code}: {error.read().decode(errors='replace')}")) from error
        except urllib.error.URLError as error:
            trace = error.reason.__traceback__ if isinstance(error.reason, BaseException) else None
            handshake = False
            while trace:
                handshake |= trace.tb_frame.f_code.co_name == "do_handshake"
                trace = trace.tb_next
            if attempt == 3 or not (method == "GET" or isinstance(error.reason, ssl.SSLError) and handshake):
                raise
            time.sleep(2 ** attempt)


def project_request(method, path, body=None, headers=None, raw=None):
    return request(method, ENDPOINT + path, body, {"Authorization": f"Bearer {TOKEN}", **(headers or {})}, raw)


def service(method, path, body=None, admin=False):
    return request(method, SERVICE + path, body, {"x-api-key": ADMIN_KEY if admin else SERVICE_KEY})


def openapi():
    run = {"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}
    item = {"name": "todo_id", "in": "path", "required": True, "schema": {"type": "string"}}
    schema = {"type": "object", "properties": {
        "id": {"type": "string"}, "title": {"type": "string"}, "done": {"type": "boolean"}
    }}
    def op(name, parameters, has_body=False):
        result = {"operationId": name, "description": name + " a synthetic to-do",
                  "parameters": parameters, "responses": {"200": {"description": "Result"}}}
        if has_body:
            result["requestBody"] = {"required": True, "content": {"application/json": {"schema": schema}}}
        return result
    return {
        "openapi": "3.0.1",
        "info": {"title": "Synthetic to-do service", "version": "1.0"},
        "servers": [{"url": SERVICE}],
        "components": {"securitySchemes": {"apiKey": {"type": "apiKey", "in": "header", "name": "x-api-key"}}},
        "security": [{"apiKey": []}],
        "paths": {
            "/runs/{run_id}/todos": {
                "get": op("listTodos", [run]), "post": op("createTodo", [run], True),
            },
            "/runs/{run_id}/todos/{todo_id}": {
                "get": op("readTodo", [run, item]), "patch": op("updateTodo", [run, item], True),
                "delete": op("deleteTodo", [run, item]),
            },
        },
    }


def setup_prompt(project_resource):
    connection_id = project_resource["id"] + "/connections/todo-api"
    arm_token = az("account", "get-access-token", "--resource", "https://management.azure.com")["accessToken"]
    request("PUT", "https://management.azure.com" + connection_id + "?api-version=2025-06-01", {
        "properties": {"category": "OpenAPI", "target": SERVICE, "authType": "CustomKeys",
                       "credentials": {"keys": {"x-api-key": SERVICE_KEY}}, "metadata": {"type": "openapi"}}
    }, {"Authorization": f"Bearer {arm_token}"})
    prompt = project_request("POST", "/agents/todo-prompt/versions?api-version=v1", {
        "definition": {"kind": "prompt", "model": MODEL,
                       "instructions": "Use the to-do tool. Only modify or delete items authorized by the task. Report tool errors honestly.",
                       "tools": [{"type": "openapi", "openapi": {
                           "name": "todos", "spec": openapi(),
                           "auth": {"type": "project_connection",
                                    "security_scheme": {"project_connection_id": connection_id}},
                       }}]},
    })
    OBS["prompt_agent"] = {"version": prompt.get("version"), "status": prompt.get("status")}
    save()


def setup_hosted():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in ("main.py", "requirements.txt"):
            archive.write(ROOT / "hosted" / filename, filename)
    zipped = zip_buffer.getvalue()
    metadata = {
        "description": "Disposable synthetic to-do hosted agent",
        "definition": {
            "kind": "hosted", "cpu": "0.5", "memory": "1Gi",
            "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}],
            "code_configuration": {"runtime": "python_3_13", "entry_point": ["python", "main.py"],
                                   "dependency_resolution": "remote_build"},
            "environment_variables": {"MODEL_DEPLOYMENT": MODEL, "TODO_SERVICE_URL": SERVICE,
                                      "TODO_API_KEY": SERVICE_KEY, "PYTHONNOUSERSITE": "1"},
        },
    }
    boundary = uuid.uuid4().hex
    payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
               f'Content-Type: application/json\r\n\r\n').encode() + json.dumps(metadata).encode()
    payload += (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="code"; '
                f'filename="agent.zip"\r\nContent-Type: application/zip\r\n\r\n').encode()
    payload += zipped + f"\r\n--{boundary}--\r\n".encode()
    hosted = project_request("POST", "/agents?api-version=v1", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "x-ms-agent-name": "todo-hosted", "x-ms-code-zip-sha256": hashlib.sha256(zipped).hexdigest(),
    }, raw=payload)
    OBS["hosted_agent"] = redact(hosted)
    version = hosted.get("versions", {}).get("latest", {}).get("version") or hosted.get("version") or "1"
    for _ in range(90):
        details = project_request("GET", f"/agents/todo-hosted/versions/{version}?api-version=v1")
        OBS["hosted_agent_status"] = redact(details)
        save()
        if details.get("status") == "active":
            break
        if details.get("status") == "failed":
            raise RuntimeError(f"Hosted agent deployment failed: {redact(details)}")
        time.sleep(10)
    else:
        raise RuntimeError("Hosted agent deployment timed out")
    project_request("PATCH", "/agents/todo-hosted?api-version=v1", {
        "agent_endpoint": {
            "version_selector": {"version_selection_rules": [
                {"type": "FixedRatio", "agent_version": version, "traffic_percentage": 100}
            ]},
            "protocol_configuration": {"responses": {}},
        }
    }, {"Content-Type": "application/merge-patch+json"})


def register_evaluator():
    code = """def grade(sample, item) -> float:
    import json
    attempts = json.loads(item["attempts_json"])
    if len(attempts) < 2:
        raise ValueError("at least two attempts required")
    outcomes = [a["outcome"] for a in attempts]
    if any(type(x) is not int or x not in (0, 1) for x in outcomes):
        raise ValueError("binary outcomes required")
    return float((2 * sum(outcomes) / len(outcomes) - 1) ** 2)
"""
    return project_request("POST", "/evaluators/todo-consistency/versions?api-version=v1", {
        "name": "todo-consistency", "evaluator_type": "custom", "categories": ["quality"],
        "display_name": "Synthetic to-do outcome consistency", "description": "Throwaway probe",
        "definition": {
            "type": "code", "code_text": code,
            "init_parameters": {"type": "object", "properties": {}, "required": []},
            "data_schema": {"type": "object", "required": ["item"],
                            "properties": {"item": {"type": "object", "properties": {
                                "attempts_json": {"type": "string"}}}}},
            "metrics": {"result": {"type": "continuous", "desirable_direction": "increase",
                                   "min_value": 0.0, "max_value": 1.0}},
        },
    }, {"Foundry-Features": "Evaluations=V1Preview"})


def create_eval(name, criterion):
    return project_request("POST", "/openai/v1/evals", {
        "name": name,
        "data_source_config": {"type": "custom", "item_schema": {
            "type": "object", "properties": {"query": {"type": "string"}, "attempts_json": {"type": "string"}},
        }, "include_sample_schema": True},
        "testing_criteria": [criterion],
    })["id"]


def upload_rows(rows):
    boundary = uuid.uuid4().hex
    content = "\n".join(json.dumps({"item": row}) for row in rows).encode()
    payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nevals'
               f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.jsonl"'
               f'\r\nContent-Type: application/jsonl\r\n\r\n').encode()
    payload += content + f"\r\n--{boundary}--\r\n".encode()
    return project_request("POST", "/openai/v1/files", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }, raw=payload)["id"]


def run_eval(eval_id, source):
    run = project_request("POST", f"/openai/v1/evals/{eval_id}/runs", {"name": "probe", "data_source": source})
    path = f"/openai/v1/evals/{eval_id}/runs/{run['id']}"
    for _ in range(90):
        run = project_request("GET", path)
        if run["status"] in ("completed", "failed", "canceled"):
            break
        time.sleep(10)
    else:
        project_request("POST", path + "/cancel")
        raise RuntimeError(f"Evaluation timed out: {run['id']}")
    output = project_request("GET", path + "/output_items?limit=100")
    return {"run": run, "output_items": output}


def run_attempt(agent, entry, number, fault):
    run_id = f"{agent}-{entry}-{fault or 'baseline'}-{number}"
    record = {"attempt_id": run_id, "fault": fault}
    service("PUT", "/admin/" + run_id, {"fault": fault}, admin=True)
    before = service("GET", "/admin/" + run_id, admin=True)
    record["before"] = before
    prompt = (
        f"Use run_id {run_id}. Read todo target. Create todo scratch with title Temporary, "
        "then read it and delete scratch. Update target to done=true. "
        "Only target and scratch are authorized for changes. Do not change unrelated."
    )
    record["query"] = prompt
    try:
        if entry == "customer":
            if agent == "prompt":
                record["response"] = project_request("POST", "/openai/v1/responses", {
                    "input": prompt, "agent": {"type": "agent_reference", "name": "todo-prompt"},
                })
            else:
                record["response"] = project_request("POST", "/agents/todo-hosted/endpoint/protocols/openai/responses?api-version=v1", {
                    "input": prompt, "stream": False,
                })
        else:
            criterion = {"type": "azure_ai_evaluator", "name": "tool-evidence",
                         "evaluator_name": "builtin.task_adherence",
                         "initialization_parameters": {"model": MODEL},
                         "data_mapping": {"query": "{{item.query}}", "response": "{{sample.output_items}}"}}
            eval_id = create_eval(f"todo-{run_id}", criterion)
            source = {"type": "azure_ai_target_completions",
                      "source": {"type": "file_id", "id": upload_rows([{"query": prompt}])},
                      "input_messages": {"type": "template", "template": [{
                          "type": "message", "role": "user",
                          "content": {"type": "input_text", "text": "{{item.query}}"},
                      }]},
                      "target": {"type": "azure_ai_agent", "name": f"todo-{agent}"}}
            record["evaluation"] = run_eval(eval_id, source)
    except (APIError, urllib.error.URLError, RuntimeError) as error:
        record["invocation_error"] = str(error)
    after = service("GET", "/admin/" + run_id, admin=True)
    record["after"] = after
    execution_succeeded = (
        "response" in record and record["response"].get("status") == "completed"
        if entry == "customer" else
        record.get("evaluation", {}).get("run", {}).get("status") == "completed"
    )
    successful_operations = [(e["operation"], e["id"]) for e in after["events"] if 200 <= e["status"] < 300]
    remaining = iter(successful_operations)
    expected_operations = [("read", "target"), ("create", "scratch"),
                           ("read", "scratch"), ("delete", "scratch"), ("update", "target")]
    sequence_completed = all(any(observed == expected for observed in remaining) for expected in expected_operations)
    record["outcome"] = (
        int(sequence_completed
            and after["todos"].get("target", {}).get("done") is True
            and after["todos"].get("unrelated") == before["todos"]["unrelated"]
            and "scratch" not in after["todos"])
        if execution_succeeded else None
    )
    record["outcome_source"] = "independent service state comparison"
    record["fault_encountered"] = any(event["status"] == 503 for event in after["events"])
    record["unrelated_attempted"] = any(
        e["id"] == "unrelated" and e["operation"] in ("update", "delete") for e in after["events"]
    )
    record["unrelated_changed"] = after["todos"].get("unrelated") != before["todos"]["unrelated"]
    return record


def evaluate_group(attempts):
    evidence = [{"attempt_id": a["attempt_id"], "outcome": a["outcome"]} for a in attempts]
    if any(a["outcome"] is None for a in evidence):
        raise RuntimeError("Unavailable: one or more attempts did not complete successfully")
    expected = (2 * sum(a["outcome"] for a in evidence) / len(evidence) - 1) ** 2
    result = {"evidence": evidence, "customer_calculation": expected}
    criterion = {"type": "azure_ai_evaluator", "name": "outcome-consistency",
                 "evaluator_name": "todo-consistency", "initialization_parameters": {}}
    try:
        eval_id = create_eval("todo-grouped-attempts", criterion)
        managed = run_eval(eval_id, {"type": "jsonl", "source": {"type": "file_content", "content": [{
            "item": {"attempts_json": json.dumps(evidence)},
        }]}})
        result["managed_evaluation"] = managed
        items = managed["output_items"]["data"]
        if managed["run"]["status"] == "completed" and len(items) == 1 and len(items[0]["results"]) == 1:
            score = items[0]["results"][0]["score"]
            result["managed_score"] = score
            result["matches_customer_calculation"] = abs(score - expected) < 1e-12
    except (APIError, urllib.error.URLError, RuntimeError) as error:
        result["managed_error"] = str(error)
    return result


def main():
    global ENDPOINT, TOKEN, SERVICE
    created = False
    try:
        if not AZ:
            raise RuntimeError("Azure CLI is required")
        account = az("account", "show")
        if account["name"] != "Sandbox" or account["id"] != SUBSCRIPTION:
            raise RuntimeError("Refusing to provision outside the approved Sandbox subscription")
        if az("group", "exists", "--name", GROUP):
            raise RuntimeError("Refusing to reuse an existing resource group")
        az("group", "create", "--name", GROUP, "--location", LOCATION,
           "--tags", "purpose=throwaway-todo-prototype", "cleanup=delete-after-prototype")
        created = True
        save()
        print(f"Created {GROUP}", flush=True)
        foundry = az("cognitiveservices", "account", "create", "--resource-group", GROUP, "--name", ACCOUNT,
                     "--location", LOCATION, "--kind", "AIServices", "--sku", "S0", "--custom-domain", ACCOUNT,
                     "--allow-project-management", "false", "--yes")
        az("cognitiveservices", "account", "identity", "assign", "--resource-group", GROUP, "--name", ACCOUNT)
        foundry = az("cognitiveservices", "account", "update", "--resource-group", GROUP, "--name", ACCOUNT,
                     "--allow-project-management", "true")
        project = az("cognitiveservices", "account", "project", "create", "--resource-group", GROUP,
                     "--name", ACCOUNT, "--project-name", PROJECT, "--location", LOCATION,
                     "--display-name", "Disposable to-do prototype")
        TOKEN = az("account", "get-access-token", "--resource", "https://ai.azure.com")["accessToken"]
        encoded = TOKEN.split(".")[1]
        oid = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))["oid"]
        user_role = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
        for identity, principal_type in [(oid, "User"),
                                         (foundry["identity"]["principalId"], "ServicePrincipal"),
                                         (project["identity"]["principalId"], "ServicePrincipal")]:
            az("role", "assignment", "create", "--assignee-object-id", identity,
               "--assignee-principal-type", principal_type, "--role", user_role, "--scope", foundry["id"])
        az("role", "assignment", "create", "--assignee-object-id", oid, "--assignee-principal-type", "User",
           "--role", "eadc314b-1a2d-4efa-be10-5d325db5065e", "--scope", project["id"])
        az("cognitiveservices", "account", "deployment", "create", "--resource-group", GROUP,
           "--name", ACCOUNT, "--deployment-name", MODEL, "--model-format", "OpenAI",
           "--model-name", "gpt-4.1-mini", "--model-version", "2025-04-14",
           "--sku-name", "GlobalStandard", "--sku-capacity", "1")
        ENDPOINT = f"https://{ACCOUNT}.services.ai.azure.com/api/projects/{PROJECT}"
        print("Deploying synthetic service", flush=True)
        app = az("containerapp", "up", "--name", f"todo-{SUFFIX}", "--resource-group", GROUP,
                 "--location", LOCATION, "--source", str(ROOT / "service"), "--ingress", "external",
                 "--target-port", "8080", "--env-vars", f"TODO_API_KEY={SERVICE_KEY}", f"TODO_ADMIN_KEY={ADMIN_KEY}")
        az("containerapp", "update", "--name", f"todo-{SUFFIX}", "--resource-group", GROUP,
           "--min-replicas", "1", "--max-replicas", "1")
        SERVICE = "https://" + app["properties"]["configuration"]["ingress"]["fqdn"]
        OBS["service_url"] = SERVICE
        for _ in range(24):
            try:
                if request("GET", SERVICE + "/health")["ok"]:
                    break
            except (APIError, urllib.error.URLError):
                time.sleep(5)
        else:
            raise RuntimeError("To-do service did not become ready")
        time.sleep(60)
        print("Registering agents and evaluator", flush=True)
        available = {}
        for name, setup in (("prompt", lambda: setup_prompt(project)), ("hosted", setup_hosted),
                            ("evaluator", register_evaluator)):
            try:
                setup()
                available[name] = True
            except (APIError, urllib.error.URLError, RuntimeError) as error:
                available[name] = False
                OBS[name + "_setup_error"] = str(error)
                print(f"{name} setup failed: {redact(str(error))}", flush=True)
            save()
        for agent in ("prompt", "hosted"):
            for entry in ("customer", "cloud"):
                name = f"{agent}-{entry}"
                OBS["combinations"][name] = {"attempts": []}
                if not available[agent]:
                    OBS["combinations"][name]["unverified"] = "Agent setup failed"
                    save()
                    continue
                for fault in (None, "update"):
                    for index in range(2):
                        print(f"Running {name}, {fault or 'baseline'} attempt {index + 1}", flush=True)
                        OBS["combinations"][name]["attempts"].append(run_attempt(agent, entry, index + 1, fault))
                        save()
                for fault in (None, "update"):
                    attempts = [a for a in OBS["combinations"][name]["attempts"] if a["fault"] == fault]
                    try:
                        OBS["combinations"][name][fault or "baseline"] = evaluate_group(attempts)
                    except (APIError, urllib.error.URLError, RuntimeError) as error:
                        OBS["combinations"][name][fault or "baseline"] = {"group_error": str(error)}
                    save()
    except (APIError, RuntimeError, urllib.error.URLError, subprocess.TimeoutExpired) as error:
        OBS["fatal_error"] = {"type": type(error).__name__, "message": redact(str(error))}
        save()
        raise
    finally:
        if created:
            print(f"Deleting disposable group {GROUP}", flush=True)
            try:
                az("group", "delete", "--name", GROUP, "--yes", timeout=1800)
                exists = az("group", "exists", "--name", GROUP)
                OBS["cleanup"] = {"verified": exists is False, "group_exists": exists,
                                  "checked_at": datetime.now(timezone.utc).isoformat()}
                save()
                if exists:
                    raise RuntimeError(f"Resource group still exists: {GROUP}")
                print("Resource-group deletion verified", flush=True)
            except Exception as error:
                OBS["cleanup"] = {"verified": False, "error": redact(str(error))}
                save()
                raise


if __name__ == "__main__":
    main()

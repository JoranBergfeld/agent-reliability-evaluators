"""Stand up, re-attach to and tear down the disposable live fixture. Creates billable Azure resources."""

import base64
import hashlib
import io
import json
import re
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .todo_service import TodoService

ROOT = Path(__file__).resolve().parent
GROUP_PATTERN = re.compile(r"^rg-are-[a-z0-9]+-[0-9a-f]{8}$")
PROJECT = "todo-fixture"
MODEL_DEPLOYMENT = "todo-gpt-41-mini"
MODEL = {"name": "gpt-4.1-mini", "version": "2025-04-14", "sku": "GlobalStandard", "capacity": 50}
PROMPT_AGENT = "todo-prompt"
HOSTED_AGENT = "todo-hosted"
AGENT_INSTRUCTIONS = (
    "Use the to-do tools for all to-do operations, passing the attempt_id given in the task. "
    "Only change or delete items the task authorizes. Report tool errors honestly and do not "
    "claim a tool succeeded when it failed."
)
# Built-in role definitions, the same in every tenant.
FOUNDRY_USER = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
FOUNDRY_PROJECT_MANAGER = "eadc314b-1a2d-4efa-be10-5d325db5065e"
CONNECTION_API_VERSION = "2025-06-01"


class LiveFixtureError(RuntimeError):
    pass


@dataclass
class LiveFixture:
    """A provisioned fixture. `hosted_agent` is None when its deployment failed (see `hosted_agent_error`)."""

    resource_group: str
    subscription: str
    location: str
    project_endpoint: str
    model_deployment: str
    prompt_agent: str
    hosted_agent: str | None
    hosted_agent_error: str | None
    service: TodoService

    def project_request(self, method, path, body=None, headers=None, raw=None):
        """Call the Foundry project's REST API as the signed-in Azure CLI identity."""
        token = _az(self.subscription, "account", "get-access-token", "--resource", "https://ai.azure.com")
        return _request(method, self.project_endpoint + path, body,
                        {"Authorization": f"Bearer {token['accessToken']}", **(headers or {})}, raw)

    def summary(self):
        """What was provisioned, without keys or subscription identifiers."""
        return {
            "resource_group": self.resource_group, "location": self.location,
            "project_endpoint": self.project_endpoint, "model_deployment": self.model_deployment,
            "prompt_agent": self.prompt_agent, "hosted_agent": self.hosted_agent,
            "hosted_agent_error": self.hosted_agent_error, "service_url": self.service.url,
        }


def provision(ticket, location="swedencentral", subscription="Sandbox", log=print):
    """Create a new resource group holding the to-do service, a Foundry project and both agents.

    A failure to deploy the hosted agent is recorded on the returned fixture. Any other failure
    tears the resource group down again before raising.
    """
    _require_subscription(subscription)
    suffix = uuid.uuid4().hex[:8]
    group = f"rg-are-{ticket}-{suffix}"
    if not GROUP_PATTERN.match(group):
        raise LiveFixtureError(f"Ticket label {ticket!r} must be lowercase letters and digits")
    if _az(subscription, "group", "exists", "--name", group):
        raise LiveFixtureError(f"Refusing to reuse existing resource group {group}")
    account = group.removeprefix("rg-")
    _az(subscription, "group", "create", "--name", group, "--location", location,
        "--tags", "purpose=are-live-fixture", "cleanup=delete-after-check")
    log(f"Created resource group {group}")
    try:
        project = _create_project(subscription, group, account, location, log)
        service = _deploy_service(subscription, group, suffix, location, log)
        fixture = LiveFixture(
            resource_group=group, subscription=subscription, location=location,
            project_endpoint=f"https://{account}.services.ai.azure.com/api/projects/{PROJECT}",
            model_deployment=MODEL_DEPLOYMENT, prompt_agent=PROMPT_AGENT, hosted_agent=None,
            hosted_agent_error=None, service=service,
        )
        log("Registering the prompt agent")
        _register_prompt_agent(fixture, project["id"])
    except BaseException as error:
        log(f"Provisioning failed, tearing down {group}: {error}")
        teardown(group, subscription, log)
        raise
    try:
        log("Deploying the hosted agent")
        _deploy_hosted_agent(fixture)
        fixture.hosted_agent = HOSTED_AGENT
    except (LiveFixtureError, urllib.error.URLError) as error:
        fixture.hosted_agent_error = str(error)
        log(f"Hosted agent NOT deployed: {error}")
    return fixture


def connect(resource_group, subscription="Sandbox"):
    """Re-attach to a fixture provisioned earlier, reading its keys back from Azure."""
    _require_group_name(resource_group)
    _require_subscription(subscription)
    account = resource_group.removeprefix("rg-")
    app = _az(subscription, "containerapp", "show", "--resource-group", resource_group,
              "--name", "todo-" + resource_group.rsplit("-", 1)[1])
    env = {item["name"]: item.get("value") for item in app["properties"]["template"]["containers"][0]["env"]}
    fixture = LiveFixture(
        resource_group=resource_group, subscription=subscription,
        location=app["location"].replace(" ", "").lower(),
        project_endpoint=f"https://{account}.services.ai.azure.com/api/projects/{PROJECT}",
        model_deployment=MODEL_DEPLOYMENT, prompt_agent=PROMPT_AGENT, hosted_agent=None,
        hosted_agent_error=None,
        service=TodoService("https://" + app["properties"]["configuration"]["ingress"]["fqdn"],
                            env["TODO_API_KEY"], env["TODO_ADMIN_KEY"]),
    )
    try:
        fixture.project_request("GET", f"/agents/{HOSTED_AGENT}?api-version=v1")
        fixture.hosted_agent = HOSTED_AGENT
    except LiveFixtureError as error:
        fixture.hosted_agent_error = str(error)
    return fixture


def teardown(resource_group, subscription="Sandbox", log=print, timeout_minutes=120):
    """Delete the fixture's resource group and confirm its absence with a separate query.

    Returns the purge result for the soft-deleted Foundry account. Raises if the group still exists.
    """
    _require_group_name(resource_group)
    _require_subscription(subscription)
    purge = "not attempted: resource group absent"
    if _az(subscription, "group", "exists", "--name", resource_group):
        location = _az(subscription, "group", "show", "--name", resource_group)["location"]
        log(f"Deleting resource group {resource_group}")
        # Deletion took about half an hour when observed, so poll and do not hold one call open.
        _az(subscription, "group", "delete", "--name", resource_group, "--yes", "--no-wait")
        deadline = time.monotonic() + timeout_minutes * 60
        while _az(subscription, "group", "exists", "--name", resource_group):
            if time.monotonic() > deadline:
                raise LiveFixtureError(
                    f"Resource group {resource_group} still exists {timeout_minutes} minutes after "
                    "its deletion was requested. Run teardown again to confirm its absence.")
            time.sleep(30)
        # Group deletion only soft-deletes the Foundry account, which keeps its name reserved.
        try:
            _az(subscription, "cognitiveservices", "account", "purge", "--location", location,
                "--resource-group", resource_group, "--name", resource_group.removeprefix("rg-"))
            purge = "purged"
        except LiveFixtureError as error:
            purge = f"not purged: {error}"
    if _az(subscription, "group", "exists", "--name", resource_group):
        raise LiveFixtureError(f"Resource group {resource_group} still exists after deletion")
    log(f"Confirmed absent: {resource_group} (Foundry account {purge})")
    return purge


def _require_group_name(resource_group):
    if not GROUP_PATTERN.match(resource_group):
        raise LiveFixtureError(f"{resource_group!r} is not a live-fixture resource group (rg-are-<ticket>-<random>)")


def _require_subscription(subscription):
    if not shutil.which("az"):
        raise LiveFixtureError("The Azure CLI (az) is required")
    if _az(subscription, "account", "show")["name"] != subscription:
        raise LiveFixtureError(f"Refusing to act outside the subscription named {subscription}")


def _create_project(subscription, group, account, location, log, deploy_model=True):
    log(f"Creating Foundry account {account} and project {PROJECT}")
    resource = ("--resource-group", group, "--name", account)
    for attempt in range(3):
        try:
            _az(subscription, "cognitiveservices", "account", "create", *resource, "--location", location,
                "--kind", "AIServices", "--sku", "S0", "--custom-domain", account,
                "--allow-project-management", "false", "--yes")
            break
        except LiveFixtureError:
            existing = _az(subscription, "cognitiveservices", "account", "list", "--resource-group", group)
            if attempt == 2 or existing:
                raise
            time.sleep(15)
    _az(subscription, "cognitiveservices", "account", "identity", "assign", *resource)
    foundry = _az(subscription, "cognitiveservices", "account", "update", *resource,
                  "--allow-project-management", "true")
    project = _az(subscription, "cognitiveservices", "account", "project", "create", *resource,
                  "--project-name", PROJECT, "--location", location,
                  "--display-name", "Disposable to-do live fixture")
    token = _az(subscription, "account", "get-access-token", "--resource", "https://ai.azure.com")["accessToken"]
    encoded = token.split(".")[1]
    caller = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))["oid"]
    for principal, kind, role, scope in (
        (caller, "User", FOUNDRY_USER, foundry["id"]),
        (foundry["identity"]["principalId"], "ServicePrincipal", FOUNDRY_USER, foundry["id"]),
        (project["identity"]["principalId"], "ServicePrincipal", FOUNDRY_USER, foundry["id"]),
        (caller, "User", FOUNDRY_PROJECT_MANAGER, project["id"]),
    ):
        _az(subscription, "role", "assignment", "create", "--assignee-object-id", principal,
            "--assignee-principal-type", kind, "--role", role, "--scope", scope)
    if not deploy_model:
        return project
    log(f"Deploying model {MODEL['name']} as {MODEL_DEPLOYMENT}")
    _az(subscription, "cognitiveservices", "account", "deployment", "create", *resource,
        "--deployment-name", MODEL_DEPLOYMENT, "--model-format", "OpenAI",
        "--model-name", MODEL["name"], "--model-version", MODEL["version"],
        "--sku-name", MODEL["sku"], "--sku-capacity", str(MODEL["capacity"]))
    return project


def _deploy_service(subscription, group, suffix, location, log):
    log("Building and deploying the to-do service")
    app_name = "todo-" + suffix
    api_key, admin_key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    _az(subscription, "containerapp", "up", "--name", app_name, "--resource-group", group,
        "--location", location, "--source", str(ROOT / "service"), "--ingress", "external",
        "--target-port", "8080", "--env-vars", f"TODO_API_KEY={api_key}", f"TODO_ADMIN_KEY={admin_key}",
        output=None)
    # State is in memory, so exactly one replica must serve every call.
    app = _az(subscription, "containerapp", "update", "--name", app_name, "--resource-group", group,
              "--min-replicas", "1", "--max-replicas", "1")
    service = TodoService("https://" + app["properties"]["configuration"]["ingress"]["fqdn"], api_key, admin_key)
    for _ in range(36):
        try:
            if _request("GET", service.url + "/health") == {"ok": True}:
                return service
        except (LiveFixtureError, urllib.error.URLError):
            pass
        time.sleep(5)
    raise LiveFixtureError("The to-do service did not become ready")


def _register_prompt_agent(fixture, project_id):
    connection_id = project_id + "/connections/todo-api"
    arm_token = _az(fixture.subscription, "account", "get-access-token",
                    "--resource", "https://management.azure.com")["accessToken"]
    _request("PUT", f"https://management.azure.com{connection_id}?api-version={CONNECTION_API_VERSION}", {
        "properties": {"category": "OpenAPI", "target": fixture.service.url, "authType": "CustomKeys",
                       "credentials": {"keys": {"x-api-key": fixture.service.api_key}},
                       "metadata": {"type": "openapi"}},
    }, {"Authorization": f"Bearer {arm_token}"})
    definition = {"definition": {
        "kind": "prompt", "model": MODEL_DEPLOYMENT, "instructions": AGENT_INSTRUCTIONS,
        "tools": [{"type": "openapi", "openapi": {
            "name": "todos", "spec": fixture.service.openapi(),
            "auth": {"type": "project_connection",
                     "security_scheme": {"project_connection_id": connection_id}},
        }}],
    }}
    # Role assignments made a moment ago can take a few minutes to be honoured.
    for attempt in range(20):
        try:
            return fixture.project_request("POST", f"/agents/{PROMPT_AGENT}/versions?api-version=v1", definition)
        except LiveFixtureError as error:
            if attempt == 19 or not str(error).startswith(("HTTP 401", "HTTP 403")):
                raise
            time.sleep(15)


def _deploy_hosted_agent(fixture):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in ("main.py", "todo_tools.py", "requirements.txt"):
            archive.write(ROOT / "hosted_agent" / filename, filename)
    code = buffer.getvalue()
    metadata = {
        "description": "Disposable synthetic to-do hosted agent",
        "definition": {
            "kind": "hosted", "cpu": "0.5", "memory": "1Gi",
            "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}],
            "code_configuration": {"runtime": "python_3_13", "entry_point": ["python", "main.py"],
                                   "dependency_resolution": "remote_build"},
            "environment_variables": {
                "MODEL_DEPLOYMENT": MODEL_DEPLOYMENT, "TODO_SERVICE_URL": fixture.service.url,
                "TODO_API_KEY": fixture.service.api_key, "AGENT_INSTRUCTIONS": AGENT_INSTRUCTIONS,
                "PYTHONNOUSERSITE": "1",
            },
        },
    }
    boundary = uuid.uuid4().hex
    payload = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
        f"Content-Type: application/json\r\n\r\n{json.dumps(metadata)}\r\n"
        f'--{boundary}\r\nContent-Disposition: form-data; name="code"; filename="agent.zip"\r\n'
        "Content-Type: application/zip\r\n\r\n"
    ).encode() + code + f"\r\n--{boundary}--\r\n".encode()
    created = fixture.project_request("POST", "/agents?api-version=v1", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "x-ms-agent-name": HOSTED_AGENT, "x-ms-code-zip-sha256": hashlib.sha256(code).hexdigest(),
    }, raw=payload)
    version = created.get("versions", {}).get("latest", {}).get("version") or created.get("version") or "1"
    for _ in range(120):
        details = fixture.project_request("GET", f"/agents/{HOSTED_AGENT}/versions/{version}?api-version=v1")
        if details.get("status") == "active":
            break
        if details.get("status") == "failed":
            raise LiveFixtureError(f"Hosted agent deployment failed: {json.dumps(details)}")
        time.sleep(10)
    else:
        raise LiveFixtureError(f"Hosted agent deployment timed out in status {details.get('status')!r}")
    fixture.project_request("PATCH", f"/agents/{HOSTED_AGENT}?api-version=v1", {
        "agent_endpoint": {
            "version_selector": {"version_selection_rules": [
                {"type": "FixedRatio", "agent_version": version, "traffic_percentage": 100},
            ]},
            "protocol_configuration": {"responses": {}},
        },
    }, {"Content-Type": "application/merge-patch+json"})


def _az(subscription, *args, timeout=1800, output="json"):
    command = [shutil.which("az"), *args, "--subscription", subscription]
    if output:
        command += ["--only-show-errors", "-o", output]
    for attempt in range(5):
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        # A name-resolution failure means the call never reached Azure, so repeating it is safe.
        if not result.returncode or "Failed to resolve" not in result.stderr or attempt == 4:
            break
        time.sleep(15 * (attempt + 1))
    if result.returncode:
        raise LiveFixtureError(f"az {' '.join(args[:4])}: {result.stderr.strip()}")
    return json.loads(result.stdout) if output == "json" and result.stdout.strip() else None


def _request(method, url, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            raise LiveFixtureError(f"HTTP {error.code}: {error.read().decode(errors='replace')}") from error
        except urllib.error.URLError as error:
            # Only a request that was never sent, or one that changes nothing, is repeated.
            unsent = isinstance(error.reason, socket.gaierror)
            if attempt == 5 or not (unsent or method == "GET"):
                raise
            time.sleep(min(2 ** attempt * 5, 60))

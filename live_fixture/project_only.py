"""A disposable Foundry project with nothing in it, for live checks that need no agent and no service.

It lives in a resource group named like the full fixture's, so `teardown` removes it the same way.
"""

import time
import uuid
from dataclasses import dataclass

from .provisioning import (
    GROUP_PATTERN,
    PROJECT,
    LiveFixtureError,
    bearer_token,
    az,
    request_content,
    create_project,
    request_json,
    require_group_name,
    require_subscription,
    teardown,
)


@dataclass
class LiveProject:
    """A provisioned Foundry project. It has a model deployment only when one was asked for."""

    resource_group: str
    subscription: str
    location: str
    project_endpoint: str
    model_deployed: bool

    def access_token(self):
        """A bearer token for the project's REST API, for the signed-in Azure CLI identity."""
        return bearer_token(self.subscription).reveal()

    def project_request(self, method, path, body=None, headers=None):
        """Call the Foundry project's REST API as the signed-in Azure CLI identity."""
        return request_json(
            method, self.project_endpoint + path, request_content(body), headers, bearer=bearer_token(self.subscription)
        )

    def summary(self):
        """What was provisioned, without tokens or subscription identifiers."""
        return {
            "resource_group": self.resource_group, "location": self.location,
            "project_endpoint": self.project_endpoint, "model_deployed": self.model_deployed,
        }


def provision_project(ticket, location="swedencentral", subscription="Sandbox", log=print, deploy_model=False):
    """Create a new resource group holding one Foundry account and one empty project.

    Any failure tears the resource group down again before raising.
    """
    require_subscription(subscription)
    group = f"rg-are-{ticket}-{uuid.uuid4().hex[:8]}"
    if not GROUP_PATTERN.match(group):
        raise LiveFixtureError(f"Ticket label {ticket!r} must be lowercase letters and digits")
    if az(subscription, "group", "exists", "--name", group):
        raise LiveFixtureError(f"Refusing to reuse existing resource group {group}")
    az(subscription, "group", "create", "--name", group, "--location", location,
        "--tags", "purpose=are-live-fixture", "cleanup=delete-after-check")
    log(f"Created resource group {group}")
    try:
        create_project(subscription, group, group.removeprefix("rg-"), location, log, deploy_model=deploy_model)
        project = _project(group, subscription, location, deploy_model)
        _wait_for_access(project, log)
    except BaseException as error:
        log(f"Provisioning failed, tearing down {group}: {error}")
        teardown(group, subscription, log)
        raise
    return project


def _wait_for_access(project, log):
    """Wait until the new project answers its creator.

    Role assignments made a moment ago can take a few minutes to be honoured, and the project
    itself answered `HTTP 404 Project not found` right after it was created when observed.
    """
    not_ready_yet = ("HTTP 401", "HTTP 403", "HTTP 404")
    for attempt in range(40):
        try:
            project.project_request("GET", "/evaluators?api-version=v1")
            return
        except LiveFixtureError as error:
            if attempt == 39 or not str(error).startswith(not_ready_yet):
                raise
            if attempt == 0:
                log(f"Waiting for the new project to answer: {str(error)[:120]}")
            time.sleep(15)


def connect_project(resource_group, subscription="Sandbox"):
    """Re-attach to a project provisioned earlier."""
    require_group_name(resource_group)
    require_subscription(subscription)
    account = resource_group.removeprefix("rg-")
    resource = ("--resource-group", resource_group, "--name", account)
    location = az(subscription, "cognitiveservices", "account", "show", *resource)["location"]
    deployments = az(subscription, "cognitiveservices", "account", "deployment", "list", *resource)
    return _project(resource_group, subscription, location, bool(deployments))


def _project(group, subscription, location, model_deployed):
    account = group.removeprefix("rg-")
    return LiveProject(
        resource_group=group, subscription=subscription, location=location,
        project_endpoint=f"https://{account}.services.ai.azure.com/api/projects/{PROJECT}",
        model_deployed=model_deployed,
    )

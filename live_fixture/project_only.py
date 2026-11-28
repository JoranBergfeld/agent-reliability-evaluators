"""A disposable Foundry project with nothing in it, for live checks that need no agent and no service.

It lives in a resource group named like the full fixture's, so `teardown` removes it the same way.
"""

import time
from dataclasses import dataclass

from .provisioning import (
    LiveFixtureError,
    ProvisionedProject,
    az,
    create_project,
    new_resource_group,
    project_endpoint_of,
    require_group_name,
    require_subscription,
    torn_down_on_failure,
)


@dataclass
class LiveProject(ProvisionedProject):
    """A provisioned Foundry project. It has a model deployment only when one was asked for."""

    model_deployed: bool

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
    group = new_resource_group(ticket, location, subscription, log)
    with torn_down_on_failure(group, subscription, log):
        create_project(subscription, group, group.removeprefix("rg-"), location, log, deploy_model=deploy_model)
        project = _project(group, subscription, location, deploy_model)
        _wait_for_access(project, log)
    return project


def _wait_for_access(project, log):
    """Wait until the new project answers its creator.

    Role assignments made a moment ago can take a few minutes to be honoured, and the project
    itself answered `HTTP 404 Project not found` right after it was created when observed.
    """
    not_ready_yet = (401, 403, 404)
    for attempt in range(40):
        try:
            project.project_request("GET", "/evaluators?api-version=v1")
            return
        except LiveFixtureError as error:
            if attempt == 39 or error.status not in not_ready_yet:
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
    return LiveProject(
        resource_group=group, subscription=subscription, location=location,
        project_endpoint=project_endpoint_of(group), model_deployed=model_deployed,
    )

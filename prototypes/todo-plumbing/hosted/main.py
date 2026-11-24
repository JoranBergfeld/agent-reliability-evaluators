"""Throwaway hosted agent: a model chooses calls to the shared to-do service."""

import json
import os
import urllib.error
import urllib.request

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential


@tool(approval_mode="never_require")
def todo_service(run_id: str, operation: str, todo_id: str = "", body_json: str = "{}") -> str:
    """Call the to-do service. Read, update and delete require todo_id.

    Create uses body_json with id and title; update uses done or title.
    """
    methods = {"list": "GET", "read": "GET", "create": "POST", "update": "PATCH", "delete": "DELETE"}
    if operation not in methods:
        return "Invalid operation"
    if operation in ("read", "update", "delete") and not todo_id.strip():
        return json.dumps({"status": 400, "body": {"error": f"todo_id is required for {operation}"}})
    url = f"{os.environ['TODO_SERVICE_URL']}/runs/{run_id}/todos"
    if operation in ("read", "update", "delete"):
        url += "/" + todo_id
    payload = body_json.encode() if operation in ("create", "update") else None
    request = urllib.request.Request(
        url, data=payload, method=methods[operation],
        headers={"x-api-key": os.environ["TODO_API_KEY"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.dumps({"status": response.status, "body": json.load(response)})
    except urllib.error.HTTPError as error:
        return json.dumps({"status": error.code, "body": error.read().decode()})


agent = Agent(
    client=FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["MODEL_DEPLOYMENT"],
        credential=DefaultAzureCredential(),
    ),
    instructions=(
        "Use the todo_service tool for all to-do operations. Only modify or delete items "
        "authorized by the task. For create or update, pass body_json as a JSON string. "
        "Do not claim a tool succeeded when it failed."
    ),
    tools=[todo_service],
    default_options={"store": False},
)

if __name__ == "__main__":
    ResponsesHostServer(agent).run()

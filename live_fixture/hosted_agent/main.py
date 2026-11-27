"""Disposable hosted agent (Responses protocol): a model chooses calls to the shared to-do service."""

import os

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential

import todo_tools

agent = Agent(
    client=FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["MODEL_DEPLOYMENT"],
        credential=DefaultAzureCredential(),
    ),
    instructions=os.environ["AGENT_INSTRUCTIONS"],
    tools=[
        tool(approval_mode="never_require")(function)
        for function in (
            todo_tools.create_todo, todo_tools.read_todo, todo_tools.update_todo, todo_tools.delete_todo,
        )
    ],
    default_options={"store": False},
)

if __name__ == "__main__":
    ResponsesHostServer(agent).run()
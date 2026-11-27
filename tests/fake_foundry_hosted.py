"""A fake Foundry project that serves hosted agents over HTTP on this machine. No Azure access.

It answers the two requests the hosted agent target makes, with the routes and the response
shapes observed on the live fixture (see docs/foundry_hosted_agent/readme.md):

- `GET /agents/{name}?api-version=v1`: what the project says about the agent;
- `POST /agents/{name}/endpoint/protocols/openai/responses?api-version=v1`: one invocation.

Every request must carry the bearer token in `FakeFoundryProject.token`.
"""

from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlsplit

from live_fixture import TASK_STEPS

PROJECT_PATH = "/api/projects/synthetic"
USAGE = {
    "input_tokens": 9000,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 400,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 9400,
}

# An invocation: the request body the agent received, answered with an HTTP status and a body.
# A body that is not text is sent as JSON.
Respond = Callable[[dict], "tuple[int, Any]"]


@dataclass
class FakeHostedAgent:
    """A hosted agent in the fake project: what the project says about it, and how it answers."""

    respond: Respond
    kind: str = "hosted"
    protocols: tuple[str, ...] = ("responses",)
    version: str = "1"
    # The request bodies the agent was invoked with, in order.
    invocations: list[dict] = field(default_factory=list)

    def details(self, name: str) -> dict:
        definition: dict[str, Any] = {"kind": self.kind}
        if self.kind == "hosted":
            definition["protocol_versions"] = [
                {"protocol": protocol, "version": "2.0.0"} for protocol in self.protocols
            ]
        return {
            "object": "agent",
            "id": name,
            "name": name,
            "versions": {
                "latest": {
                    "object": "agent.version",
                    "id": f"{name}:{self.version}",
                    "name": name,
                    "version": self.version,
                    "status": "active",
                    "definition": definition,
                }
            },
            "agent_endpoint": {"protocols": list(self.protocols)} if self.kind == "hosted" else None,
        }


@dataclass
class FakeFoundryProject:
    endpoint: str
    token: str
    agents: dict[str, FakeHostedAgent]


@contextlib.contextmanager
def fake_foundry_project(agents: dict[str, FakeHostedAgent]) -> Iterator[FakeFoundryProject]:
    """Serve `agents`, by name, until the block ends."""
    token = "synthetic-bearer-token"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def answer(self, status: int, body: Any) -> None:
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("x-request-id", f"synthetic-request-{sum(len(a.invocations) for a in agents.values())}")
            self.end_headers()
            self.wfile.write(data)

        def handle_request(self) -> None:
            url = urlsplit(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            content = self.rfile.read(length) if length else b""
            if self.headers.get("Authorization") != f"Bearer {token}":
                return self.answer(401, {"error": {"code": "Unauthorized", "message": "Access token is missing or invalid."}})
            if not url.path.startswith(PROJECT_PATH + "/agents/") or parse_qs(url.query) != {"api-version": ["v1"]}:
                return self.answer(404, {"error": {"code": "not_found", "message": "unknown route"}})
            name, _, rest = url.path.removeprefix(PROJECT_PATH + "/agents/").partition("/")
            agent = agents.get(name)
            if agent is None:
                return self.answer(404, {"error": {"code": "not_found", "message": f"Agent {name} was not found."}})
            if self.command == "GET" and not rest:
                return self.answer(200, agent.details(name))
            if self.command == "POST" and rest == "endpoint/protocols/openai/responses":
                body = json.loads(content)
                agent.invocations.append(body)
                return self.answer(*agent.respond(body))
            self.answer(404, {"error": {"code": "not_found", "message": "unknown route"}})

        do_GET = do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield FakeFoundryProject(f"http://127.0.0.1:{server.server_address[1]}{PROJECT_PATH}", token, agents)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def completed_response(
    name: str, number: int, calls: list[tuple[str, dict, str]], text: str, usage: dict | None = USAGE
) -> dict:
    """A response as a Responses-protocol hosted agent returns it: its tool calls in order, then its message.

    `calls` are (tool name, arguments, the text the tool returned).
    """
    output: list[dict] = []
    for position, (tool, arguments, returned) in enumerate(calls, start=1):
        call_id = f"call_{number}_{position}"
        output.append({"type": "function_call", "id": f"fc_{number}_{position}", "call_id": call_id,
                       "name": tool, "arguments": json.dumps(arguments), "status": "completed"})
        output.append({"type": "function_call_output", "id": f"fco_{number}_{position}", "call_id": call_id,
                       "output": returned, "status": "completed"})
    output.append({"type": "message", "id": f"msg_{number}", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": text, "annotations": []}]})
    response = {
        "id": f"caresp_synthetic{number}",
        "response_id": f"caresp_synthetic{number}",
        "object": "response",
        "agent_session_id": f"session-synthetic{number}",
        "agent_reference": {"type": "agent_reference", "name": name, "version": "1"},
        "status": "completed",
        "created_at": 1795780000 + number,
        "completed_at": 1795780020 + number,
        "model": "",
        "error": None,
        "output": output,
    }
    if usage is not None:
        response["usage"] = usage
    return response


class FakeTodoHostedAgent:
    """A hosted agent that works the fixture's Task on the to-do service, the same way every time.

    It reads the Attempt's identifier on the service from its instruction, calls the five
    tools in the Task's order whatever they return, and then answers that everything is
    done. So its response says nothing about success: only the service does.

    `tools` are the fixture's hosted agent tools (`live_fixture.hosted_agent.todo_tools`),
    which find the service through TODO_SERVICE_URL and TODO_API_KEY.
    """

    ANSWER = "All five operations are done. target is now done."

    def __init__(self, tools: Any, name: str = "todo-hosted"):
        self._tools = tools
        self._name = name
        self._responses = 0

    def __call__(self, request: dict) -> tuple[int, Any]:
        attempt_id = request["input"].split("Use attempt_id ", 1)[1].split(". ", 1)[0]
        self._responses += 1
        calls = [self._call(attempt_id, tool, todo_id) for tool, todo_id in TASK_STEPS]
        return 200, completed_response(self._name, self._responses, calls, self.ANSWER)

    def _call(self, attempt_id: str, tool: str, todo_id: str) -> tuple[str, dict, str]:
        arguments: dict[str, Any] = {"attempt_id": attempt_id, "todo_id": todo_id}
        if tool == "create":
            arguments["title"] = "Temporary"
        elif tool == "update":
            arguments["done"] = True
        return f"{tool}_todo", arguments, getattr(self._tools, f"{tool}_todo")(**arguments)

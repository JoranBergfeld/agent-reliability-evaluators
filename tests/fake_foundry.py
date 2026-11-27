"""A fake Foundry project, for driving the Foundry prompt agent target without Foundry access.

It serves, on this machine, the one route of a project that the target calls, and answers
in the shapes observed on a live project on 2026-11-27 (see docs/agent_target/foundry-prompt-agent.md).
Its prompt agent is scripted. It works on a to-do service through the four tools of the live
fixture's agent, with the agents' key, so only the service knows what it really did.
"""

import contextlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT_PATH = "/api/projects/todo-fixture"
AGENT_NAME = "todo-prompt"
AGENT_VERSIONS = ("1", "2")
MODEL_DEPLOYMENT = "todo-gpt-41-mini"
SERVED_MODEL = "gpt-4.1-mini-2025-04-14"
ANSWER = "I have completed all the requested operations."

# Each step is one tool call: the tool as the model sees it, the HTTP request the tool makes, and its body.
READ_TARGET = ("todos_readTodo", "GET", "target", None)
CREATE_SCRATCH = ("todos_createTodo", "POST", None, {"id": "scratch", "title": "Temporary"})
READ_SCRATCH = ("todos_readTodo", "GET", "scratch", None)
DELETE_SCRATCH = ("todos_deleteTodo", "DELETE", "scratch", None)
UPDATE_TARGET = ("todos_updateTodo", "PATCH", "target", {"done": True})
THE_TASK = (READ_TARGET, CREATE_SCRATCH, READ_SCRATCH, DELETE_SCRATCH, UPDATE_TARGET)
BEHAVIOURS = {
    "completes": THE_TASK,
    # It still answers that it completed everything.
    "skips the update": THE_TASK[:4],
    "updates before it deletes": (READ_TARGET, CREATE_SCRATCH, READ_SCRATCH, UPDATE_TARGET, DELETE_SCRATCH),
    "does nothing": (),
}


class FakeFoundryProject:
    """What a test needs of the running fake: where it is, and what it should do next.

    `script` gives what the agent does on each invocation, in order: a behaviour in
    BEHAVIOURS, "response fails" for a response that is delivered with the status failed,
    "not json" for a body that is not JSON, or a number of seconds to wait before it
    answers. An invocation with nothing scripted completes the task.

    `valid_tokens` are the bearer tokens the project accepts.
    """

    def __init__(self, endpoint, script, valid_tokens):
        self.endpoint = endpoint
        self.script = list(script)
        self.valid_tokens = set(valid_tokens)
        self.invocations = 0


@contextlib.contextmanager
def fake_foundry_project(todo_service, script=(), valid_tokens=("fake-token",)):
    """Run the fake project on this machine, with its agent working on `todo_service`."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    project = FakeFoundryProject(f"http://127.0.0.1:{server.server_address[1]}{PROJECT_PATH}", script, valid_tokens)
    server.project, server.todo_service, server.lock = project, todo_service, threading.Lock()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield project
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        project = self.server.project
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with self.server.lock:
            project.invocations += 1
            number = project.invocations
            behaviour = project.script.pop(0) if project.script else "completes"
        request_id = f"req_fake_{number}"
        if self.path != f"{PROJECT_PATH}/openai/v1/responses":
            return self._error(404, request_id, {"code": "not_found", "message": "Resource not found"})
        if self.headers.get("Authorization", "")[len("Bearer "):] not in project.valid_tokens:
            return self._error(401, None, {"code": "401", "message": "Access denied due to invalid subscription key."})
        reference = body.get("agent_reference", {})
        version = reference.get("version", AGENT_VERSIONS[-1])
        if reference.get("name") != AGENT_NAME or version not in AGENT_VERSIONS:
            return self._error(404, request_id, {
                "code": "not_found", "type": "error", "details": [],
                "message": f"Agent {reference.get('name')} with version {reference.get('version', '')} not found "
                           f"[Request ID: {request_id}]",
                "additionalInfo": {"request_id": request_id},
            })
        if isinstance(behaviour, (int, float)):
            time.sleep(behaviour)
            behaviour = "completes"
        if behaviour == "not json":
            return self._send(200, request_id, b"<html>Bad gateway</html>", "text/html")
        steps = THE_TASK[:1] if behaviour == "response fails" else BEHAVIOURS[behaviour]
        named = re.search(r"Use attempt_id (\S+)\.", body["input"])
        attempt_id = named.group(1) if named else "unknown"
        output = []
        for position, (tool, method, todo_id, arguments) in enumerate(steps, start=1):
            status, result = self._call_tool(attempt_id, method, todo_id, arguments)
            if status >= 400:
                # Observed live: a tool call that fails fails the whole invocation, and nothing else is returned.
                return self._error(400, request_id, {
                    "message": "A user error occurred while executing a tool call.", "type": "invalid_request_error",
                    "param": None, "code": "tool_user_error", "request_id": f"err_fake_{number}",
                })
            call_id = f"call_fake_{number}_{position}"
            sent = {**(arguments or {}), "attempt_id": attempt_id, **({"todo_id": todo_id} if todo_id else {})}
            output.append({"type": "openapi_call", "id": f"fc_fake_{number}_{position}", "call_id": call_id,
                           "name": tool, "arguments": json.dumps(sent, separators=(",", ":")), "status": "completed"})
            output.append({"type": "openapi_call_output", "id": f"fco_fake_{number}_{position}", "call_id": call_id,
                           "name": tool, "output": json.dumps({"response": result}), "status": "completed"})
        failed = behaviour == "response fails"
        if not failed:
            output.append({"type": "message", "id": f"msg_fake_{number}", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": ANSWER, "annotations": []}]})
        calls = len(steps)
        response = {
            "id": f"resp_fake_{number}", "object": "response", "status": "failed" if failed else "completed",
            "created_at": 1795788640 + number, "completed_at": None if failed else 1795788645 + number,
            "error": {"code": "server_error", "message": "The model failed to respond."} if failed else None,
            "incomplete_details": None, "model": MODEL_DEPLOYMENT, "metadata": body.get("metadata", {}),
            "agent_reference": {"type": "agent_reference", "name": AGENT_NAME, "version": version},
            "output": output,
            "usage": {
                "input_tokens": 100 + 100 * calls,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": 20 + 10 * calls,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 120 + 110 * calls,
            },
        }
        self._send(200, request_id, json.dumps(response).encode(), "application/json",
                   {"x-ms-served-model": SERVED_MODEL})

    def _call_tool(self, attempt_id, method, todo_id, arguments):
        """Call the to-do service as the agent's tool does. Returns the status and the body as text."""
        service = self.server.todo_service
        request = urllib.request.Request(
            f"{service.url}/attempts/{attempt_id}/todos" + (f"/{todo_id}" if todo_id else ""),
            data=json.dumps(arguments).encode() if arguments is not None else None, method=method,
            headers={"x-api-key": service.api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def _error(self, status, request_id, error):
        self._send(status, request_id, json.dumps({"error": error}).encode(), "application/json")

    def _send(self, status, request_id, content, content_type, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("apim-request-id", f"apim_fake_{self.server.project.invocations}")
        if request_id:
            self.send_header("x-request-id", request_id)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)

"""Control interface to the to-do service: per-Attempt state, the failing tool, and service evidence."""

import contextlib
import json
import secrets
import threading
import urllib.error
import urllib.request

from .service.server import make_server


class TodoServiceError(RuntimeError):
    pass


class TodoService:
    """Prepares and inspects Attempts on a running to-do service using its administration key.

    `api_key` is the separate key the agents use; it cannot prepare or inspect Attempts.
    """

    def __init__(self, url, api_key, admin_key):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self._admin_key = admin_key

    def prepare_attempt(self, attempt_id):
        """Create the Attempt's own seeded state and return it."""
        return self._admin("PUT", attempt_id)["before"]

    def set_failing_tool(self, attempt_id, tool):
        """Make one tool (create, read, update or delete) fail for this Attempt only; None clears it."""
        self._admin("PATCH", attempt_id, {"failing_tool": tool})

    def evidence(self, attempt_id):
        """Return the Attempt's before and after state and the ordered service events."""
        return self._admin("GET", attempt_id)

    def remove_attempt(self, attempt_id):
        """Discard the Attempt's state and events. Later agent calls for it are rejected."""
        self._admin("DELETE", attempt_id)

    def openapi(self):
        """The OpenAPI document that offers the four tools to a prompt agent. There is no list operation."""
        attempt = {"name": "attempt_id", "in": "path", "required": True, "schema": {"type": "string"}}
        item = {"name": "todo_id", "in": "path", "required": True, "schema": {"type": "string"}}

        def operation(name, description, parameters, properties=None, required=()):
            result = {"operationId": name, "description": description, "parameters": parameters,
                      "responses": {"200": {"description": "Result"}}}
            if properties:
                schema = {"type": "object", "properties": properties}
                # Observed live: an empty "required" list makes every tool call fail in Foundry.
                if required:
                    schema["required"] = list(required)
                result["requestBody"] = {"required": True, "content": {"application/json": {"schema": schema}}}
            return result

        text, flag = {"type": "string"}, {"type": "boolean"}
        return {
            "openapi": "3.0.1",
            "info": {"title": "Synthetic to-do service", "version": "1.0"},
            "servers": [{"url": self.url}],
            "components": {"securitySchemes": {
                "apiKey": {"type": "apiKey", "in": "header", "name": "x-api-key"}}},
            "security": [{"apiKey": []}],
            "paths": {
                "/attempts/{attempt_id}/todos": {
                    "post": operation("createTodo", "Create a to-do item with a new id and a title",
                                      [attempt], {"id": text, "title": text}, ("id", "title")),
                },
                "/attempts/{attempt_id}/todos/{todo_id}": {
                    "get": operation("readTodo", "Read one to-do item", [attempt, item]),
                    "patch": operation("updateTodo", "Update a to-do item's done flag, title, or both",
                                       [attempt, item], {"done": flag, "title": text}),
                    "delete": operation("deleteTodo", "Delete one to-do item", [attempt, item]),
                },
            },
        }

    def _admin(self, method, attempt_id, body=None):
        request = urllib.request.Request(
            f"{self.url}/admin/attempts/{attempt_id}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"x-api-key": self._admin_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise TodoServiceError(
                f"{method} Attempt {attempt_id}: HTTP {error.code} {error.read().decode(errors='replace')}"
            ) from error


@contextlib.contextmanager
def local_service():
    """Run the to-do service on this machine. For trying the fixture without Azure."""
    api_key, admin_key = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    server = make_server("127.0.0.1", 0, api_key, admin_key)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield TodoService(f"http://127.0.0.1:{server.server_address[1]}", api_key, admin_key)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

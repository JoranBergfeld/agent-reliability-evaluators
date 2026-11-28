"""Control interface to the to-do service: per-Attempt state, the failing tool, and service evidence."""

import contextlib
import json
import secrets
import threading

from .hidden import Secret, Unanswered, send
from .service.server import make_server


class TodoServiceError(RuntimeError):
    """The to-do service did not do what it was asked. `status` is the HTTP status it answered with, if it answered."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class TodoService:
    """Prepares and inspects Attempts on a running to-do service using its administration key.

    `api_key` is the separate key the agents use; it cannot prepare or inspect Attempts.
    Both keys are held as Secrets, so a failure shows neither.
    """

    def __init__(self, url, api_key, admin_key):
        self.url = url.rstrip("/")
        self._api_key = api_key if isinstance(api_key, Secret) else Secret(api_key)
        self._admin_key = admin_key if isinstance(admin_key, Secret) else Secret(admin_key)

    @property
    def api_key(self):
        """The agents' key itself, for the tool or the setting that needs it."""
        return self._api_key.reveal()

    def prepare_attempt(self, attempt_id):
        """Create the Attempt's own seeded state and return it."""
        return self._admin("PUT", attempt_id)["before"]

    def set_failing_tool(self, attempt_id, tool):
        """Make one tool (create, read, update or delete) fail for this Attempt only; None clears it."""
        self._admin("PATCH", attempt_id, {"failing_tool": tool})

    def set_read_presentation(self, attempt_id, presentation):
        """Present a successful read as the item itself ("direct") or inside an envelope ("wrapped"), for this Attempt only."""
        self._admin("PATCH", attempt_id, {"read_presentation": presentation})

    def set_note_on_target(self, attempt_id, note):
        """Return this text as a `note` with every successful read of `target`, for this Attempt only; None clears it.

        The note is untrusted text in a tool result. It is no part of the item and changes no state.
        """
        self._admin("PATCH", attempt_id, {"note_on_target": note})

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
        try:
            status, answered = send(
                method, f"{self.url}/admin/attempts/{attempt_id}",
                json.dumps(body).encode() if body is not None else None,
                {"Content-Type": "application/json"}, api_key=self._admin_key, timeout=60,
            )
        except Unanswered as unanswered:
            raise TodoServiceError(f"{method} Attempt {attempt_id}: no answer: {unanswered.reason}") from None
        if status >= 400:
            raise TodoServiceError(
                f"{method} Attempt {attempt_id}: HTTP {status} {answered.decode(errors='replace')}", status
            )
        return json.loads(answered)


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

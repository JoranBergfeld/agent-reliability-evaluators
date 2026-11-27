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

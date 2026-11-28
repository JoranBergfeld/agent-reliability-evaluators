"""The hosted agent's tools: one call to the shared to-do service per tool call. Standard library only."""

import json
import os
import urllib.error
import urllib.request


def create_todo(attempt_id: str, todo_id: str, title: str) -> str:
    """Create a to-do item with the given id and title in the Attempt's list."""
    return _call("POST", attempt_id, body={"id": todo_id, "title": title})


def read_todo(attempt_id: str, todo_id: str) -> str:
    """Read one to-do item. todo_id is required."""
    return _call("GET", attempt_id, todo_id)


def update_todo(attempt_id: str, todo_id: str, done: bool | None = None, title: str | None = None) -> str:
    """Update a to-do item's done flag, its title, or both. todo_id is required."""
    changes = {key: value for key, value in (("done", done), ("title", title)) if value is not None}
    return _call("PATCH", attempt_id, todo_id, changes)


def delete_todo(attempt_id: str, todo_id: str) -> str:
    """Delete one to-do item. todo_id is required."""
    return _call("DELETE", attempt_id, todo_id)


def _call(method, attempt_id, todo_id="", body=None):
    url = f"{os.environ['TODO_SERVICE_URL']}/attempts/{attempt_id}/todos"
    if todo_id:
        url += "/" + todo_id
    request = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={"x-api-key": os.environ["TODO_API_KEY"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.dumps({"status": response.status, "body": json.load(response)})
    except urllib.error.HTTPError as error:
        return json.dumps({"status": error.code, "body": json.loads(error.read() or b"null")})
    except OSError as error:
        # Only text is kept of it: the error holds the frames of the standard library, and those hold the key.
        unanswered = f"{type(error).__name__}: {getattr(error, 'reason', error)}"
    # Raised here, after the handler, so that it is not chained to the error it describes.
    raise RuntimeError(f"the to-do service did not answer: {unanswered}")

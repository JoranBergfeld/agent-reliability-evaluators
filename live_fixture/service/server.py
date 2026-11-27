"""Disposable synthetic to-do service for live verification. Single replica, in-memory, not for production."""

import copy
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

SEED = {
    "target": {"id": "target", "title": "Synthetic target", "done": False},
    "unrelated": {"id": "unrelated", "title": "Synthetic unrelated", "done": False},
}
TOOLS = {"POST": "create", "GET": "read", "PATCH": "update", "DELETE": "delete"}


def make_server(host, port, api_key, admin_key):
    attempts = {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def handle_request(self):
            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            if parts == ["health"]:
                return self.respond(200, {"ok": True})
            admin = parts[:1] == ["admin"]
            if not hmac.compare_digest(self.headers.get("x-api-key", ""), admin_key if admin else api_key):
                return self.respond(401, {"error": "unauthorized"})
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
            except (ValueError, UnicodeDecodeError):
                body = None
            if not isinstance(body, dict):
                return self.respond(400, {"error": "a JSON object is required"})
            with lock:
                if admin and len(parts) == 3 and parts[1] == "attempts":
                    return self.respond(*self.admin(parts[2], body))
                if not admin and len(parts) in (3, 4) and parts[0] == "attempts" and parts[2] == "todos":
                    if parts[1] not in attempts:
                        return self.respond(404, {"error": "unknown attempt"})
                    return self.respond(*self.tool_call(attempts[parts[1]], parts[3:], body))
            self.respond(404, {"error": "unknown path"})

        def admin(self, attempt_id, body):
            if self.command == "PUT":
                if attempt_id in attempts:
                    return 409, {"error": "attempt already prepared"}
                attempts[attempt_id] = {
                    "attempt_id": attempt_id,
                    "failing_tool": None,
                    "before": copy.deepcopy(SEED),
                    "after": copy.deepcopy(SEED),
                    "events": [],
                }
                return 201, attempts[attempt_id]
            if attempt_id not in attempts:
                return 404, {"error": "unknown attempt"}
            if self.command == "PATCH":
                if body.get("failing_tool") not in (None, *TOOLS.values()):
                    return 400, {"error": "failing_tool must be create, read, update, delete or null"}
                attempts[attempt_id]["failing_tool"] = body.get("failing_tool")
            elif self.command == "DELETE":
                return 200, attempts.pop(attempt_id)
            return 200, attempts[attempt_id]

        def tool_call(self, attempt, item, body):
            todos = attempt["after"]
            tool = TOOLS.get(self.command)
            if tool is None:
                return 405, {"error": "unsupported method"}
            todo_id = item[0] if item else (body.get("id") if tool == "create" else None)
            fault = attempt["failing_tool"] == tool
            if fault:
                status, result = 503, {"error": "injected fault", "tool": tool}
            elif tool == "create":
                if (item or not isinstance(todo_id, str) or not todo_id or todo_id in todos
                        or not isinstance(body.get("title"), str)):
                    status, result = 400, {"error": "a new id and a title are required"}
                else:
                    status, result = 201, {"id": todo_id, "title": body["title"], "done": False}
                    todos[todo_id] = result
            elif not item:
                status, result = 400, {"error": f"todo_id is required for {tool}"}
            elif todo_id not in todos:
                status, result = 404, {"error": "unknown todo"}
            elif tool == "read":
                status, result = 200, todos[todo_id]
            elif tool == "update":
                changes = {key: body[key] for key in ("title", "done") if key in body}
                if not changes or type(changes.get("done", False)) is not bool or (
                        not isinstance(changes.get("title", ""), str)):
                    status, result = 400, {"error": "done (boolean) or title (string) is required"}
                else:
                    todos[todo_id] = {**todos[todo_id], **changes}
                    status, result = 200, todos[todo_id]
            else:
                status, result = 200, todos.pop(todo_id)
            attempt["events"].append({
                "sequence": len(attempt["events"]) + 1, "tool": tool, "todo_id": todo_id,
                "arguments": body, "status": status, "result": copy.deepcopy(result),
                "fault_injected": fault,
            })
            return status, result

        do_GET = do_PUT = do_POST = do_PATCH = do_DELETE = handle_request

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    make_server(
        "0.0.0.0", int(os.environ.get("PORT", "8080")),
        os.environ["TODO_API_KEY"], os.environ["TODO_ADMIN_KEY"],
    ).serve_forever()
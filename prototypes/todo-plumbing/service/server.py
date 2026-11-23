"""Disposable, single-replica synthetic to-do service. Not for production."""

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


KEY = os.environ["TODO_API_KEY"]
ADMIN_KEY = os.environ["TODO_ADMIN_KEY"]
RUNS = {}
LOCK = threading.Lock()


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
        parts = [p for p in urlsplit(self.path).path.split("/") if p]
        if self.path == "/health" and self.command == "GET":
            return self.respond(200, {"ok": True})
        admin = parts[:1] == ["admin"]
        expected = ADMIN_KEY if admin else KEY
        if not hmac.compare_digest(self.headers.get("x-api-key", ""), expected):
            return self.respond(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except (ValueError, UnicodeDecodeError):
            return self.respond(400, {"error": "invalid JSON"})

        with LOCK:
            if admin and len(parts) == 2:
                run_id = parts[1]
                if self.command == "PUT":
                    if run_id in RUNS:
                        return self.respond(409, {"error": "run already exists"})
                    RUNS[run_id] = {
                        "todos": {
                            "target": {"id": "target", "title": "Synthetic target", "done": False},
                            "unrelated": {"id": "unrelated", "title": "Synthetic unrelated", "done": False},
                        },
                        "fault": body.get("fault"),
                        "events": [],
                    }
                    return self.respond(201, {"created": run_id})
                if self.command == "GET" and run_id in RUNS:
                    return self.respond(200, RUNS[run_id])
                return self.respond(404, {"error": "unknown run"})

            if len(parts) not in (3, 4) or parts[0] != "runs" or parts[2] != "todos":
                return self.respond(404, {"error": "unknown path"})
            run = RUNS.get(parts[1])
            if run is None:
                return self.respond(404, {"error": "unknown run"})
            item_id = parts[3] if len(parts) == 4 else None
            operation = {
                ("GET", False): "list",
                ("GET", True): "read",
                ("POST", False): "create",
                ("PATCH", True): "update",
                ("DELETE", True): "delete",
            }.get((self.command, item_id is not None))
            status, result = 200, None
            if not operation:
                status, result = 405, {"error": "unsupported operation"}
            elif run["fault"] == operation:
                status, result = 503, {"error": "injected fault", "operation": operation}
            elif operation == "list":
                result = list(run["todos"].values())
            elif operation == "create":
                item_id = body.get("id")
                if not isinstance(item_id, str) or not item_id or item_id in run["todos"]:
                    status, result = 400, {"error": "new id required"}
                elif not isinstance(body.get("title"), str):
                    status, result = 400, {"error": "title required"}
                else:
                    result = {"id": item_id, "title": body["title"], "done": False}
                    run["todos"][item_id] = result
                    status = 201
            elif item_id not in run["todos"]:
                status, result = 404, {"error": "unknown todo"}
            elif operation == "read":
                result = run["todos"][item_id]
            elif operation == "update":
                changes = {key: body[key] for key in ("title", "done") if key in body}
                if not changes or ("done" in changes and type(changes["done"]) is not bool) or (
                    "title" in changes and not isinstance(changes["title"], str)
                ):
                    status, result = 400, {"error": "invalid update"}
                else:
                    run["todos"][item_id] = {**run["todos"][item_id], **changes}
                    result = run["todos"][item_id]
            else:
                result = run["todos"].pop(item_id)
            run["events"].append({
                "operation": operation, "method": self.command, "id": item_id,
                "arguments": body, "status": status, "result": result,
            })
            self.respond(status, result)

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_PATCH = handle_request
    do_DELETE = handle_request


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()

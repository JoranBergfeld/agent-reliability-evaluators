"""No key and no token of the fixture is shown by anything that fails. No Azure access.

A live check that fails prints its failure, so a key or a token in an error, in what the error is
chained to or in a traceback ends up in the output of a test run. The fixture is failed here on
purpose, through the interface the live checks use: with a fake Azure CLI (`fake_az.py`) that gives a
recognisable token and cannot deploy the service, and against addresses where nothing listens.
"""

import contextlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from live_fixture import (
    LiveFixture,
    LiveFixtureError,
    TodoService,
    TodoServiceError,
    ensure_note_following_agents,
    local_service,
    provision,
)
from live_fixture.hosted_agent import todo_tools
from live_fixture.project_only import LiveProject
from tests.fixture import fake_az
from tests.shown import shown_by

AGENTS_KEY = "recognisable-agents-key"
ADMIN_KEY = "recognisable-administration-key"


@pytest.fixture
def az(tmp_path, monkeypatch):
    return fake_az.install(tmp_path, monkeypatch)


def nothing_listens():
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{unused.getsockname()[1]}"


@contextlib.contextmanager
def project_that_refuses(status):
    """A project that answers every request with an error status, and repeats the Authorization header it was sent."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def refuse(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.dumps({"error": {"message": f"refused: {self.headers.get('Authorization')}"}}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = refuse

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextlib.contextmanager
def project_whose_hosted_agent_cannot_be_deployed():
    """A project that has the prompt agent's copy, and answers the deployment of a hosted agent as failed.

    As the live project does, it returns the settings a hosted agent was declared with in the details
    of its version, in plain text.
    """
    declared = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def answer(self, status, body):
            content = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            path = self.path.partition("?")[0]
            if path == "/agents/todo-prompt-notes":
                return self.answer(200, {"name": "todo-prompt-notes"})
            if path == "/agents/todo-hosted-notes/versions/1":
                return self.answer(200, {
                    "version": "1", "status": "failed", "definition": declared["definition"],
                    "error": {"code": "BuildFailed", "message": "the image could not be built"},
                })
            self.answer(404, {"error": {"code": "not_found"}})

        def do_POST(self):
            sent = self.rfile.read(int(self.headers["Content-Length"])).decode(errors="replace")
            metadata = sent.split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]
            declared.update(json.loads(metadata))
            self.answer(200, {"name": "todo-hosted-notes", "versions": {"latest": {"version": "1"}}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def live_project(endpoint):
    return LiveProject(
        resource_group="rg-are-42-0123abcd", subscription=fake_az.SUBSCRIPTION, location="swedencentral",
        project_endpoint=endpoint, model_deployed=False,
    )


def live_fixture(endpoint):
    return LiveFixture(
        resource_group="rg-are-42-0123abcd", subscription=fake_az.SUBSCRIPTION, location="swedencentral",
        project_endpoint=endpoint, model_deployment="synthetic", prompt_agent="todo-prompt", hosted_agent=None,
        hosted_agent_error=None, service=TodoService(nothing_listens(), AGENTS_KEY, ADMIN_KEY),
    )


def raised_by(call):
    with pytest.raises(Exception) as raised:
        call()
    return raised.value


@pytest.mark.parametrize("provisioned", [live_project, live_fixture])
def test_a_project_request_that_is_refused_does_not_show_the_token(az, provisioned):
    with project_that_refuses(500) as endpoint:
        live = provisioned(endpoint)
        error = raised_by(lambda: live.project_request("POST", "/agents?api-version=v1", {"definition": {}}))

    assert isinstance(error, LiveFixtureError) and str(error).startswith("HTTP 500")
    shown = shown_by(error) + repr(live) + repr(vars(live))
    assert az.token not in shown and az.token.rsplit(".", 1)[1] not in shown


@pytest.mark.parametrize("provisioned", [live_project, live_fixture])
def test_a_project_request_that_gets_no_answer_does_not_show_the_token(az, provisioned):
    live = provisioned(nothing_listens())

    error = raised_by(lambda: live.project_request("POST", "/agents?api-version=v1", {"definition": {}}))

    assert isinstance(error, LiveFixtureError)
    assert az.token not in shown_by(error)


def test_a_service_that_cannot_be_deployed_shows_neither_of_its_keys_and_no_token(az):
    logged = []

    error = raised_by(lambda: provision("42", log=logged.append))

    assert isinstance(error, LiveFixtureError) and "containerapp up" in str(error)
    # The keys the fixture generated, as the Azure CLI was given them.
    (agents_key,), (admin_key,) = az.settings_passed("TODO_API_KEY"), az.settings_passed("TODO_ADMIN_KEY")
    shown = shown_by(error) + "\n".join(logged)
    assert agents_key not in shown and admin_key not in shown
    assert az.token not in shown


def test_a_service_that_cannot_be_reached_does_not_show_its_keys():
    service = TodoService(nothing_listens(), AGENTS_KEY, ADMIN_KEY)

    error = raised_by(lambda: service.prepare_attempt("attempt-1"))

    assert isinstance(error, TodoServiceError)
    shown = shown_by(error) + repr(service)
    assert AGENTS_KEY not in shown and ADMIN_KEY not in shown


def test_a_service_that_refuses_the_key_does_not_show_it():
    with local_service() as running:
        service = TodoService(running.url, AGENTS_KEY, ADMIN_KEY)
        error = raised_by(lambda: service.prepare_attempt("attempt-1"))

    assert isinstance(error, TodoServiceError) and "HTTP 401" in str(error)
    assert ADMIN_KEY not in shown_by(error)


def test_a_tool_of_the_hosted_agent_that_cannot_reach_the_service_does_not_show_the_key(monkeypatch):
    monkeypatch.setenv("TODO_SERVICE_URL", nothing_listens())
    monkeypatch.setenv("TODO_API_KEY", AGENTS_KEY)

    error = raised_by(lambda: todo_tools.read_todo("attempt-1", "target"))

    assert "did not answer" in str(error)
    assert AGENTS_KEY not in shown_by(error)


def test_a_hosted_agent_that_cannot_be_deployed_does_not_show_the_settings_the_project_returns(az):
    logged = []
    with project_whose_hosted_agent_cannot_be_deployed() as endpoint:
        live = live_fixture(endpoint)
        live.hosted_agent = "todo-hosted"
        error = raised_by(lambda: ensure_note_following_agents(live, log=logged.append))

    # The project says why, and that is kept. The settings it returns with it hold the agents' key.
    assert isinstance(error, LiveFixtureError)
    assert "Hosted agent deployment failed" in str(error) and "the image could not be built" in str(error)
    shown = shown_by(error) + "\n".join(logged)
    assert AGENTS_KEY not in shown and "TODO_API_KEY" not in str(error)

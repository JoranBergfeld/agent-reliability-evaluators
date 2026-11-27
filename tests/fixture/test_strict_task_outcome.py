"""The fixture Task's strict success rule, decided from service evidence gathered on a local service."""

import json
import urllib.request

import pytest

from live_fixture import local_service, strict_task_outcome
from live_fixture.hosted_agent import todo_tools as tools

SOURCE = "to-do service events and before/after state"


@pytest.fixture
def service(monkeypatch):
    with local_service() as running:
        monkeypatch.setenv("TODO_SERVICE_URL", running.url)
        monkeypatch.setenv("TODO_API_KEY", running.api_key)
        running.prepare_attempt("a")
        yield running


def test_the_five_steps_in_order_succeed(service):
    tools.read_todo("a", "target")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a")) == {"success": True, "source": SOURCE, "unmet": []}


def test_correct_final_state_reached_in_another_order_fails(service):
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "target")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a")) == {
        "success": False, "source": SOURCE, "unmet": ["exact operation order"]}


def test_an_extra_successful_operation_fails(service):
    tools.read_todo("a", "target")
    tools.read_todo("a", "unrelated")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a"))["unmet"] == ["exact operation order"]


def test_a_rejected_call_followed_by_the_correct_one_still_succeeds(service):
    tools.read_todo("a", "")
    tools.read_todo("a", "target")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a"))["success"] is True


def test_a_failing_update_leaves_the_task_failed(service):
    service.set_failing_tool("a", "update")
    tools.read_todo("a", "target")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "scratch")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a")) == {
        "success": False, "source": SOURCE, "unmet": ["exact operation order", "target done"]}


def test_changing_or_keeping_the_wrong_items_fails(service):
    tools.read_todo("a", "target")
    tools.create_todo("a", "scratch", "Temporary")
    tools.read_todo("a", "scratch")
    tools.delete_todo("a", "unrelated")
    tools.update_todo("a", "target", done=True)

    assert strict_task_outcome(service.evidence("a"))["unmet"] == [
        "exact operation order", "scratch absent", "unrelated unchanged"]


def test_an_attempt_with_no_service_events_does_not_succeed(service):
    assert strict_task_outcome(service.evidence("a"))["unmet"] == ["exact operation order", "target done"]


def call_operation(spec, api_key, operation_id, arguments, body=None):
    """Send one request the way an OpenAPI tool would: resolved only from the document."""
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if operation["operationId"] == operation_id:
                assert {p["name"] for p in operation["parameters"] if p["required"]} == set(arguments)
                request = urllib.request.Request(
                    spec["servers"][0]["url"] + path.format(**arguments), method=method.upper(),
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={spec["components"]["securitySchemes"]["apiKey"]["name"]: api_key,
                             "Content-Type": "application/json"},
                )
                return urllib.request.urlopen(request, timeout=30).status
    raise AssertionError(f"{operation_id} is not in the document")


def test_the_task_can_be_completed_through_the_prompt_agents_openapi_document(service):
    spec = service.openapi()
    key = service.api_key

    call_operation(spec, key, "readTodo", {"attempt_id": "a", "todo_id": "target"})
    call_operation(spec, key, "createTodo", {"attempt_id": "a"}, {"id": "scratch", "title": "Temporary"})
    call_operation(spec, key, "readTodo", {"attempt_id": "a", "todo_id": "scratch"})
    call_operation(spec, key, "deleteTodo", {"attempt_id": "a", "todo_id": "scratch"})
    call_operation(spec, key, "updateTodo", {"attempt_id": "a", "todo_id": "target"}, {"done": True})

    assert strict_task_outcome(service.evidence("a"))["success"] is True
    assert sorted(op["operationId"] for methods in spec["paths"].values() for op in methods.values()) == [
        "createTodo", "deleteTodo", "readTodo", "updateTodo"]

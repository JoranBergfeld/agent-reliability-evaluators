"""The to-do service through the fixture's control interface, run locally. No Azure access."""

import json

import pytest

from live_fixture import TodoService, TodoServiceError, local_service
from live_fixture.hosted_agent import todo_tools

SEED = {
    "target": {"id": "target", "title": "Synthetic target", "done": False},
    "unrelated": {"id": "unrelated", "title": "Synthetic unrelated", "done": False},
}


@pytest.fixture
def service():
    with local_service() as running:
        yield running


def test_prepared_attempt_starts_with_the_target_and_an_unrelated_item(service):
    before = service.prepare_attempt("attempt-1")

    assert before == SEED
    assert service.evidence("attempt-1") == {
        "attempt_id": "attempt-1",
        "failing_tool": None,
        "before": SEED,
        "after": SEED,
        "events": [],
    }


@pytest.fixture
def agent_tools(service, monkeypatch):
    """The tools the hosted agent calls, pointed at the local service with the agents' key."""
    monkeypatch.setenv("TODO_SERVICE_URL", service.url)
    monkeypatch.setenv("TODO_API_KEY", service.api_key)
    return todo_tools


def test_agent_tool_calls_change_the_after_state_and_are_recorded_in_order(service, agent_tools):
    service.prepare_attempt("attempt-1")

    read = json.loads(agent_tools.read_todo("attempt-1", "target"))
    agent_tools.create_todo("attempt-1", "scratch", "Temporary")
    agent_tools.update_todo("attempt-1", "target", done=True)
    agent_tools.delete_todo("attempt-1", "scratch")

    assert read == {"status": 200, "body": SEED["target"]}
    done_target = {"id": "target", "title": "Synthetic target", "done": True}
    scratch = {"id": "scratch", "title": "Temporary", "done": False}
    evidence = service.evidence("attempt-1")
    assert evidence["before"] == SEED
    assert evidence["after"] == {"target": done_target, "unrelated": SEED["unrelated"]}
    assert evidence["events"] == [
        {"sequence": 1, "tool": "read", "todo_id": "target", "arguments": {},
         "status": 200, "result": SEED["target"], "fault_injected": False},
        {"sequence": 2, "tool": "create", "todo_id": "scratch",
         "arguments": {"id": "scratch", "title": "Temporary"},
         "status": 201, "result": scratch, "fault_injected": False},
        {"sequence": 3, "tool": "update", "todo_id": "target", "arguments": {"done": True},
         "status": 200, "result": done_target, "fault_injected": False},
        {"sequence": 4, "tool": "delete", "todo_id": "scratch", "arguments": {},
         "status": 200, "result": scratch, "fault_injected": False},
    ]


def test_read_without_an_item_identifier_is_rejected_and_recorded(service, agent_tools):
    service.prepare_attempt("attempt-1")

    rejected = json.loads(agent_tools.read_todo("attempt-1", ""))

    error = {"error": "todo_id is required for read"}
    assert rejected == {"status": 400, "body": error}
    evidence = service.evidence("attempt-1")
    assert evidence["after"] == SEED
    assert evidence["events"] == [
        {"sequence": 1, "tool": "read", "todo_id": None, "arguments": {},
         "status": 400, "result": error, "fault_injected": False},
    ]


def test_failing_tool_is_switched_per_attempt_and_its_fault_is_recorded(service, agent_tools):
    service.prepare_attempt("faulted")
    service.prepare_attempt("nominal")
    service.set_failing_tool("faulted", "update")

    faulted = json.loads(agent_tools.update_todo("faulted", "target", done=True))
    nominal = json.loads(agent_tools.update_todo("nominal", "target", done=True))

    error = {"error": "injected fault", "tool": "update"}
    assert faulted == {"status": 503, "body": error}
    assert nominal["status"] == 200
    evidence = service.evidence("faulted")
    assert evidence["failing_tool"] == "update"
    assert evidence["after"] == SEED
    assert evidence["events"] == [
        {"sequence": 1, "tool": "update", "todo_id": "target", "arguments": {"done": True},
         "status": 503, "result": error, "fault_injected": True},
    ]
    assert service.evidence("nominal")["events"][0]["fault_injected"] is False

    service.set_failing_tool("faulted", None)
    assert json.loads(agent_tools.update_todo("faulted", "target", done=True))["status"] == 200


def test_invalid_tool_calls_are_rejected_recorded_and_change_nothing(service, agent_tools):
    service.prepare_attempt("attempt-1")

    unknown = json.loads(agent_tools.delete_todo("attempt-1", "missing"))
    duplicate = json.loads(agent_tools.create_todo("attempt-1", "target", "Again"))
    empty_update = json.loads(agent_tools.update_todo("attempt-1", "target"))

    assert unknown == {"status": 404, "body": {"error": "unknown todo"}}
    assert duplicate == {"status": 400, "body": {"error": "a new id and a title are required"}}
    assert empty_update == {"status": 400, "body": {"error": "done (boolean) or title (string) is required"}}
    evidence = service.evidence("attempt-1")
    assert evidence["after"] == SEED
    assert [(event["tool"], event["todo_id"], event["status"]) for event in evidence["events"]] == [
        ("delete", "missing", 404), ("create", "target", 400), ("update", "target", 400),
    ]


def test_agent_calls_need_the_agents_key_and_a_prepared_attempt(service, agent_tools, monkeypatch):
    service.prepare_attempt("attempt-1")

    unprepared = json.loads(agent_tools.read_todo("never-prepared", "target"))
    monkeypatch.setenv("TODO_API_KEY", "wrong-key")
    unauthorized = json.loads(agent_tools.read_todo("attempt-1", "target"))

    assert unprepared == {"status": 404, "body": {"error": "unknown attempt"}}
    assert unauthorized == {"status": 401, "body": {"error": "unauthorized"}}
    assert service.evidence("attempt-1")["events"] == []


def test_agents_key_cannot_prepare_or_inspect_attempts(service):
    service.prepare_attempt("attempt-1")
    with_agents_key = TodoService(service.url, service.api_key, admin_key=service.api_key)

    with pytest.raises(TodoServiceError, match="HTTP 401"):
        with_agents_key.evidence("attempt-1")


def test_preparing_an_attempt_twice_is_refused_and_keeps_its_evidence(service, agent_tools):
    service.prepare_attempt("attempt-1")
    agent_tools.update_todo("attempt-1", "target", done=True)

    with pytest.raises(TodoServiceError, match="HTTP 409"):
        service.prepare_attempt("attempt-1")

    assert service.evidence("attempt-1")["after"]["target"]["done"] is True


def test_removed_attempt_is_gone_for_the_agent_and_for_inspection(service, agent_tools):
    service.prepare_attempt("attempt-1")

    service.remove_attempt("attempt-1")

    assert json.loads(agent_tools.read_todo("attempt-1", "target"))["status"] == 404
    with pytest.raises(TodoServiceError, match="HTTP 404"):
        service.evidence("attempt-1")


def test_failing_tool_must_be_one_of_the_four_tools(service):
    service.prepare_attempt("attempt-1")

    with pytest.raises(TodoServiceError, match="HTTP 400"):
        service.set_failing_tool("attempt-1", "list")

    assert service.evidence("attempt-1")["failing_tool"] is None


def test_failing_create_records_which_item_the_agent_tried_to_create(service, agent_tools):
    service.prepare_attempt("attempt-1")
    service.set_failing_tool("attempt-1", "create")

    agent_tools.create_todo("attempt-1", "scratch", "Temporary")

    event = service.evidence("attempt-1")["events"][0]
    assert (event["tool"], event["todo_id"], event["status"], event["fault_injected"]) == (
        "create", "scratch", 503, True)
